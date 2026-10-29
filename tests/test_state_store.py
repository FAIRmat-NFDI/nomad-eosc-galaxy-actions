# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import json
import multiprocessing
from datetime import datetime, timedelta, timezone

import pytest

from nomad_eosc_galaxy_actions.state_store import (
    ACTIVE_RUN_FILE,
    STATE_SCHEMA_VERSION,
    NoTransactionError,
    RunLockedError,
    StateSchemaError,
    StateStore,
    acquire_run,
    release_run,
)

CID = "5907155"


@pytest.fixture
def store(tmp_path):
    with StateStore(tmp_path / "state" / "state.json") as store:
        yield store


def _files(store) -> dict:
    return store.snapshot()["files"][CID]


def test_missing_file_is_an_empty_state(tmp_path):
    path = tmp_path / "a" / "state.json"
    with StateStore(path) as store:
        assert store.schema_version == STATE_SCHEMA_VERSION
        assert store.known_sha256(CID) == []
    assert not path.exists()  # reading never writes


def test_transaction_writes_the_file(tmp_path):
    path = tmp_path / "a" / "state.json"  # parent folder is created
    with StateStore(path) as store, store.transaction():
        store.record_loose_md5(CID, "md5-a", "sha-a")

    content = json.loads(path.read_text())
    assert content["schema_version"] == STATE_SCHEMA_VERSION
    assert content["loose_md5"] == {CID: {"md5-a": "sha-a"}}
    assert sorted(p.name for p in path.parent.iterdir()) == [
        "state.json",
        "state.json.lock",
    ]
    with StateStore(path) as store:  # reopening reads it back
        assert store.is_loose_md5_known(CID, "md5-a")


def _write(path, content: dict) -> None:
    path.write_text(json.dumps(content))


@pytest.mark.parametrize("version", [None, 0, "1"])
def test_bad_schema_version_is_refused(tmp_path, version):
    path = tmp_path / "state.json"
    _write(path, {"schema_version": version})

    with pytest.raises(StateSchemaError, match="bad schema_version"):
        StateStore(path)


def test_newer_schema_version_is_refused(tmp_path):
    path = tmp_path / "state.json"
    _write(path, {"schema_version": STATE_SCHEMA_VERSION + 1})

    with pytest.raises(StateSchemaError, match="newer than supported"):
        StateStore(path)


# Fake upgrade steps for an imagined version 3: v1 -> v2 and v2 -> v3 each add a key.
FAKE_UPGRADES = {1: lambda s: s | {"a": 1}, 2: lambda s: s | {"b": 2}}


def test_upgrade_runs_each_step_in_memory(tmp_path):
    path = tmp_path / "state.json"
    _write(path, {"schema_version": 1, "files": {}, "loose_md5": {}, "archives": {}})
    before = path.read_bytes()

    with StateStore(path, schema_version=3, upgrades=FAKE_UPGRADES) as store:
        assert store.schema_version == 3  # noqa: PLR2004
        assert (store.snapshot()["a"], store.snapshot()["b"]) == (1, 2)
    assert path.read_bytes() == before  # saved only with the next transaction


def test_missing_upgrade_step_is_refused(tmp_path):
    path = tmp_path / "state.json"
    _write(path, {"schema_version": 1})

    with pytest.raises(StateSchemaError, match="no upgrade from state schema 1"):
        StateStore(path, schema_version=2, upgrades={})


def test_writes_need_a_transaction(store):
    with pytest.raises(NoTransactionError):
        store.set_archive_resolved(CID, "md5", True)


def test_transactions_cannot_be_nested(store):
    with store.transaction(), pytest.raises(NoTransactionError):
        with store.transaction():
            pass


def test_failed_transaction_saves_nothing(store, tmp_path):
    with store.transaction():
        store.record_file(CID, "sha-a", status="failed")
    before = (tmp_path / "state" / "state.json").read_bytes()

    with pytest.raises(RuntimeError), store.transaction():
        store.set_archive_resolved(CID, "md5", True)
        store.record_file(CID, "sha-a", status="success")
        raise RuntimeError("merge interrupted")

    assert not store.is_archive_resolved(CID, "md5")
    assert _files(store)["sha-a"]["status"] == "failed"
    assert (tmp_path / "state" / "state.json").read_bytes() == before
    assert not list((tmp_path / "state").glob("*.tmp"))


def test_known_sha256_only_final_statuses(store):
    with store.transaction():
        store.record_file(CID, "sha-b", status="failed", error="reader error")
        store.record_file(CID, "sha-a", status="success", upload_id="u1")
        store.record_file(CID, "sha-c", status="retry")  # not final
        store.record_file("other", "sha-d", status="success")

    assert store.known_sha256(CID) == ["sha-a", "sha-b"]
    assert store.known_sha256("unknown") == []


