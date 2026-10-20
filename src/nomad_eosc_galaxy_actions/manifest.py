# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Merge Galaxy's manifest and conversion results into the state.

Galaxy computes, NOMAD writes: the manifest lists every downloaded or
extracted file, the results give the conversion status per raw file
(joined by SHA-256). Applies the decision table of the project plan:

- a processed raw file gets `success` or `failed` (no result: `failed`);
- a loose raw file's Zenodo MD5 is kept, so it is skipped before download;
- an archive is `resolved` once extracted, or when it failed for a reason
  a new download cannot fix;
- failed files from an older tool version are set to `retry`.

Framework-agnostic: no NOMAD or Temporal imports.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from nomad_eosc_galaxy_actions.state_store import StateStore

# Archive failures that downloading again cannot fix (`download_failed` and
# `md5_mismatch` can be temporary, so those archives are tried again).
PERMANENT_ARCHIVE_REASONS = frozenset(
    {"too_large", "unsafe_path", "uncompressed_limit", "unsupported_archive"}
)


class ManifestArchive(BaseModel):
    concept_record_id: str
    version_doi: str | None = None
    key: str
    md5: str | None = None
    status: Literal["extracted", "skipped", "failed"]
    reason: str | None = None


class ManifestFile(BaseModel):
    concept_record_id: str
    version_doi: str | None = None
    record_id: str
    source: Literal["archive", "loose"]
    archive_key: str | None = None
    path: str
    size: int
    sha256: str
    md5: str | None = None
    role: Literal["raw", "context", "ignored"]
    action: Literal["process", "already_known", "skipped"]
    reason: str | None = None
    context_paths: list[str] = []


class Manifest(BaseModel):
    """`manifest.json` from Galaxy; only schema version 1 exists so far."""

    schema_version: Literal[1]
    run_id: str
    tool_version: str
    archives: list[ManifestArchive] = []
    files: list[ManifestFile] = []


class FileResult(BaseModel):
    sha256: str
    status: Literal["success", "failed"]
    error: str | None = None


class Results(BaseModel):
    """`results.json` from Galaxy: conversion status per raw file."""

    schema_version: Literal[1]
    run_id: str
    files: list[FileResult] = []


class MergeSummary(BaseModel):
    success: int = 0
    failed: int = 0
    archives_resolved: int = 0
    archives_unresolved: int = 0
    retried: int = 0


def _file_status(
    file: ManifestFile, result: FileResult | None
) -> tuple[str, str | None]:
    """Status and error of a raw file that Galaxy processed or skipped."""
    if file.action == "skipped":  # e.g. an unreadable archive member
        return "failed", file.reason or "skipped"
    if result is None:
        return "failed", "no_result"
    return result.status, result.error


def merge_manifest(
    store: StateStore, manifest: Manifest, results: Results
) -> MergeSummary:
    """Write one run's outcome into the state, in a single transaction.

    Raises:
        ValueError: the results belong to another run.
    """
    if results.run_id != manifest.run_id:
        raise ValueError(
            f"results run_id {results.run_id!r} does not match the "
            f"manifest's {manifest.run_id!r}"
        )
    by_sha256 = {result.sha256: result for result in results.files}
    archive_md5 = {(a.concept_record_id, a.key): a.md5 for a in manifest.archives}
    summary = MergeSummary()
    with store.transaction():
        for file in manifest.files:
            if file.role != "raw":  # context files are inputs, not tracked
                continue
            cid = file.concept_record_id
            if file.source == "loose" and file.md5:
                store.record_loose_md5(cid, file.md5, file.sha256)
            if file.action == "already_known":
                continue
            status, error = _file_status(file, by_sha256.get(file.sha256))
            store.record_file(
                cid,
                file.sha256,
                status=status,
                path=file.path,
                version_doi=file.version_doi,
                tool_version=manifest.tool_version,
                error=error,
                archive_md5=(
                    archive_md5.get((cid, file.archive_key))
                    if file.source == "archive"
                    else None
                ),
            )
            if status == "success":
                summary.success += 1
            else:
                summary.failed += 1
        for archive in manifest.archives:
            if not archive.md5:  # cannot be recognized in a later run
                continue
            resolved = (
                archive.status == "extracted"
                or archive.reason in PERMANENT_ARCHIVE_REASONS
            )
            store.set_archive_resolved(archive.concept_record_id, archive.md5, resolved)
            if resolved:
                summary.archives_resolved += 1
            else:
                summary.archives_unresolved += 1
        summary.retried = store.retry_failed(manifest.tool_version)
    return summary
