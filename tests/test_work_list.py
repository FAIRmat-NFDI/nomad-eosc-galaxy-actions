# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import pytest

from nomad_eosc_galaxy_actions.state_store import StateStore
from nomad_eosc_galaxy_actions.work_list import (
    WORK_LIST_SCHEMA_VERSION,
    WorkListSettings,
    build_work_list,
    plan_record,
)
from nomad_eosc_galaxy_actions.zenodo_client import (
    ZenodoFile,
    ZenodoRecord,
    parse_record,
)

CID = "100"
SETTINGS = WorkListSettings(
    raw_extensions=[".sxm"],
    context_extensions=[".txt", ".md"],
    ignore_patterns=["__MACOSX/*", "*.mp4"],
    max_archive_bytes=1000,
    max_uncompressed_bytes=5000,
)


@pytest.fixture
def store(tmp_path):
    with StateStore(tmp_path / "state.json") as store:
        yield store


def _record(
    *files: tuple[str, int, str | None], record_id: str = "101"
) -> ZenodoRecord:
    return ZenodoRecord(
        record_id=record_id,
        concept_record_id=CID,
        doi=f"10.5281/zenodo.{record_id}",
        files=[ZenodoFile(key=k, size=s, md5=m) for k, s, m in files],
    )


def _keys(files) -> list[str]:
    return [f.key for f in files]


def _reasons(skipped) -> list[tuple[str, str]]:
    return [(s.key, s.reason) for s in skipped]


def test_new_record_downloads_archive_raw_and_context(store):
    record = _record(
        ("data.zip", 500, "z1"),
        ("scan.SXM", 10, "s1"),  # extension case is ignored
        ("readme.md", 1, "r1"),
        ("movie.mp4", 10, "m1"),  # ignored
        ("model.npz", 10, "n1"),  # neither raw nor context
    )
    download, skipped = plan_record(record, store, SETTINGS)

    assert _keys(download) == ["data.zip", "scan.SXM", "readme.md"]
    assert skipped == []


def test_resolved_archive_is_not_downloaded(store):
    with store.transaction():
        store.set_archive_resolved(CID, "z1", True)
        store.set_archive_resolved(CID, "z2", False)

    download, skipped = plan_record(
        _record(("v1.zip", 500, "z1"), ("v2.zip", 500, "z2"), ("readme.md", 1, "r")),
        store,
        SETTINGS,
    )

    assert _keys(download) == ["v2.zip", "readme.md"]  # unresolved: retried
    assert _reasons(skipped) == [("v1.zip", "archive_resolved")]


def test_record_with_nothing_new_gets_no_context(store):
    with store.transaction():
        store.set_archive_resolved(CID, "z1", True)
        store.record_loose_md5(CID, "s1", "sha-s1")

    download, skipped = plan_record(
        _record(("data.zip", 500, "z1"), ("scan.sxm", 10, "s1"), ("readme.md", 1, "r")),
        store,
        SETTINGS,
    )

    assert download == []
    assert _reasons(skipped) == [
        ("data.zip", "archive_resolved"),
        ("scan.sxm", "already_known"),
    ]


@pytest.mark.parametrize(
    "case",
    [(1000, ["big.zip"]), (1001, [])],  # limit itself is allowed
)
def test_archive_size_limit(store, case):
    size, expected = case
    download, skipped = plan_record(_record(("big.zip", size, "z")), store, SETTINGS)

    assert _keys(download) == expected
    assert _reasons(skipped) == ([] if expected else [("big.zip", "too_large")])


def test_files_without_md5_are_downloaded(store):
    with store.transaction():  # an unrelated known MD5 must not match None
        store.set_archive_resolved(CID, "z", True)

    download, _ = plan_record(
        _record(("data.zip", 10, None), ("scan.sxm", 10, None)), store, SETTINGS
    )
    assert _keys(download) == ["data.zip", "scan.sxm"]


def test_state_is_per_concept_record(store):
    with store.transaction():
        store.set_archive_resolved("other concept", "z1", True)

    download, _ = plan_record(_record(("data.zip", 10, "z1")), store, SETTINGS)
    assert _keys(download) == ["data.zip"]


def test_record_without_files(store):
    assert plan_record(_record(), store, SETTINGS) == ([], [])


