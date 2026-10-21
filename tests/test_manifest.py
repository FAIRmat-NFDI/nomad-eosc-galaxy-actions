# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""One test per row of the decision table (plan section 3.1) that the merge
decides, checked through the next run's work list where possible."""

import pytest
from pydantic import ValidationError

from nomad_eosc_galaxy_actions.manifest import (
    Manifest,
    MergeSummary,
    Results,
    merge_manifest,
)
from nomad_eosc_galaxy_actions.state_store import StateStore
from nomad_eosc_galaxy_actions.work_list import WorkListSettings, plan_record
from nomad_eosc_galaxy_actions.zenodo_client import ZenodoFile, ZenodoRecord

CID = "100"
RUN = "run-1"
SETTINGS = WorkListSettings(
    raw_extensions=[".sxm"],
    context_extensions=[".txt"],
    ignore_patterns=[],
    max_archive_bytes=1000,
    max_uncompressed_bytes=5000,
)


@pytest.fixture
def store(tmp_path):
    with StateStore(tmp_path / "state.sqlite") as store:
        yield store


def _archive(md5="zip-md5", status="extracted", reason=None, key="data.zip"):
    return {
        "concept_record_id": CID,
        "record_id": "101",
        "key": key,
        "md5": md5,
        "status": status,
        "reason": reason,
    }


def _file(sha256, *, role="raw", action="process", source="archive", **extra):
    return {
        "concept_record_id": CID,
        "record_id": "101",
        "version_doi": "10.5281/zenodo.101",
        "source": source,
        "archive_key": "data.zip" if source == "archive" else None,
        "path": f"data/{sha256}.sxm",
        "size": 1,
        "sha256": sha256,
        "role": role,
        "action": action,
    } | extra


def _merge(  # noqa: PLR0913
    store,
    *,
    archives=(),
    files=(),
    results=(),
    tool_version="0.1",
    upload_ids=None,
    left_out=(),
):
    manifest = Manifest(
        schema_version=1,
        run_id=RUN,
        tool_version=tool_version,
        archives=list(archives),
        files=list(files),
    )
    results = Results(
        schema_version=1,
        run_id=RUN,
        files=[{"sha256": s, "status": st, "error": e} for s, st, e in results],
    )
    return merge_manifest(store, manifest, results, upload_ids, left_out)


def _next_download(store, *files: tuple[str, str]) -> list[str]:
    """Keys the next run's work list would download for this record."""
    record = ZenodoRecord(
        record_id="102",
        concept_record_id=CID,
        files=[ZenodoFile(key=key, size=10, md5=md5) for key, md5 in files],
    )
    return [file.key for file in plan_record(record, store, SETTINGS)[0]]


def _status(store, sha256):
    return store._db.execute(
        "SELECT status, error, archive_md5 FROM files WHERE sha256 = ?", (sha256,)
    ).fetchone()


def test_new_record_records_files_and_resolves_archive(store):
    summary = _merge(
        store,
        archives=[_archive()],
        files=[
            _file("sha-a"),
            _file("sha-b"),
            _file("sha-txt", role="context"),
            _file("sha-mp4", role="ignored", action="skipped"),
        ],
        results=[("sha-a", "success", None), ("sha-b", "failed", "reader error")],
    )

    assert summary == MergeSummary(success=1, failed=1, archives_resolved=1)
    assert _status(store, "sha-a") == ("success", None, "zip-md5")
    assert _status(store, "sha-b") == ("failed", "reader error", "zip-md5")
    assert store.known_sha256(CID) == ["sha-a", "sha-b"]  # context not tracked
    # Next version with the same archive: not downloaded again.
    assert _next_download(store, ("data.zip", "zip-md5")) == []


def test_processed_file_without_result_fails(store):
    _merge(store, archives=[_archive()], files=[_file("sha-a")])

    assert _status(store, "sha-a") == ("failed", "no_result", "zip-md5")


def test_member_skipped_in_galaxy_fails_with_reason(store):
    _merge(
        store,
        archives=[_archive()],
        files=[_file("sha-a", action="skipped", reason="unreadable")],
    )

    assert _status(store, "sha-a") == ("failed", "unreadable", "zip-md5")