def test_record_file_keeps_first_version_doi(store):
    with store.transaction():
        store.record_file(CID, "sha-a", status="failed", version_doi="doi/v1")
        store.record_file(CID, "sha-a", status="success", version_doi="doi/v2")

    file = _files(store)["sha-a"]
    assert (file["status"], file["version_doi"]) == ("success", "doi/v1")


def test_loose_md5(store):
    with store.transaction():
        store.record_loose_md5(CID, "md5-a", "sha-a")

    assert store.is_loose_md5_known(CID, "md5-a")
    assert not store.is_loose_md5_known(CID, "md5-b")
    assert not store.is_loose_md5_known("other", "md5-a")  # keyed per concept


def test_archive_resolved_flag(store):
    assert not store.is_archive_resolved(CID, "md5")  # unknown archive
    with store.transaction():
        store.set_archive_resolved(CID, "md5", False)
    assert not store.is_archive_resolved(CID, "md5")
    with store.transaction():
        store.set_archive_resolved(CID, "md5", True)
    assert store.is_archive_resolved(CID, "md5")


def _record_in_process(path, sha256: str) -> None:
    with StateStore(path) as store, store.transaction():
        store.record_file(CID, sha256, status="success")


def test_parallel_writers_keep_each_others_changes(tmp_path):
    # Two worker processes merge into the same state; the lock serializes them.
    path = tmp_path / "state.json"
    with StateStore(path) as stale:  # opened before the others write
        workers = [
            multiprocessing.Process(target=_record_in_process, args=(path, f"sha-{i}"))
            for i in range(4)
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        with stale.transaction():  # starts from the file, not from its old copy
            stale.record_file(CID, "sha-x", status="failed")

    with StateStore(path) as store:
        assert store.known_sha256(CID) == ["sha-0", "sha-1", "sha-2", "sha-3", "sha-x"]


def test_retry_failed_reopens_archive_and_loose_file(store):
    with store.transaction():
        store.set_archive_resolved(CID, "zip-1", True)
        store.set_archive_resolved(CID, "zip-2", True)
        store.record_file(
            CID, "sha-a", status="failed", tool_version="0.1", archive_md5="zip-1"
        )
        store.record_file(
            CID, "sha-b", status="success", tool_version="0.1", archive_md5="zip-2"
        )
        store.record_file(CID, "sha-c", status="failed", tool_version="0.1")
        store.record_loose_md5(CID, "md5-c", "sha-c")
        store.record_file(CID, "sha-d", status="failed", tool_version="0.2")

    with store.transaction():
        assert store.retry_failed("0.2") == 2  # noqa: PLR2004

    assert store.known_sha256(CID) == ["sha-b", "sha-d"]  # retry is not final
    assert not store.is_archive_resolved(CID, "zip-1")
    assert store.is_archive_resolved(CID, "zip-2")  # only successes inside
    assert not store.is_loose_md5_known(CID, "md5-c")


def test_retry_failed_same_version_changes_nothing(store):
    with store.transaction():
        store.record_file(CID, "sha-a", status="failed", tool_version="0.1")
        assert store.retry_failed("0.1") == 0
    assert store.known_sha256(CID) == ["sha-a"]


def test_retry_failed_needs_a_transaction(store):
    with pytest.raises(NoTransactionError):
        store.retry_failed("0.1")


# One run per user at a time

T0 = datetime(2026, 10, 28, 8, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def test_second_run_of_a_user_is_refused(tmp_path):
    acquire_run(tmp_path, "run-1", now=T0, max_age=DAY)

    with pytest.raises(RunLockedError, match="run-1, started 2026-10-28T08:00"):
        acquire_run(tmp_path, "run-2", now=T0 + DAY / 2, max_age=DAY)


def test_acquire_again_is_fine_for_a_retry(tmp_path):
    acquire_run(tmp_path, "run-1", now=T0, max_age=DAY)
    acquire_run(tmp_path, "run-1", now=T0, max_age=DAY)


def test_stale_mark_is_taken_over(tmp_path):
    acquire_run(tmp_path, "run-1", now=T0, max_age=DAY)

    acquire_run(tmp_path, "run-2", now=T0 + DAY, max_age=DAY)

    assert json.loads((tmp_path / ACTIVE_RUN_FILE).read_text())["run_id"] == "run-2"


def test_release_keeps_another_runs_mark(tmp_path):
    assert release_run(tmp_path, "run-1") is False  # nothing to release
    acquire_run(tmp_path, "run-1", now=T0, max_age=DAY)

    assert release_run(tmp_path, "run-2") is False
    assert release_run(tmp_path, "run-1") is True
    acquire_run(tmp_path, "run-2", now=T0, max_age=DAY)  # free again
