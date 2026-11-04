# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Create private NOMAD projects for harvested Zenodo records.

One project per Zenodo concept record; each record version is a folder in it,
from the zip Galaxy builds for that version. Rules for working against NOMAD
(project plan, section 6):

- every project is named `zenodo_<concept record ID>`;
- at most `MAX_DEMO_PROJECTS` unpublished harvest projects exist at any
  time, counted on the server, not locally;
- an existing unpublished project with the same name gets the new version,
  so a retry never creates a second one;
- there is no publish code: projects stay private until a person publishes
  them in the NOMAD GUI.

`RestProjects` uses the NOMAD API (https://docs.nomad-lab.eu/howto/manage/program/api.html).
Framework-agnostic: no NOMAD or Temporal imports.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

import requests

from nomad_eosc_galaxy_actions.manifest import Manifest, Results

PROJECT_PREFIX = "zenodo_"
HARVEST_PROJECT = re.compile(rf"{PROJECT_PREFIX}\d+")
# A constant on purpose: raising it needs a reviewed code change. NOMAD allows
# 10 unpublished projects per user (services.upload_limit); one stays free
# for the state project.
MAX_DEMO_PROJECTS = 9
# Seconds to wait for a project's processing before files are added to it.
PROCESSING_TIMEOUT = 600.0
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

    def create(self, name: str, zip_path: Path | None) -> str:
        """Create a project (from a zip, or empty) and return its upload ID."""

    def add(self, upload_id: str, zip_path: Path) -> None:
        """Extract a zip into an existing project and process it."""

    def read_file(self, upload_id: str, path: str) -> bytes | None:
        """A file of a project; None if it does not exist."""

    def write_file(self, upload_id: str, path: str, content: bytes) -> None:
        """Write (or replace) a file of a project."""


def project_name(concept_record_id: str) -> str:
    return f"{PROJECT_PREFIX}{concept_record_id}"


def version_folder(record_id: str, version_doi: str | None) -> str:
    """The version's folder in the project; same rule as Galaxy's report builder.

    `10.5281/zenodo.1` → `10.5281_zenodo.1`; `zenodo_<record ID>` without a DOI.
    """
    return version_doi.replace("/", "_") if version_doi else f"zenodo_{record_id}"


def merge_references(existing: bytes | None, references: list[str]) -> list[str]:
    """References of an existing `nomad.json`, then the new ones; no repeats."""
    old = json.loads(existing).get("references", []) if existing else []
    return list(dict.fromkeys([*old, *references]))


def merge_entries(
    existing: bytes | None, entries: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Entry metadata of an existing `nomad.json`; the new one wins per mainfile."""
    old = json.loads(existing).get("entries", {}) if existing else {}
    return old | entries


def add_project_metadata(
    zip_path: str | Path,
    references: list[str],
    entries: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Set `nomad.json` in the zip's root: the Zenodo references of the project
    and, per NeXus mainfile, its `external_id` and references.

    NOMAD reads `entries` only from the root file. Calling it again replaces
    the file, so a retry with merged content works.
    """
    content: dict[str, Any] = {"references": references}
    if entries:
        content["entries"] = entries
    _set_file(zip_path, METADATA_FILE, content)


def _add_file(zip_path: str | Path, name: str, content: dict[str, Any]) -> None:
    """Add a JSON file to the zip; the same content again changes nothing."""
    text = json.dumps(content)
    with zipfile.ZipFile(zip_path, "a") as archive:
        if name not in archive.namelist():
            archive.writestr(name, text)
        elif archive.read(name).decode() != text:
            raise ValueError(f"{zip_path} already contains another {name}")


def _set_file(zip_path: str | Path, name: str, content: dict[str, Any]) -> None:
    """Add a JSON file to the zip, replacing one with the same name."""
    zip_path = Path(zip_path)
    text = json.dumps(content)
    with zipfile.ZipFile(zip_path) as archive:
        if name not in archive.namelist():
            pass
        elif archive.read(name).decode() == text:
            return
        else:  # zip files cannot remove a member, so copy the others
            copy = zip_path.with_suffix(".tmp")
            with zipfile.ZipFile(copy, "w", zipfile.ZIP_DEFLATED) as target:
                for item in archive.infolist():
                    if item.filename != name:
                        target.writestr(item, archive.read(item.filename))
            shutil.move(copy, zip_path)
    with zipfile.ZipFile(zip_path, "a") as archive:
        archive.writestr(name, text)


def add_harvest_metadata(
    zip_path: str | Path, record: dict[str, Any], manifest: Manifest, results: Results
) -> None:
    """Add `<version folder>/zenodo_harvest.json`: the Zenodo record and, per
    converted raw file, its path and SHA-256 and those of its NeXus file.

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
    folder = version_folder(record["record_id"], record.get("version_doi"))
    _add_file(zip_path, f"{folder}/{HARVEST_METADATA_FILE}", content)


def create_project(
    projects: Projects,
    name: str,
    zip_path: Path,
    references: list[str],
    entries: dict[str, dict[str, Any]] | None = None,
) -> tuple[str, bool]:
    """Create the project, or add the zip to an unpublished one with the same name.

    `nomad.json` gets the references and entry metadata of the existing
    project plus `references` and `entries`.
    Returns the upload ID and whether a new project was created.

    Raises:
        ProjectLimitReached: no new demo project is allowed.
    """
    existing = projects.unpublished_projects()
    if name in existing:
        upload_id = existing[name]
        old = projects.read_file(upload_id, METADATA_FILE)
        add_project_metadata(
            zip_path,
            merge_references(old, references),
            merge_entries(old, entries or {}),
        )
        projects.add(upload_id, zip_path)
        return upload_id, False
    demo = [n for n in existing if HARVEST_PROJECT.fullmatch(n)]
    if len(demo) >= MAX_DEMO_PROJECTS:
        raise ProjectLimitReached(
            f"{len(demo)} unpublished {PROJECT_PREFIX}* projects exist "
            f"(limit {MAX_DEMO_PROJECTS}); publish or delete some first"
        )
    add_project_metadata(zip_path, references, entries)
    return projects.create(name, zip_path), True


def wait_until_idle(is_processing, timeout: float = PROCESSING_TIMEOUT) -> None:
    """Wait until `is_processing()` is False; NOMAD refuses changes meanwhile.

    Raises:
        TimeoutError: still processing after `timeout` seconds.
    """
    deadline = time.monotonic() + timeout
    while is_processing():
        if time.monotonic() > deadline:
            raise TimeoutError(f"project still processing after {timeout:.0f} s")
        time.sleep(2)


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

    def create(self, name: str, zip_path: Path | None) -> str:
        params = {"upload_name": name}
        if zip_path is None:
            response = self._session.post(
                f"{self._url}/uploads", params=params, timeout=self._timeout
            )
        else:
            with open(zip_path, "rb") as content:
                response = self._session.post(
                    f"{self._url}/uploads",
                    params={**params, "file_name": Path(zip_path).name},
                    headers={"Accept": "application/json"},
                    data=content,
                    timeout=self._timeout,
                )
        response.raise_for_status()
        return response.json()["upload_id"]

    def _processing(self, upload_id: str) -> bool:
        response = self._session.get(
            f"{self._url}/uploads/{upload_id}", timeout=self._timeout
        )
        response.raise_for_status()
        return bool(response.json()["data"].get("process_running"))

    def _put(self, upload_id: str, folder: str, name: str, content) -> None:
        wait_until_idle(lambda: self._processing(upload_id))
        response = self._session.put(
            f"{self._url}/uploads/{upload_id}/raw/{folder}",
            params={"file_name": name},
            headers={"Accept": "application/json"},
            data=content,
            timeout=self._timeout,
        )
        response.raise_for_status()

    def add(self, upload_id: str, zip_path: Path) -> None:
        with open(zip_path, "rb") as content:
            self._put(upload_id, "", Path(zip_path).name, content)

    def read_file(self, upload_id: str, path: str) -> bytes | None:
        response = self._session.get(
            f"{self._url}/uploads/{upload_id}/raw/{path}", timeout=self._timeout
        )
        if response.status_code == 404:  # noqa: PLR2004
            return None
        response.raise_for_status()
        return response.content

    def write_file(self, upload_id: str, path: str, content: bytes) -> None:
        target = PurePosixPath(path)
        folder = "" if str(target.parent) == "." else str(target.parent)
        self._put(upload_id, folder, target.name, content)


def write_temp_file(name: str, content: bytes) -> Path:
    """`content` as a file called `name` in a new temporary folder."""
    path = Path(tempfile.mkdtemp()) / name
    path.write_bytes(content)
    return path
