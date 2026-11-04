# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import hashlib
import json
import zipfile
from pathlib import Path

import pytest
import requests

from nomad_eosc_galaxy_actions.manifest import Manifest, Results
from nomad_eosc_galaxy_actions.nomad_projects import (
    HARVEST_METADATA_FILE,
    HARVEST_METADATA_SCHEMA_VERSION,
    MAX_DEMO_PROJECTS,
    METADATA_FILE,
    ProjectLimitReached,
    RestProjects,
    add_harvest_metadata,
    add_project_metadata,
    create_project,
    merge_entries,
    merge_references,
    project_name,
    version_folder,
    wait_until_idle,
)

NAME = project_name("100")
REFERENCE = "https://doi.org/10.5281/zenodo.101"


class InMemoryProjects:
    """A fake `Projects`: unpublished projects and their files kept in dicts."""

    def __init__(self, names=()):
        self.projects = {name: f"id-{name}" for name in names}
        self.files: dict[str, dict[str, bytes]] = {
            i: {} for i in self.projects.values()
        }
        self.created: list[str] = []
        self.added: list[str] = []

    def unpublished_projects(self) -> dict[str, str]:
        return dict(self.projects)

    def create(self, name: str, zip_path: Path | None) -> str:
        self.created.append(name)
        self.projects[name] = f"id-{name}"
        self.files[self.projects[name]] = {}
        if zip_path is not None:
            self._extract(self.projects[name], zip_path)
        return self.projects[name]

    def add(self, upload_id: str, zip_path: Path) -> None:
        self.added.append(upload_id)
        self._extract(upload_id, zip_path)

    def read_file(self, upload_id: str, path: str) -> bytes | None:
        return self.files[upload_id].get(path)

    def write_file(self, upload_id: str, path: str, content: bytes) -> None:
        self.files[upload_id][path] = content

    def _extract(self, upload_id: str, zip_path: Path) -> None:
        with zipfile.ZipFile(zip_path) as archive:
            for name in archive.namelist():
                self.files[upload_id][name] = archive.read(name)


@pytest.fixture
def zip_path(tmp_path) -> Path:
    path = tmp_path / "101.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("scan_1/scan_1.nxs", b"nexus")
    return path


def test_project_name():
    assert NAME == "zenodo_100"


def test_version_folder():
    assert version_folder("101", "10.5281/zenodo.101") == "10.5281_zenodo.101"
    assert version_folder("101", None) == "zenodo_101"


def test_merge_references_keeps_order_without_repeats():
    old = json.dumps({"references": ["a", "b"]}).encode()
    assert merge_references(old, ["b", "c"]) == ["a", "b", "c"]
    assert merge_references(None, ["c"]) == ["c"]
    assert merge_references(b"{}", []) == []


def test_wait_until_idle_returns_once_idle():
    answers = iter([True, False])
    wait_until_idle(lambda: next(answers), timeout=10)


def test_wait_until_idle_times_out():
    with pytest.raises(TimeoutError, match="still processing"):
        wait_until_idle(lambda: True, timeout=0)


# add_harvest_metadata

RECORD = {
    "record_id": "101",
    "concept_record_id": "100",
    "version_doi": "10.5281/zenodo.101",
    "concept_doi": None,
    "title": "not copied",
}


def _raw(path, sha256, record_id="101", action="process"):
    return {
        "concept_record_id": "100",
        "record_id": record_id,
        "source": "archive",
        "path": path,
        "size": 1,
        "sha256": sha256,
        "role": "raw",
        "action": action,
    }


def _harvest_inputs():
    manifest = Manifest(
        schema_version=1,
        run_id="r",
        tool_version="0.1",
        files=[
            _raw("d/scan_2.sxm", "sha-2"),  # conversion failed: no NeXus file
            _raw("d/scan_1.sxm", "sha-1"),
            _raw("d/known.sxm", "sha-k", action="already_known"),
            _raw("d/other.sxm", "sha-o", record_id="102"),
        ],
    )
    results = Results(
        schema_version=1,
        run_id="r",
        files=[
            {"sha256": "sha-1", "status": "success", "nexus_path": "scan_1/scan_1.nxs"},
            {"sha256": "sha-2", "status": "failed", "nexus_path": "missing.nxs"},
        ],
    )
    return manifest, results


