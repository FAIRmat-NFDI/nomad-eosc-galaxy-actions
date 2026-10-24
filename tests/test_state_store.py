# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import sqlite3
import threading

import pytest

from nomad_eosc_galaxy_actions.state_store import (
    STATE_SCHEMA_VERSION,
    NoTransactionError,
    StateSchemaError,
    StateStore,
)

CID = "5907155"


@pytest.fixture
def store(tmp_path):
    with StateStore(tmp_path / "state" / "state.sqlite") as store:
        yield store


def test_new_file_gets_tables_and_version(tmp_path):
    path = tmp_path / "a" / "state.sqlite"  # parent folder is created
    with StateStore(path) as store:
        assert store.schema_version == STATE_SCHEMA_VERSION
        assert store.known_sha256(CID) == []
    with StateStore(path) as store:  # reopening keeps the file as is
        assert store.schema_version == STATE_SCHEMA_VERSION


def test_newer_schema_version_is_refused(tmp_path):
    path = tmp_path / "state.sqlite"
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version = {STATE_SCHEMA_VERSION + 1}")
    db.close()

    with pytest.raises(StateSchemaError, match="newer than supported"):
        StateStore(path)


def _file_at_version(path, version: int, sql: str = "") -> None:
    db = sqlite3.connect(path)
    db.executescript(f"{sql}; PRAGMA user_version = {version};")
    db.close()


def test_upgrade_runs_each_step(tmp_path):
    # Example: version 3 is current; v1 -> v2 and v2 -> v3 each add a table.
    path = tmp_path / "state.sqlite"
    _file_at_version(path, 1, "CREATE TABLE files (x TEXT)")
    upgrades = {1: "CREATE TABLE a (y TEXT)", 2: "CREATE TABLE b (z TEXT)"}

    with StateStore(path, schema_version=3, upgrades=upgrades) as store:
        assert store.schema_version == 3  # noqa: PLR2004
        tables = store._db.execute("SELECT name FROM sqlite_master").fetchall()
    assert sorted(name for (name,) in tables) == ["a", "b", "files"]


def test_failed_upgrade_keeps_old_version(tmp_path):
    path = tmp_path / "state.sqlite"
    _file_at_version(path, 1, "CREATE TABLE files (x TEXT)")

    with pytest.raises(sqlite3.OperationalError):
        StateStore(path, schema_version=2, upgrades={1: "CREATE TABLE files (x)"})
    with StateStore(path, schema_version=1) as store:
        assert store.schema_version == 1


def test_missing_upgrade_step_is_refused(tmp_path):
    path = tmp_path / "state.sqlite"
    _file_at_version(path, 1, "CREATE TABLE files (x TEXT)")

    with pytest.raises(StateSchemaError, match="no upgrade from state schema 1"):
        StateStore(path, schema_version=2, upgrades={})


# Layout of schema version 1, before files got `archive_md5`.
_V1_FILES = """CREATE TABLE files (concept_record_id TEXT NOT NULL,
    sha256 TEXT NOT NULL, status TEXT NOT NULL, path TEXT, version_doi TEXT,
    tool_version TEXT, upload_id TEXT, error TEXT, updated_at TEXT NOT NULL,
    PRIMARY KEY (concept_record_id, sha256));
INSERT INTO files VALUES ('c', 'sha-a', 'success', 'a.sxm', NULL, '0.1',
    NULL, NULL, 'then')"""


def test_version_1_file_is_upgraded(tmp_path):
    path = tmp_path / "state.sqlite"
    _file_at_version(path, 1, _V1_FILES)

    with StateStore(path) as store:
        assert store.schema_version == STATE_SCHEMA_VERSION
        assert store.known_sha256("c") == ["sha-a"]  # rows are kept
        with store.transaction():
            store.record_file("c", "sha-b", status="failed", archive_md5="zip")
        row = store._db.execute(
            "SELECT archive_md5 FROM files WHERE sha256 = 'sha-b'"
        ).fetchone()
    assert row == ("zip",)


def test_writes_need_a_transaction(store):
    with pytest.raises(NoTransactionError):
        store.set_archive_resolved(CID, "md5", True)


def test_transactions_cannot_be_nested(store):
    with store.transaction(), pytest.raises(NoTransactionError):
        with store.transaction():
            pass


def test_failed_transaction_saves_nothing(store):
    with pytest.raises(RuntimeError), store.transaction():
        store.set_archive_resolved(CID, "md5", True)
        store.record_file(CID, "sha-a", status="success")
        raise RuntimeError("merge interrupted")

    assert not store.is_archive_resolved(CID, "md5")
    assert store.known_sha256(CID) == []


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

    row = store._db.execute(
        "SELECT status, version_doi FROM files WHERE sha256 = 'sha-a'"
    ).fetchone()
    assert row == ("success", "doi/v1")


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


def test_two_connections_see_committed_writes(tmp_path):
    # Two runs in parallel (e.g. a manual run during a scheduled one).
    path = tmp_path / "state.sqlite"
    with StateStore(path) as writer, writer.transaction():
        writer.record_loose_md5(CID, "md5-a", "sha-a")

    seen = []

    def read():
        with StateStore(path) as reader:
            seen.append(reader.is_loose_md5_known(CID, "md5-a"))

    thread = threading.Thread(target=read)
    thread.start()
    thread.join()
    assert seen == [True]


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
