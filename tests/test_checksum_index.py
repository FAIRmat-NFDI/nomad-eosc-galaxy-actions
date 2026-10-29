# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import json

import pytest

from nomad_eosc_galaxy_actions.checksum_index import (
    CHECKSUM_INDEX_SCHEMA_VERSION,
    ChecksumIndex,
    ChecksumIndexError,
    IndexEntry,
    converted_files,
    find_duplicates,
)
from nomad_eosc_galaxy_actions.manifest import Manifest, Results


def _entry(sha256="sha-a", user_id="u1", upload_id="up-1") -> IndexEntry:
    return IndexEntry(
        sha256=sha256, user_id=user_id, upload_id=upload_id, record_id="101", path="a"
    )


@pytest.fixture
def path(tmp_path):
    return tmp_path / "checksum_index.json"


def test_missing_file_is_an_empty_index(path):
    index = ChecksumIndex(path)

    assert index.known_sha256("u1") == []
    assert index.projects("sha-a", "u1") == []
    assert not path.exists()


def test_add_writes_the_file_once_per_entry(path):
    index = ChecksumIndex(path)

    assert index.add([_entry(), _entry()]) == 1
    assert index.add([_entry()]) == 0  # a retried merge adds nothing

    content = json.loads(path.read_text())
    assert content["schema_version"] == CHECKSUM_INDEX_SCHEMA_VERSION
    assert len(content["files"]["sha-a"]) == 1
    assert ChecksumIndex(path).projects("sha-a", "u1") == ["up-1"]


def test_only_own_projects_count(path):
    ChecksumIndex(path).add([_entry(), _entry("sha-b", user_id="u2", upload_id="up-9")])
    index = ChecksumIndex(path)

    assert index.known_sha256("u1") == ["sha-a"]
    assert index.known_sha256("u2") == ["sha-b"]
    assert index.projects("sha-b", "u1") == []  # another user's private project


def test_add_keeps_entries_written_by_another_run(path):
    stale = ChecksumIndex(path)  # opened before the other run wrote
    ChecksumIndex(path).add([_entry("sha-b", user_id="u2")])

    stale.add([_entry()])

    assert set(json.loads(path.read_text())["files"]) == {"sha-a", "sha-b"}


@pytest.mark.parametrize("version", [None, CHECKSUM_INDEX_SCHEMA_VERSION + 1])
def test_unsupported_version_is_refused(path, version):
    path.write_text(json.dumps({"schema_version": version, "files": {}}))

    with pytest.raises(ChecksumIndexError, match="unsupported schema_version"):
        ChecksumIndex(path)


def _file(sha256, *, record_id="101", role="raw", action="process") -> dict:
    return {
        "concept_record_id": "100",
        "record_id": record_id,
        "source": "loose",
        "path": f"{sha256}.sxm",
        "size": 1,
        "sha256": sha256,
        "role": role,
        "action": action,
    }


def test_converted_files_only_successful_raw_files_with_a_project():
    manifest = Manifest(
        schema_version=1,
        run_id="r",
        tool_version="0.1",
        files=[
            _file("ok"),
            _file("bad"),
            _file("readme", role="context"),
            _file("known", action="already_known"),
            _file("no-project", record_id="102"),
        ],
    )
    results = Results(
        schema_version=1,
        run_id="r",
        files=[
            {"sha256": sha, "status": "failed" if sha == "bad" else "success"}
            for sha in ("ok", "bad", "readme", "known", "no-project")
        ],
    )

    entries = converted_files(manifest, results, {"101": "up-1"}, "u1")

    assert [(e.sha256, e.upload_id, e.path) for e in entries] == [
        ("ok", "up-1", "ok.sxm")
    ]


def test_find_duplicates(path):
    ChecksumIndex(path).add([_entry("sha-a")])
    manifest = {
        "files": [
            _file("sha-a", action="already_known"),  # in project up-1
            _file("sha-b", action="already_known"),  # known, but in no project
            _file("sha-a", record_id="102"),  # converted again (duplicates allowed)
        ]
    }

    [duplicate] = find_duplicates(manifest, ChecksumIndex(path), "u1")

    assert duplicate.model_dump() == {
        "record_id": "101",
        "key": "sha-a.sxm",
        "sha256": "sha-a",
        "upload_id": "up-1",
        "reason": "duplicate",
    }
    assert find_duplicates(manifest, ChecksumIndex(path), "u2") == []
    assert find_duplicates({}, ChecksumIndex(path), "u1") == []
