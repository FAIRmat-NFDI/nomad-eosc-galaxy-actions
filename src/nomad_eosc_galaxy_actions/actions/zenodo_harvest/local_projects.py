# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Projects in the NOMAD that runs the Action, without the API or a PAT.

The fallback when the harvest config has no `nomad_url`. Does what NOMAD's
`POST /uploads` endpoint does, with NOMAD's own Python API. Needs a running
NOMAD (MongoDB, file storage), so it is not covered by the unit tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


class LocalProjects:
    """Projects of one NOMAD user in the local NOMAD."""

    def __init__(self, user_id: str):
        self._user_id = user_id

    def unpublished_projects(self) -> dict[str, str]:
        from nomad.processing import Upload  # noqa: PLC0415

        uploads = Upload.objects(main_author=self._user_id, publish_time=None)
        return {u.upload_name: u.upload_id for u in uploads if u.upload_name}

    def create(self, name: str, zip_path: Path) -> str:
        from nomad import datamodel, files  # noqa: PLC0415
        from nomad.processing import Upload  # noqa: PLC0415

        upload = Upload.create(
            main_author=datamodel.User.get(user_id=self._user_id),
            upload_name=name,
            upload_create_time=datetime.now(timezone.utc),
        )
        files.StagingUploadFiles(upload_id=upload.upload_id, create=True)
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
        return upload.upload_id