def test_build_work_list_empty(store):
    content, skipped = build_work_list([], store, SETTINGS, run_id="run-1")

    assert content["records"] == [] and skipped == []
    assert content["schema_version"] == WORK_LIST_SCHEMA_VERSION
    assert content["run_id"] == "run-1"
    assert content["limits"] == {
        "max_archive_bytes": 1000,
        "max_uncompressed_bytes": 5000,
    }
    assert content["raw_extensions"] == [".sxm"]


def test_build_work_list_lists_only_records_with_work(store):
    with store.transaction():
        store.set_archive_resolved(CID, "done", True)
        store.record_file(CID, "sha-ok", status="success")
        store.record_file(CID, "sha-bad", status="failed")

    new = _record(("v2.zip", 10, "new"), ("readme.md", 1, "r"), record_id="102")
    done = _record(("v1.zip", 10, "done"), record_id="101")
    content, skipped = build_work_list([done, new], store, SETTINGS, run_id="r")

    [entry] = content["records"]
    assert entry["record_id"] == "102"
    assert entry["version_doi"] == "10.5281/zenodo.102"
    assert entry["download"] == [
        {"key": "v2.zip", "size": 10, "md5": "new"},
        {"key": "readme.md", "size": 1, "md5": "r"},
    ]
    assert entry["known_sha256"] == ["sha-bad", "sha-ok"]
    assert _reasons(skipped) == [("v1.zip", "archive_resolved")]


def test_max_records_counts_only_records_with_work(store):
    with store.transaction():
        store.set_archive_resolved(CID, "done", True)
    done = _record(("v1.zip", 10, "done"), record_id="101")
    new = [_record(("d.zip", 10, f"n{i}"), record_id=f"10{i}") for i in (2, 3, 4)]

    content, skipped = build_work_list(
        [done, *new], store, SETTINGS, run_id="r", max_records=2
    )

    # The handled record does not use up the limit; 104 waits for a later run.
    assert [r["record_id"] for r in content["records"]] == ["102", "103"]
    assert _reasons(skipped) == [("v1.zip", "archive_resolved")]


def test_repeated_runs_reach_the_end_of_the_list(store):
    # Same search result every run; each run's archives are then resolved.
    records = [_record(("d.zip", 10, f"n{i}"), record_id=str(i)) for i in range(5)]
    planned = []
    for run in range(3):
        content, _ = build_work_list(
            records, store, SETTINGS, run_id=str(run), max_records=2
        )
        planned.append([r["record_id"] for r in content["records"]])
        with store.transaction():
            for entry in content["records"]:
                for file in entry["download"]:
                    store.set_archive_resolved(CID, file["md5"], True)

    assert planned == [["0", "1"], ["2", "3"], ["4"]]


@pytest.mark.parametrize("case", [(None, 3), (3, 3), (5, 3)])
def test_max_records_limits(store, case):
    max_records, expected = case
    records = [_record(("d.zip", 10, f"n{i}"), record_id=str(i)) for i in range(3)]

    content, _ = build_work_list(
        records, store, SETTINGS, run_id="r", max_records=max_records
    )
    assert len(content["records"]) == expected


def test_build_work_list_on_recorded_hits(store, zenodo_hits):
    settings = SETTINGS.model_copy(update={"max_archive_bytes": 2_000_000_000})
    records = [parse_record(hit) for hit in zenodo_hits]

    content, skipped = build_work_list(records, store, settings, run_id="r")

    assert len(content["records"]) == len(records)  # empty state: all new
    assert _reasons(skipped) == [("STM_data.zip", "too_large")]  # 9.3 GB
    by_id = {r["record_id"]: r for r in content["records"]}
    assert _keys_of(by_id["22806339"]) == ["topo016.sxm", "LICENSE.txt", "README.md"]
    assert by_id["10843653"]["concept_doi"] is None


def _keys_of(entry: dict) -> list[str]:
    return [f["key"] for f in entry["download"]]


def test_files_in_projects_are_known_for_every_record(store):
    with store.transaction():
        store.record_file(CID, "sha-own", status="success")
    record = _record(("v1.zip", 10, "m"), record_id="101")

    content, _ = build_work_list(
        [record], store, SETTINGS, run_id="r", in_projects=["sha-elsewhere"]
    )

    assert content["records"][0]["known_sha256"] == ["sha-elsewhere", "sha-own"]
