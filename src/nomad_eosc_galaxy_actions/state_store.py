# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""What earlier harvest runs have already handled.

A single SQLite file (https://docs.python.org/3/library/sqlite3.html) with
three tables, all keyed by Zenodo's concept record ID, which groups every
version of a record:

- `files`: raw files by SHA-256, with their final status. Never process the
  same content twice.
- `loose_md5`: Zenodo MD5 of loose raw files. Skip them before download.
- `archives`: archives by Zenodo MD5. `resolved` means every raw file inside
  has a final status, so the archive is not downloaded again.

Framework-agnostic: no NOMAD or Temporal imports.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Version of the table layout, stored in the file as `PRAGMA user_version`
# (https://www.sqlite.org/pragma.html#pragma_user_version). When the layout
# changes: bump it and add the SQL that upgrades version n to UPGRADES[n].
STATE_SCHEMA_VERSION = 1
UPGRADES: dict[int, str] = {}

# Statuses that mean "done, do not process again".
FINAL_STATUSES = ("success", "failed")

_SCHEMA = """
CREATE TABLE files (
    concept_record_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    status TEXT NOT NULL,
    path TEXT,
    version_doi TEXT,
    tool_version TEXT,
    upload_id TEXT,
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (concept_record_id, sha256)
);
CREATE TABLE loose_md5 (
    concept_record_id TEXT NOT NULL,
    md5 TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (concept_record_id, md5)
);
CREATE TABLE archives (
    concept_record_id TEXT NOT NULL,
    md5 TEXT NOT NULL,
    resolved INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (concept_record_id, md5)
);
"""


class StateSchemaError(RuntimeError):
    """The state file has a newer or unknown schema version."""


class NoTransactionError(RuntimeError):
    """A write was attempted outside `StateStore.transaction()`."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StateStore:
    """Read and update the harvest state in one SQLite file.

    Use as a context manager, so the connection is always closed. Writes
    must run inside `transaction()`, so a merge is saved completely or not
    at all:

        with StateStore(path) as store, store.transaction():
            store.set_archive_resolved("5907155", "c72c7234...", True)
    """

    def __init__(
        self,
        path: str | Path,
        *,
        schema_version: int = STATE_SCHEMA_VERSION,
        upgrades: dict[int, str] | None = None,
    ):
        """Open (or create) the state file and upgrade it to `schema_version`.

        `schema_version` and `upgrades` default to the current layout; tests
        pass their own to check the upgrade path.
        """
        self._target_version = schema_version
        self._upgrades = UPGRADES if upgrades is None else upgrades
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path)
        self._in_transaction = False
        try:
            self._migrate()
        except Exception:
            self._db.close()
            raise

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self._db.close()

    @property
    def schema_version(self) -> int:
        return self._db.execute("PRAGMA user_version").fetchone()[0]

    def _migrate(self) -> None:
        """Create the tables in a new file, or upgrade an older file."""
        version, target = self.schema_version, self._target_version
        if version > target:
            raise StateSchemaError(
                f"state schema version {version} is newer than supported "
                f"({target}); update nomad-eosc-galaxy-actions"
            )
        if version == 0:  # new, empty file
            self._apply(_SCHEMA, target)
            return
        while version < target:
            if version not in self._upgrades:
                raise StateSchemaError(f"no upgrade from state schema {version}")
            self._apply(self._upgrades[version], version + 1)
            version += 1

    def _apply(self, script: str, version: int) -> None:
        # executescript() commits first, so the script and the new version
        # are wrapped in one explicit transaction.
        self._db.executescript(
            f"BEGIN; {script}; PRAGMA user_version = {int(version)}; COMMIT;"
        )

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group writes: all of them are saved, or none (on an exception)."""
        if self._in_transaction:
            raise NoTransactionError("transactions cannot be nested")
        self._in_transaction = True
        try:
            with self._db:  # commits on success, rolls back on an exception
                yield
        finally:
            self._in_transaction = False

    def _write(self, sql: str, params: tuple) -> None:
        if not self._in_transaction:
            raise NoTransactionError("writes must run inside transaction()")
        self._db.execute(sql, params)

    # Reads used to build the work list

    def known_sha256(self, concept_record_id: str) -> list[str]:
        """SHA-256 of the record's raw files that already have a final status."""
        rows = self._db.execute(
            "SELECT sha256 FROM files WHERE concept_record_id = ? "
            f"AND status IN ({', '.join('?' * len(FINAL_STATUSES))}) "
            "ORDER BY sha256",
            (concept_record_id, *FINAL_STATUSES),
        )
        return [sha256 for (sha256,) in rows]

    def is_loose_md5_known(self, concept_record_id: str, md5: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM loose_md5 WHERE concept_record_id = ? AND md5 = ?",
            (concept_record_id, md5),
        ).fetchone()
        return row is not None

    def is_archive_resolved(self, concept_record_id: str, md5: str) -> bool:
        row = self._db.execute(
            "SELECT resolved FROM archives WHERE concept_record_id = ? AND md5 = ?",
            (concept_record_id, md5),
        ).fetchone()
        return bool(row and row[0])

    # Writes, used when a manifest is merged (F5)

    def record_file(  # noqa: PLR0913
        self,
        concept_record_id: str,
        sha256: str,
        *,
        status: str,
        path: str | None = None,
        version_doi: str | None = None,
        tool_version: str | None = None,
        upload_id: str | None = None,
        error: str | None = None,
    ) -> None:
        """Insert or replace the state of one raw file.

        `version_doi` keeps the first version the file was seen in.
        """
        self._write(
            """
                INSERT INTO files (concept_record_id, sha256, status, path,
                    version_doi, tool_version, upload_id, error, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (concept_record_id, sha256) DO UPDATE SET
                    status = excluded.status, path = excluded.path,
                    version_doi = COALESCE(files.version_doi, excluded.version_doi),
                    tool_version = excluded.tool_version,
                    upload_id = excluded.upload_id, error = excluded.error,
                    updated_at = excluded.updated_at
            """,
            (
                concept_record_id,
                sha256,
                status,
                path,
                version_doi,
                tool_version,
                upload_id,
                error,
                _now(),
            ),
        )

    def record_loose_md5(self, concept_record_id: str, md5: str, sha256: str) -> None:
        self._write(
            "INSERT OR REPLACE INTO loose_md5 VALUES (?, ?, ?)",
            (concept_record_id, md5, sha256),
        )

    def set_archive_resolved(
        self, concept_record_id: str, md5: str, resolved: bool
    ) -> None:
        self._write(
            "INSERT OR REPLACE INTO archives VALUES (?, ?, ?, ?)",
            (concept_record_id, md5, int(resolved), _now()),
        )