def test_already_known_file_is_not_rewritten(store):
    _merge(store, files=[_file("sha-a")], results=[("sha-a", "success", None)])
    summary = _merge(
        store,
        files=[_file("sha-a", action="already_known")],
        tool_version="0.2",
    )

    assert summary == MergeSummary()
    row = store._db.execute(
        "SELECT tool_version FROM files WHERE sha256 = 'sha-a'"
    ).fetchone()
    assert row == ("0.1",)


def test_loose_raw_file_with_known_md5_is_not_downloaded(store):
    _merge(
        store,
        files=[_file("sha-a", source="loose", md5="md5-a")],
        results=[("sha-a", "success", None)],
    )

    assert _status(store, "sha-a") == ("success", None, None)
    assert _next_download(store, ("scan.sxm", "md5-a")) == []
    assert _next_download(store, ("other.sxm", "md5-b")) == ["other.sxm"]


def test_new_archive_md5_is_downloaded(store):
    _merge(store, archives=[_archive()])

    assert _next_download(store, ("data.zip", "new-md5")) == ["data.zip"]


@pytest.mark.parametrize(
    "reason, resolved",
    [
        ("unsafe_path", True),
        ("uncompressed_limit", True),
        ("unsupported_archive", True),
        ("too_large", True),
        ("download_failed", False),  # may work next time
        ("md5_mismatch", False),
    ],
)
def test_failed_archive(store, reason, resolved):
    summary = _merge(store, archives=[_archive(status="failed", reason=reason)])

    assert store.is_archive_resolved(CID, "zip-md5") is resolved
    assert summary.archives_unresolved == (0 if resolved else 1)


def test_archive_without_md5_is_not_stored(store):
    summary = _merge(store, archives=[_archive(md5=None)])

    assert summary == MergeSummary()
    assert store._db.execute("SELECT COUNT(*) FROM archives").fetchone() == (0,)


def test_newer_tool_version_retries_failed_files(store):
    _merge(
        store,
        archives=[_archive()],
        files=[_file("sha-a"), _file("sha-b")],
        results=[("sha-a", "success", None), ("sha-b", "failed", "bug")],
    )
    # A later run with a newer converter, on another record.
    summary = _merge(store, tool_version="0.2")

    assert summary.retried == 1
    assert _status(store, "sha-b")[0] == "retry"
    assert store.known_sha256(CID) == ["sha-a"]  # Galaxy converts sha-b again
    assert _next_download(store, ("data.zip", "zip-md5")) == ["data.zip"]


def test_results_of_another_run_are_refused(store):
    manifest = Manifest(schema_version=1, run_id=RUN, tool_version="0.1")
    results = Results(schema_version=1, run_id="run-2")

    with pytest.raises(ValueError, match="does not match"):
        merge_manifest(store, manifest, results)


def test_upload_id_is_stored_per_record(store):
    _merge(
        store,
        files=[_file("sha-a"), _file("sha-b", record_id="102")],
        results=[("sha-a", "success", None), ("sha-b", "success", None)],
        upload_ids={"101": "upload-1"},
    )

    rows = store._db.execute("SELECT sha256, upload_id FROM files ORDER BY sha256")
    assert rows.fetchall() == [("sha-a", "upload-1"), ("sha-b", None)]


def test_record_without_project_is_left_out(store):
    # No NOMAD project for record 101 (e.g. the project limit was reached).
    summary = _merge(
        store,
        archives=[_archive()],
        files=[
            _file("sha-a"),
            _file("sha-loose", source="loose", md5="md5-l"),
            _file("sha-b", record_id="102"),
        ],
        results=[("sha-a", "success", None), ("sha-b", "success", None)],
        left_out=["101"],
    )

    assert summary == MergeSummary(success=1, records_left_out=1)
    assert store.known_sha256(CID) == ["sha-b"]
    assert _next_download(store, ("data.zip", "zip-md5"), ("x.sxm", "md5-l")) == [
        "data.zip",
        "x.sxm",
    ]


def test_empty_manifest(store):
    assert _merge(store) == MergeSummary()


def test_unknown_schema_version_is_refused():
    with pytest.raises(ValidationError):
        Manifest(schema_version=2, run_id=RUN, tool_version="0.1")
