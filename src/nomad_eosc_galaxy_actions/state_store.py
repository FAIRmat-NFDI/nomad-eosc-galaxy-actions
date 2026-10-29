# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""What earlier harvest runs of one user have already handled.

One JSON file per user (`users/<user_id>/state.json`) with three maps, all
keyed by Zenodo's concept record ID, which groups every version of a record:

- `files`: raw files by SHA-256, with their status and the archive they came
  from. Never process the same content twice.
- `loose_md5`: Zenodo MD5 of loose raw files. Skip them before download.
- `archives`: archives by Zenodo MD5. `resolved` means every raw file inside
  has a final status, so the archive is not downloaded again.

Writes run in `transaction()`: the file is locked, re-read, changed and saved
atomically (temporary file, then rename), so a crash never leaves half a
file. When the file grows too large for JSON, only this module changes.

Framework-agnostic: no NOMAD or Temporal imports.
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Version of the file layout (keys and their meaning). When it changes: bump
# it and add the function that turns version n into n + 1 to UPGRADES[n].
STATE_SCHEMA_VERSION = 1
Upgrade = Callable[[dict[str, Any]], dict[str, Any]]
UPGRADES: dict[int, Upgrade] = {}

# Statuses that mean "done, do not process again". `retry` is not final.
FINAL_STATUSES = ("success", "failed")


class StateSchemaError(RuntimeError):
    """The state file has a newer or unknown schema version."""


class NoTransactionError(RuntimeError):
    """A write was attempted outside `StateStore.transaction()`."""


