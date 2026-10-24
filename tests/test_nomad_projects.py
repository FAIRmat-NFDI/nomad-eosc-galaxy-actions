# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import json
import zipfile
from pathlib import Path

import pytest
import requests

from nomad_eosc_galaxy_actions.nomad_projects import (
    MAX_DEMO_PROJECTS,
    METADATA_FILE,
    ProjectLimitReached,
    RestProjects,
    add_project_metadata,
    create_project,
    project_name,
)

NAME = project_name("100", "101")


class InMemoryProjects:
    """A fake `Projects`: unpublished projects kept in a dict."""

    def __init__(self, names=()):
        self.projects = {name: f"id-{name}" for name in names}
        self.created: list[str] = []

    def unpublished_projects(self) -> dict[str, str]:
        return dict(self.projects)

    def create(self, name: str, zip_path: Path) -> str:
        self.created.append(name)
        self.projects[name] = f"id-{name}"
        return self.projects[name]


@pytest.fixture
def zip_path(tmp_path) -> Path:
    path = tmp_path / "101.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("scan_1/scan_1.nxs", b"nexus")
    return path


def test_project_name():
    assert NAME == "zgn-demo-100-101"


# add_project_metadata


def test_metadata_file_is_added_once(zip_path):
    references = ["https://doi.org/10.5281/zenodo.101"]
    add_project_metadata(zip_path, references)
    add_project_metadata(zip_path, references)  # a retry changes nothing

    with zipfile.ZipFile(zip_path) as archive:
        names = archive.namelist()
        content = json.loads(archive.read(METADATA_FILE))
    assert names == ["scan_1/scan_1.nxs", METADATA_FILE]
    assert content == {"references": references}


def test_other_metadata_file_is_refused(zip_path):
    with zipfile.ZipFile(zip_path, "a") as archive:
        archive.writestr(METADATA_FILE, "{}")  # e.g. written by Galaxy

    with pytest.raises(ValueError, match="already contains another"):
        add_project_metadata(zip_path, ["https://doi.org/x"])


# create_project


def test_create_new_project(zip_path):
    projects = InMemoryProjects()

    assert create_project(projects, NAME, zip_path) == (f"id-{NAME}", True)
    assert projects.created == [NAME]


def test_existing_project_is_reused(zip_path):
    projects = InMemoryProjects([NAME])

    assert create_project(projects, NAME, zip_path) == (f"id-{NAME}", False)
    assert projects.created == []


def test_limit_counts_only_demo_projects(zip_path):
    demo = [project_name("1", str(i)) for i in range(MAX_DEMO_PROJECTS - 1)]
    projects = InMemoryProjects([*demo, "my own project"])

    create_project(projects, NAME, zip_path)  # the 10th demo project

    with pytest.raises(ProjectLimitReached, match="limit 10"):
        create_project(projects, project_name("100", "102"), zip_path)
    assert projects.created == [NAME]


def test_reuse_works_at_the_limit(zip_path):
    demo = [project_name("1", str(i)) for i in range(MAX_DEMO_PROJECTS - 1)]
    projects = InMemoryProjects([*demo, NAME])

    assert create_project(projects, NAME, zip_path) == (f"id-{NAME}", False)


# RestProjects (against a local fake NOMAD server)


def test_rest_lists_own_unpublished_projects(fake_nomad):
    fake_nomad.projects = {
        "u1": {"upload_name": NAME, "published": False},
        "u2": {"upload_name": "zgn-demo-1-2", "published": True},
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
