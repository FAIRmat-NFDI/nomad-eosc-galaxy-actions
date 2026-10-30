# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Raw files that are already in a NOMAD project, for all users.

One JSON file next to the per-user states: the SHA-256 of each converted raw
file → the projects it is in. A raw file counts as a duplicate only in a
project the user can open; today all projects are private, so these are the
user's own (published and shared projects can be added in `_visible`).

Framework-agnostic: no NOMAD or Temporal imports.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from nomad_eosc_galaxy_actions.manifest import Manifest, Results
from nomad_eosc_galaxy_actions.state_store import locked, write_atomically

# Version of the file layout (same rule as STATE_SCHEMA_VERSION).
CHECKSUM_INDEX_SCHEMA_VERSION = 1


class ChecksumIndexError(RuntimeError):
    """The index file has a newer or unknown schema version."""


class IndexEntry(BaseModel):
    """One project that contains a raw file."""

    sha256: str
    user_id: str
    upload_id: str
    record_id: str
    path: str


class Duplicate(BaseModel):
    """A raw file Galaxy skipped because it is already in a project."""

    record_id: str
    key: str
    sha256: str
    upload_id: str
    reason: str = "duplicate"


def _visible(entry: dict[str, Any], user_id: str) -> bool:
    """Whether the user can open the entry's project (own projects today)."""
    return entry["user_id"] == user_id


class ChecksumIndex:
    """Read and extend the checksum index file."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._files = self._load()

    def _load(self) -> dict[str, list[dict[str, Any]]]:
        if not self._path.is_file():
            return {}
        content = json.loads(self._path.read_text(encoding="utf-8"))
        version = content.get("schema_version")
        if version != CHECKSUM_INDEX_SCHEMA_VERSION:
            raise ChecksumIndexError(
                f"{self._path}: unsupported schema_version {version!r} "
                f"(supported: {CHECKSUM_INDEX_SCHEMA_VERSION})"
            )
        return content["files"]

    def projects(self, sha256: str, user_id: str) -> list[str]:
        """Upload IDs of the projects with this raw file that the user can open."""
        return [
            entry["upload_id"]
            for entry in self._files.get(sha256, [])
            if _visible(entry, user_id)
        ]

    def known_sha256(self, user_id: str) -> list[str]:
        """All raw files in projects the user can open."""
        return sorted(
            sha256 for sha256 in self._files if self.projects(sha256, user_id)
        )

    def add(self, entries: Iterable[IndexEntry]) -> int:
        """Add entries under the file lock; known ones are kept once.

        Returns the number of new entries.
        """
        added = 0
        with locked(self._path):
            self._files = self._load()  # another user's run may have written
            for entry in entries:
                known = self._files.setdefault(entry.sha256, [])
                value = entry.model_dump(exclude={"sha256"})
                if value not in known:
                    known.append(value)
                    added += 1
            content = {
                "schema_version": CHECKSUM_INDEX_SCHEMA_VERSION,
                "files": self._files,
            }
            write_atomically(self._path, content)
        return added


def converted_files(
    manifest: Manifest,
    results: Results,
    upload_ids: dict[str, str],
    user_id: str,
) -> list[IndexEntry]:
    """Index entries of the raw files converted into a project in this run."""
    converted = {r.sha256 for r in results.files if r.status == "success"}
    return [
        IndexEntry(
            sha256=file.sha256,
            user_id=user_id,
            upload_id=upload_ids[file.record_id],
            record_id=file.record_id,
            path=file.path,
        )
        for file in manifest.files
        if file.role == "raw"
        and file.action == "process"
        and file.sha256 in converted
        and file.record_id in upload_ids
    ]


def find_duplicates(
    manifest: dict[str, Any], index: ChecksumIndex, user_id: str
) -> list[Duplicate]:
    """Raw files Galaxy skipped as known that are in a project of the user.

    Reads the manifest as plain JSON: it is checked in full only when merged.
    """
    duplicates = []
    for file in manifest.get("files", []):
        if file.get("role") != "raw" or file.get("action") != "already_known":
            continue
        projects = index.projects(file.get("sha256", ""), user_id)
        if projects:
            duplicates.append(
                Duplicate(
                    record_id=file["record_id"],
                    key=file["path"],
                    sha256=file["sha256"],
                    upload_id=projects[0],
                )
            )
    return duplicates