def test_harvest_metadata_lists_the_records_raw_and_nexus_checksums(zip_path):
    add_harvest_metadata(zip_path, RECORD, *_harvest_inputs())

    with zipfile.ZipFile(zip_path) as archive:
        content = json.loads(
            archive.read(f"10.5281_zenodo.101/{HARVEST_METADATA_FILE}")
        )
    assert content == {
        "schema_version": HARVEST_METADATA_SCHEMA_VERSION,
        "zenodo": {
            "record_id": "101",
            "concept_record_id": "100",
            "version_doi": "10.5281/zenodo.101",
            "concept_doi": None,
        },
        "files": [
            {
                "raw_path": "d/scan_1.sxm",
                "raw_sha256": "sha-1",
                "nexus_path": "scan_1/scan_1.nxs",
                "nexus_sha256": hashlib.sha256(b"nexus").hexdigest(),
            },
            {
                "raw_path": "d/scan_2.sxm",
                "raw_sha256": "sha-2",
                "nexus_path": None,
                "nexus_sha256": None,
            },
        ],
    }


def test_harvest_metadata_is_retry_safe(zip_path):
    add_harvest_metadata(zip_path, RECORD, *_harvest_inputs())
    add_harvest_metadata(zip_path, RECORD, *_harvest_inputs())

    with zipfile.ZipFile(zip_path) as archive:
        assert (
            archive.namelist().count(f"10.5281_zenodo.101/{HARVEST_METADATA_FILE}") == 1
        )
    with pytest.raises(ValueError, match="another .*zenodo_harvest.json"):
        add_harvest_metadata(
            zip_path, RECORD | {"record_id": "999"}, *_harvest_inputs()
        )


# add_project_metadata


def test_metadata_file_is_added_once(zip_path):
    references = [REFERENCE]
    add_project_metadata(zip_path, references)
    add_project_metadata(zip_path, references)  # a retry changes nothing

    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        content = json.loads(archive.read(METADATA_FILE))
    assert names == ["scan_1/scan_1.nxs", METADATA_FILE]
    assert content == {"references": references}


def test_metadata_file_is_replaced(zip_path):
    add_project_metadata(zip_path, ["https://doi.org/x"])
    add_project_metadata(zip_path, ["https://doi.org/x", REFERENCE])

    with zipfile.ZipFile(zip_path) as archive:
        assert archive.namelist().count(METADATA_FILE) == 1
        assert archive.read("scan_1/scan_1.nxs") == b"nexus"
        content = json.loads(archive.read(METADATA_FILE))
    assert content == {"references": ["https://doi.org/x", REFERENCE]}


# create_project


def test_create_new_project(zip_path):
    projects = InMemoryProjects()

    assert create_project(projects, NAME, zip_path, [REFERENCE]) == (f"id-{NAME}", True)
    assert projects.created == [NAME]
    metadata = json.loads(projects.files[f"id-{NAME}"][METADATA_FILE])
    assert metadata == {"references": [REFERENCE]}


def test_new_version_is_added_to_the_concept_project(zip_path):
    projects = InMemoryProjects([NAME])
    upload_id = f"id-{NAME}"
    projects.files[upload_id][METADATA_FILE] = json.dumps(
        {"references": ["https://doi.org/10.5281/zenodo.100"]}
    ).encode()

    assert create_project(projects, NAME, zip_path, [REFERENCE]) == (upload_id, False)
    assert projects.created == []
    assert projects.added == [upload_id]
    metadata = json.loads(projects.files[upload_id][METADATA_FILE])
    assert metadata["references"] == ["https://doi.org/10.5281/zenodo.100", REFERENCE]


def test_adding_the_same_version_again_keeps_one_reference(zip_path):
    projects = InMemoryProjects()
    create_project(projects, NAME, zip_path, [REFERENCE])
    create_project(projects, NAME, zip_path, [REFERENCE])  # a retry

    metadata = json.loads(projects.files[f"id-{NAME}"][METADATA_FILE])
    assert metadata["references"] == [REFERENCE]


ENTRY_1 = {"scan_1/scan_1.nxs": {"external_id": "zenodo:100:sha256:1"}}
ENTRY_2 = {"scan_2/scan_2.nxs": {"external_id": "zenodo:100:sha256:2"}}


def test_merge_entries_new_metadata_wins_per_mainfile():
    old = json.dumps({"entries": ENTRY_1 | ENTRY_2}).encode()
    new = {"scan_2/scan_2.nxs": {"external_id": "zenodo:100:sha256:new"}}

    assert merge_entries(None, ENTRY_1) == ENTRY_1
    assert merge_entries(b'{"references": []}', {}) == {}
    assert merge_entries(old, new) == ENTRY_1 | new


def test_new_project_gets_the_entry_metadata(zip_path):
    projects = InMemoryProjects()

    create_project(projects, NAME, zip_path, [REFERENCE], ENTRY_1)

    metadata = json.loads(projects.files[f"id-{NAME}"][METADATA_FILE])
    assert metadata == {"references": [REFERENCE], "entries": ENTRY_1}


