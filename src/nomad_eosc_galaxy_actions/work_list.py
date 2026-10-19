# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Build the work list: what Galaxy has to download and process in this run.

Applies the decision table of the project plan to each candidate record:

- an archive is downloaded unless the state marks it `resolved`;
- an archive larger than `max_archive_bytes` is skipped (`too_large`);
- a loose raw file is downloaded unless its Zenodo MD5 is already known;
- a record with nothing to download is left out;
- loose context files always come along with a record that is listed.

Framework-agnostic: no NOMAD or Temporal imports.
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatch
from typing import Any

from pydantic import BaseModel

from nomad_eosc_galaxy_actions.state_store import StateStore
from nomad_eosc_galaxy_actions.zenodo_client import (
    ARCHIVE_EXTENSIONS,
    ZenodoFile,
    ZenodoRecord,
    has_extension,
)

# Version of the work_list.json layout (keys and their meaning). Galaxy's
# download tool reads it, so a change needs a matching change there.
WORK_LIST_SCHEMA_VERSION = 1


class WorkListSettings(BaseModel):
    """The harvest settings the work list needs; Galaxy gets them too."""

    raw_extensions: list[str]
    context_extensions: list[str]
    ignore_patterns: list[str]
    max_archive_bytes: int
    max_uncompressed_bytes: int


class SkippedFile(BaseModel):
    """A file that is not downloaded, with the reason for the report."""

    record_id: str
    key: str
    reason: str


def _is_ignored(key: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch(key, pattern) for pattern in patterns)


def _file_entry(file: ZenodoFile) -> dict[str, Any]:
    return {"key": file.key, "size": file.size, "md5": file.md5}


def plan_record(
    record: ZenodoRecord, store: StateStore, settings: WorkListSettings
) -> tuple[list[ZenodoFile], list[SkippedFile]]:
    """Files to download for one record, and files skipped with a reason.

    Returns no files to download if the record has nothing new; its loose
    context files are only added when something else is downloaded.
    """
    cid = record.concept_record_id
    work: list[ZenodoFile] = []
    context: list[ZenodoFile] = []
    skipped: list[SkippedFile] = []

    def skip(file: ZenodoFile, reason: str) -> None:
        skipped.append(
            SkippedFile(record_id=record.record_id, key=file.key, reason=reason)
        )

    for file in record.files:
        if _is_ignored(file.key, settings.ignore_patterns):
            continue
        if has_extension(file.key, ARCHIVE_EXTENSIONS):
            # Without an MD5 the archive cannot be recognised, so it is downloaded.
            if file.md5 and store.is_archive_resolved(cid, file.md5):
                skip(file, "archive_resolved")
            elif file.size > settings.max_archive_bytes:
                skip(file, "too_large")
            else:
                work.append(file)
        elif has_extension(file.key, settings.raw_extensions):
            if file.md5 and store.is_loose_md5_known(cid, file.md5):
                skip(file, "already_known")
            else:
                work.append(file)
        elif has_extension(file.key, settings.context_extensions):
            context.append(file)

    return (work + context if work else []), skipped


def build_work_list(
    candidates: Iterable[ZenodoRecord],
    store: StateStore,
    settings: WorkListSettings,
    run_id: str,
    max_records: int | None = None,
) -> tuple[dict[str, Any], list[SkippedFile]]:
    """Build the content of `work_list.json` and the list of skipped files.

    `max_records` limits the records **with work**, so already handled
    records never use up the limit; the rest are picked up by later runs.

    Each listed record carries its metadata (context for the conversion),
    the files to download and `known_sha256`: raw files of the same concept
    record that already have a final status, so Galaxy does not convert them
    again when they show up in a new archive.
    """
    records: list[dict[str, Any]] = []
    skipped: list[SkippedFile] = []
    for record in candidates:
        if max_records is not None and len(records) >= max_records:
            break
        download, record_skipped = plan_record(record, store, settings)
        skipped.extend(record_skipped)
        if not download:
            continue
        records.append(
            {
                "record_id": record.record_id,
                "version_doi": record.doi,
                "concept_record_id": record.concept_record_id,
                "concept_doi": record.concept_doi,
                "license": record.license,
                "title": record.title,
                "creators": record.creators,
                "description_html": record.description_html,
                "download": [_file_entry(file) for file in download],
                "known_sha256": store.known_sha256(record.concept_record_id),
            }
        )
    work_list = {
        "schema_version": WORK_LIST_SCHEMA_VERSION,
        "run_id": run_id,
        "limits": {
            "max_archive_bytes": settings.max_archive_bytes,
            "max_uncompressed_bytes": settings.max_uncompressed_bytes,
        },
        "raw_extensions": settings.raw_extensions,
        "context_extensions": settings.context_extensions,
        "ignore_patterns": settings.ignore_patterns,
        "records": records,
    }
    return work_list, skipped
