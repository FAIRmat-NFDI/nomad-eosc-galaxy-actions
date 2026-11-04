# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Projects and entry search in the NOMAD that runs the Action, without the
API or a PAT.

The fallback when the harvest config has no `nomad_url`. Does what NOMAD's
`POST /uploads`, `PUT /uploads/{id}/raw` and `POST /entries/query` endpoints
do, with NOMAD's own Python API. Needs a running NOMAD (MongoDB, file storage,
Elasticsearch), so it is not covered by the unit tests.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from nomad_eosc_galaxy_actions.nomad_entries import (
    LOOKUP_FIELDS,
    LOOKUP_PAGE_SIZE,
    chunks,
)
from nomad_eosc_galaxy_actions.nomad_projects import wait_until_idle, write_temp_file


class LocalProjects:
    """Projects of one NOMAD user in the local NOMAD."""

    def __init__(self, user_id: str):
        self._user_id = user_id

    def unpublished_projects(self) -> dict[str, str]:
        from nomad.processing import Upload  # noqa: PLC0415

        uploads = Upload.objects(main_author=self._user_id, publish_time=None)
        return {u.upload_name: u.upload_id for u in uploads if u.upload_name}

    def create(self, name: str, zip_path: Path | None) -> str:
        from nomad import datamodel, files  # noqa: PLC0415
        from nomad.processing import Upload  # noqa: PLC0415

        upload = Upload.create(
            main_author=datamodel.User.get(user_id=self._user_id),
            upload_name=name,
            upload_create_time=datetime.now(timezone.utc),
        )
        files.StagingUploadFiles(upload_id=upload.upload_id, create=True)
        if zip_path is not None:
            self.add(upload.upload_id, zip_path)
        return upload.upload_id

    def add(self, upload_id: str, zip_path: Path) -> None:
        upload = _upload(upload_id)
        wait_until_idle(lambda: _busy(upload))
        # Same file operation as the API: NOMAD copies and extracts the zip.
        upload.process_upload(
            [
                {
                    "op": "ADD",
                    "path": str(zip_path),
                    "target_dir": "",
                    "temporary": False,
                    "auto_decompress": True,
                }
            ]
        )

    def read_file(self, upload_id: str, path: str) -> bytes | None:
        from nomad import files  # noqa: PLC0415

        staging = files.StagingUploadFiles(upload_id=upload_id)
        if not staging.raw_path_exists(path):
            return None
        with staging.raw_file(path, "rb") as handle:
            return handle.read()

    def write_file(self, upload_id: str, path: str, content: bytes) -> None:
        """Write the file without processing: harvest data, not an entry."""
        from nomad import files  # noqa: PLC0415

        target = PurePosixPath(path)
        upload = _upload(upload_id)
        wait_until_idle(lambda: _busy(upload))
        source = write_temp_file(target.name, content)
        try:
            files.StagingUploadFiles(upload_id=upload_id).add_rawfiles(
                str(source),
                "" if str(target.parent) == "." else str(target.parent),
                auto_decompress=False,
            )
        finally:
            shutil.rmtree(source.parent, ignore_errors=True)


def _upload(upload_id: str):
    from nomad.processing import Upload  # noqa: PLC0415

    return Upload.get(upload_id)


def _busy(upload) -> bool:
    """Processing, or reserved by a processing workflow that has not started yet."""
    upload.reload()
    return bool(upload.process_running or upload.workflow_ids)


class LocalLookup:
    """Entry search of one user in the local NOMAD; same query as `RestLookup`."""

    def __init__(self, user_id: str):
        self._user_id = user_id

    def entries(self, references: list[str]) -> Iterator[dict[str, Any]]:
        from nomad.app.v1.models import MetadataPagination, MetadataRequired  # noqa: PLC0415
        from nomad.search import search  # noqa: PLC0415

        for chunk in chunks(sorted(set(references))):
            after = None
            while True:
                response = search(
                    owner="visible",
                    query={"references:any": chunk},
                    pagination=MetadataPagination(
                        page_size=LOOKUP_PAGE_SIZE, page_after_value=after
                    ),
                    required=MetadataRequired(include=LOOKUP_FIELDS),
                    user_id=self._user_id,
                )
                yield from response.data
                after = response.pagination.next_page_after_value
                if not after:
                    break