def test_new_version_keeps_the_entry_metadata_of_earlier_versions(zip_path):
    projects = InMemoryProjects()
    create_project(projects, NAME, zip_path, [REFERENCE], ENTRY_1)

    create_project(projects, NAME, zip_path, ["https://doi.org/x"], ENTRY_2)

    metadata = json.loads(projects.files[f"id-{NAME}"][METADATA_FILE])
    assert metadata["entries"] == ENTRY_1 | ENTRY_2


def test_limit_counts_only_harvest_projects(zip_path):
    demo = [project_name(str(i)) for i in range(MAX_DEMO_PROJECTS - 1)]
    projects = InMemoryProjects([*demo, "my own project", "zenodo_harvest_state"])

    create_project(projects, NAME, zip_path, [REFERENCE])  # the last one allowed

    with pytest.raises(ProjectLimitReached, match=f"limit {MAX_DEMO_PROJECTS}"):
        create_project(projects, project_name("200"), zip_path, [REFERENCE])
    assert projects.created == [NAME]


def test_adding_works_at_the_limit(zip_path):
    demo = [project_name(str(i)) for i in range(MAX_DEMO_PROJECTS - 1)]
    projects = InMemoryProjects([*demo, NAME])

    assert create_project(projects, NAME, zip_path, [REFERENCE]) == (
        f"id-{NAME}",
        False,
    )


# RestProjects (against a local fake NOMAD server)


def test_rest_lists_own_unpublished_projects(fake_nomad):
    fake_nomad.projects = {
        "u1": {"upload_name": NAME, "published": False},
        "u2": {"upload_name": "zenodo_2", "published": True},
        "u3": {"upload_name": None, "published": False},
    }

    result = RestProjects(fake_nomad.url, "pat-1").unpublished_projects()

    assert result == {NAME: "u1"}
    method, path, query = fake_nomad.requests[0]
    assert (method, path) == ("GET", "/api/v1/uploads")
    assert query == {
        "is_published": "false",
        "roles": "main_author",
        "page_size": "100",
    }
    assert fake_nomad.auth_headers == ["Bearer pat-1"]


def test_rest_creates_project(fake_nomad, zip_path):
    upload_id = RestProjects(fake_nomad.url + "/", "pat-1").create(NAME, zip_path)

    assert upload_id == "up-1"
    project = fake_nomad.projects[upload_id]
    assert project["upload_name"] == NAME
    assert project["content"] == zip_path.read_bytes()
    _, path, query = fake_nomad.requests[0]
    assert path == "/api/v1/uploads"
    assert query == {"upload_name": NAME, "file_name": "101.zip"}  # never publish


def test_rest_error_is_raised(fake_nomad, zip_path):
    fake_nomad.post_status = 400  # e.g. NOMAD's own upload limit

    with pytest.raises(requests.HTTPError):
        RestProjects(fake_nomad.url, "pat-1").create(NAME, zip_path)


def test_rest_creates_empty_project(fake_nomad):
    upload_id = RestProjects(fake_nomad.url, "pat-1").create(
        "zenodo_harvest_state", None
    )

    assert fake_nomad.projects[upload_id]["upload_name"] == "zenodo_harvest_state"
    assert fake_nomad.requests[0][2] == {"upload_name": "zenodo_harvest_state"}


def test_rest_adds_zip_after_processing(fake_nomad, zip_path):
    fake_nomad.projects = {"u1": {"upload_name": NAME, "published": False}}
    fake_nomad.busy_polls = 1  # processing at the first check

    RestProjects(fake_nomad.url, "pat-1").add("u1", zip_path)

    assert fake_nomad.projects["u1"]["files"]["scan_1/scan_1.nxs"] == b"nexus"
    methods = [(m, p) for m, p, _ in fake_nomad.requests]
    assert methods == [
        ("GET", "/api/v1/uploads/u1"),
        ("GET", "/api/v1/uploads/u1"),
        ("PUT", "/api/v1/uploads/u1/raw/"),
    ]
    assert fake_nomad.requests[-1][2] == {"file_name": "101.zip"}


def test_rest_reads_and_writes_files(fake_nomad):
    fake_nomad.projects = {"u1": {"upload_name": "state", "published": False}}
    projects = RestProjects(fake_nomad.url, "pat-1")

    assert projects.read_file("u1", "state.json") is None
    projects.write_file("u1", "state.json", b'{"a": 1}')
    projects.write_file("u1", "sub/x.json", b"{}")

    assert projects.read_file("u1", "state.json") == b'{"a": 1}'
    assert projects.read_file("u1", "sub/x.json") == b"{}"