class RunLockedError(RuntimeError):
    """Another harvest run of the same user is still active."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty(version: int) -> dict[str, Any]:
    return {"schema_version": version, "files": {}, "loose_md5": {}, "archives": {}}


@contextmanager
def locked(path: Path) -> Iterator[None]:
    """Hold an exclusive lock on `<path>.lock`; other processes wait.

    https://docs.python.org/3/library/fcntl.html#fcntl.flock
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def write_atomically(path: Path, content: dict[str, Any]) -> None:
    """Write JSON to a temporary file next to `path`, then rename it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False
    ) as tmp:
        try:
            json.dump(content, tmp, indent=1, sort_keys=True)
        except BaseException:
            Path(tmp.name).unlink()
            raise
    os.replace(tmp.name, path)  # atomic on the same file system


class StateStore:
    """Read and update the harvest state of one user in one JSON file.

    Use as a context manager. Writes must run inside `transaction()`, so a
    merge is saved completely or not at all:

        with StateStore(path) as store, store.transaction():
            store.set_archive_resolved("5907155", "c72c7234...", True)
    """

    def __init__(
        self,
        path: str | Path,
        *,
        schema_version: int = STATE_SCHEMA_VERSION,
        upgrades: dict[int, Upgrade] | None = None,
    ):
        """Read the state file; a missing file is an empty state.

        Older versions are upgraded in memory; the file changes only with the
        next transaction. `schema_version` and `upgrades` default to the
        current layout; tests pass their own to check the upgrade path.
        """
        self._path = Path(path)
        self._target_version = schema_version
        self._upgrades = UPGRADES if upgrades is None else upgrades
        self._in_transaction = False
        self._state = self._load()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Nothing to release; kept so the store is used like a connection."""

    @property
    def schema_version(self) -> int:
        return self._state["schema_version"]

    def snapshot(self) -> dict[str, Any]:
        """A copy of the whole state, for reports and tests."""
        return copy.deepcopy(self._state)

    def _load(self) -> dict[str, Any]:
        """The file's content, upgraded to the target version."""
        target = self._target_version
        if not self._path.is_file():
            return _empty(target)
        state = json.loads(self._path.read_text(encoding="utf-8"))
        version = state.get("schema_version")
        if type(version) is not int or version < 1:
            raise StateSchemaError(f"{self._path}: bad schema_version {version!r}")
        if version > target:
            raise StateSchemaError(
                f"state schema version {version} is newer than supported "
                f"({target}); update nomad-eosc-galaxy-actions"
            )
        while version < target:
            if version not in self._upgrades:
                raise StateSchemaError(f"no upgrade from state schema {version}")
            state = self._upgrades[version](state)
            version += 1
            state["schema_version"] = version
        return state

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group writes: all of them are saved, or none (on an exception).

        Holds the file lock and starts from the file's latest content, so the
        changes of a parallel writer are not lost.
        """
        if self._in_transaction:
            raise NoTransactionError("transactions cannot be nested")
        self._in_transaction = True
        try:
            with locked(self._path):
                before = self._load()
                self._state = copy.deepcopy(before)
                try:
                    yield
                except BaseException:
                    self._state = before
                    raise
                write_atomically(self._path, self._state)
        finally:
            self._in_transaction = False

    def _map(self, name: str, concept_record_id: str) -> dict[str, Any]:
        """One record's part of a map, for writing."""
        if not self._in_transaction:
            raise NoTransactionError("writes must run inside transaction()")
        return self._state[name].setdefault(concept_record_id, {})

    # Reads used to build the work list

    def known_sha256(self, concept_record_id: str) -> list[str]:
        """SHA-256 of the record's raw files that already have a final status."""
        files = self._state["files"].get(concept_record_id, {})
        return sorted(
            sha256 for sha256, file in files.items() if file["status"] in FINAL_STATUSES
        )

    def is_loose_md5_known(self, concept_record_id: str, md5: str) -> bool:
        return md5 in self._state["loose_md5"].get(concept_record_id, {})

    def is_archive_resolved(self, concept_record_id: str, md5: str) -> bool:
        archive = self._state["archives"].get(concept_record_id, {}).get(md5)
        return bool(archive and archive["resolved"])

    # Writes, used when a manifest is merged

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
        archive_md5: str | None = None,
    ) -> None:
        """Insert or replace the state of one raw file.

        `version_doi` keeps the first version the file was seen in.
        `archive_md5` is the archive it came from (None for a loose file).
        """
        files = self._map("files", concept_record_id)
        first_doi = files.get(sha256, {}).get("version_doi")
        files[sha256] = {
            "status": status,
            "path": path,
            "version_doi": first_doi or version_doi,
            "tool_version": tool_version,
            "upload_id": upload_id,
            "error": error,
            "updated_at": _now(),
            "archive_md5": archive_md5,
        }

    def record_loose_md5(self, concept_record_id: str, md5: str, sha256: str) -> None:
        self._map("loose_md5", concept_record_id)[md5] = sha256

    def set_archive_resolved(
        self, concept_record_id: str, md5: str, resolved: bool
    ) -> None:
        archives = self._map("archives", concept_record_id)
        archives[md5] = {"resolved": resolved, "updated_at": _now()}

    def retry_failed(self, tool_version: str) -> int:
        """Set failed files from another tool version to `retry`.

        Their archives become unresolved and their loose MD5s are forgotten,
        so the next work list downloads them again. Returns the file count.
        """
        if not self._in_transaction:
            raise NoTransactionError("writes must run inside transaction()")
        count = 0
        for cid, files in self._state["files"].items():
            for sha256, file in files.items():
                if file["status"] != "failed" or file["tool_version"] == tool_version:
                    continue
                file |= {"status": "retry", "updated_at": _now()}
                count += 1
                archive = self._state["archives"].get(cid, {}).get(file["archive_md5"])
                if archive:
                    archive |= {"resolved": False, "updated_at": _now()}
                loose = self._state["loose_md5"].get(cid, {})
                for md5 in [md5 for md5, known in loose.items() if known == sha256]:
                    del loose[md5]
        return count


# One run per user at a time: `active_run.json` in the user's folder names it.
ACTIVE_RUN_FILE = "active_run.json"


def acquire_run(
    user_dir: str | Path, run_id: str, *, now: datetime, max_age: timedelta
) -> None:
    """Mark `run_id` as the user's active run; calling it again is fine.

    A mark older than `max_age` is from a run that ended without releasing
    it (e.g. a terminated Action) and is taken over.

    Raises:
        RunLockedError: another run of the user is active.
    """
    path = Path(user_dir) / ACTIVE_RUN_FILE
    with locked(path):
        if path.is_file():
            active = json.loads(path.read_text(encoding="utf-8"))
            started = datetime.fromisoformat(active["started_at"])
            if active["run_id"] != run_id and now - started < max_age:
                raise RunLockedError(
                    f"another harvest run of this user is active: "
                    f"{active['run_id']}, started {active['started_at']}"
                )
        write_atomically(path, {"run_id": run_id, "started_at": now.isoformat()})


def release_run(user_dir: str | Path, run_id: str) -> bool:
    """Remove the mark of `run_id`; another run's mark is kept.

    Returns whether a mark was removed.
    """
    path = Path(user_dir) / ACTIVE_RUN_FILE
    with locked(path):
        if not path.is_file():
            return False
        if json.loads(path.read_text(encoding="utf-8"))["run_id"] != run_id:
            return False
        path.unlink()
        return True
