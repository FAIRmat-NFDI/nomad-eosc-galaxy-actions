# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Create private NOMAD projects for harvested Zenodo record versions.

One project per record version, from the zip Galaxy builds for it. Rules
for working against NOMAD (project plan, section 6):

- every project is named `zgn-demo-<concept record ID>-<record ID>`;
- at most `MAX_DEMO_PROJECTS` unpublished `zgn-demo-` projects exist at any
  time, counted on the server, not locally;
- an existing unpublished project with the same name is reused, so a retry
  never creates a second one;
- there is no publish code: projects stay private until a person publishes
  them in the NOMAD GUI.

`RestProjects` uses the NOMAD API (https://docs.nomad-lab.eu/howto/manage/program/api.html).
Framework-agnostic: no NOMAD or Temporal imports.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Protocol

import requests

from nomad_eosc_galaxy_actions.manifest import Manifest, Results

PROJECT_PREFIX = "zgn-demo-"
# A constant on purpose: raising it needs a reviewed code change. Also NOMAD's
# own limit of unpublished projects per user (services.upload_limit).
MAX_DEMO_PROJECTS = 10
# Upload metadata file NOMAD reads from the project root
# (https://docs.nomad-lab.eu/howto/manage/gui/upload.html).
METADATA_FILE = "nomad.json"
# The harvest's own record of each project: Zenodo record and file checksums.
# NOMAD's project metadata has no field for it, so it is a file in the project.
HARVEST_METADATA_FILE = "zenodo_harvest.json"
HARVEST_METADATA_SCHEMA_VERSION = 1


class ProjectLimitReached(RuntimeError):
    """`MAX_DEMO_PROJECTS` unpublished demo projects already exist."""


class Projects(Protocol):
    """Where projects are created: a NOMAD over its API, or the local one."""

    def unpublished_projects(self) -> dict[str, str]:
        """Names → upload IDs of the user's own unpublished projects."""

    def create(self, name: str, zip_path: Path) -> str:
        """Create a project from a zip and start processing; return its upload ID."""


def project_name(concept_record_id: str, record_id: str) -> str:
    return f"{PROJECT_PREFIX}{concept_record_id}-{record_id}"


def add_project_metadata(zip_path: str | Path, references: list[str]) -> None:
    """Add `nomad.json` with the Zenodo references to the zip's root.

    Calling it again with the same references changes nothing (retry-safe).

    Raises:
        ValueError: the zip already has another `nomad.json` (Galaxy must not
            add one).
    """
    _add_file(zip_path, METADATA_FILE, {"references": references})


def _add_file(zip_path: str | Path, name: str, content: dict[str, Any]) -> None:
    """Add a JSON file to the zip's root; the same content again changes nothing."""
    text = json.dumps(content)
    with zipfile.ZipFile(zip_path, "a") as archive:
        if name not in archive.namelist():
            archive.writestr(name, text)
        elif archive.read(name).decode() != text:
            raise ValueError(f"{zip_path} already contains another {name}")


def add_harvest_metadata(
    zip_path: str | Path, record: dict[str, Any], manifest: Manifest, results: Results
) -> None:
    """Add `zenodo_harvest.json`: the Zenodo record and, per converted raw file,
    its path and SHA-256 and those of its NeXus file.

    Raises:
        ValueError: the zip already has another `zenodo_harvest.json`.
    """
    by_sha256 = {result.sha256: result for result in results.files}
    files = []
    with zipfile.ZipFile(zip_path) as archive:
        members = set(archive.namelist())
        for file in sorted(manifest.files, key=lambda f: f.path):
            if (file.record_id, file.role, file.action) != (
                record["record_id"],
                "raw",
                "process",
            ):
                continue
            nexus_path = getattr(by_sha256.get(file.sha256), "nexus_path", None)
            nexus_sha256 = (
                hashlib.sha256(archive.read(nexus_path)).hexdigest()
                if nexus_path in members
                else None
            )
            files.append(
                {
                    "raw_path": file.path,
                    "raw_sha256": file.sha256,
                    "nexus_path": nexus_path if nexus_sha256 else None,
                    "nexus_sha256": nexus_sha256,
                }
            )
    zenodo = {
        key: record.get(key)
        for key in ("record_id", "concept_record_id", "version_doi", "concept_doi")
    }
    content = {
        "schema_version": HARVEST_METADATA_SCHEMA_VERSION,
        "zenodo": zenodo,
        "files": files,
    }
    _add_file(zip_path, HARVEST_METADATA_FILE, content)


def create_project(projects: Projects, name: str, zip_path: Path) -> tuple[str, bool]:
    """Create the project, or reuse an unpublished one with the same name.

    Returns the upload ID and whether a new project was created.

    Raises:
        ProjectLimitReached: no new demo project is allowed.
    """
    existing = projects.unpublished_projects()
    if name in existing:
        return existing[name], False
    demo = [n for n in existing if n.startswith(PROJECT_PREFIX)]
    if len(demo) >= MAX_DEMO_PROJECTS:
        raise ProjectLimitReached(
            f"{len(demo)} unpublished {PROJECT_PREFIX}* projects exist "
            f"(limit {MAX_DEMO_PROJECTS}); publish or delete some first"
        )
    return projects.create(name, zip_path), True


class RestProjects:
    """Projects of the PAT's user in a NOMAD, through its API."""

    def __init__(self, nomad_url: str, pat: str, timeout: float = 300.0):
        self._url = nomad_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {pat}"

    def unpublished_projects(self) -> dict[str, str]:
        # NOMAD allows only a few unpublished projects per user, so one page
        # holds them all.
        response = self._session.get(
            f"{self._url}/uploads",
            params={"is_published": "false", "roles": "main_author", "page_size": 100},
            timeout=self._timeout,
        )
        response.raise_for_status()
        return {
            upload["upload_name"]: upload["upload_id"]
            for upload in response.json().get("data", [])
            if upload.get("upload_name")
        }

    def create(self, name: str, zip_path: Path) -> str:
        with open(zip_path, "rb") as content:
            response = self._session.post(
                f"{self._url}/uploads",
                params={"upload_name": name, "file_name": Path(zip_path).name},
                headers={"Accept": "application/json"},
                data=content,
                timeout=self._timeout,
            )
        response.raise_for_status()
        return response.json()["upload_id"]
