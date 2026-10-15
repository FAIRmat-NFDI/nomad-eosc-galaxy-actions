# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Entry point for the zenodo-harvest Action.

Finds openly licensed datasets on Zenodo, sends a work list to a Galaxy
workflow that downloads, extracts and converts them to NeXus, and tracks
what has been processed so that every weekly run is incremental. See the
"Work list, manifest and tracking" section of the project plan for the data
contract between NOMAD and Galaxy.
"""

from nomad.actions import TaskQueue
from pydantic import Field
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from nomad.config.models.plugins import ActionEntryPoint


class ZenodoHarvestActionEntryPoint(ActionEntryPoint):
    """Deployment configuration, set per NOMAD installation in `nomad.yaml`.

    Everything that describes *what* to harvest lives here rather than in the
    trigger form, so that scheduled runs and manual runs behave identically.
    """

    task_queue: str = Field(
        default=TaskQueue.CPU, description="Determines the task queue for this action"
    )
    zenodo_url: str = Field(
        default="https://zenodo.org",
        description="Base URL of the Zenodo (InvenioRDM) instance to search.",
    )
    zenodo_query: str = Field(
        default=(
            '("scanning tunneling microscopy" OR STM) AND (Nanonis OR ".sxm") '
            "AND resource_type.type:dataset"
        ),
        description="Zenodo search query (Elasticsearch query string syntax).",
    )
    # TODO: Add all the file extensions once we test for .sxm files in the development workflow.
    raw_extensions: list[str] = Field(
        default=[".sxm"],
        description="File extensions converted to NeXus (role 'raw').",
    )
    context_extensions: list[str] = Field(
        default=[
            ".txt",
            ".md",
            ".pdf",
            ".rtf",
            ".csv",
            ".json",
            ".yaml",
            ".yml",
            ".docx",
        ],
        description=(
            "File extensions that describe the experiment (role 'context'); "
            "passed along with every raw file of the same record."
        ),
    )
    ignore_patterns: list[str] = Field(
        default=["__MACOSX/*", "*.DS_Store", "*.mp4"],
        description="Glob patterns of files that are neither raw nor context.",
    )
    licenses: list[str] = Field(
        default=["cc-zero", "cc-by-4.0"],
        description=(
            "Zenodo licence IDs (as in the records API's metadata.license.id, "
            "e.g. 'cc-zero' for CC0) that allow re-publication in NOMAD."
        ),
    )
    max_archive_bytes: int = Field(
        default=2_000_000_000,
        description="Archives larger than this are skipped (reported as too_large).",
    )
    max_uncompressed_bytes: int = Field(
        default=10_000_000_000,
        description="Upper limit for the extracted size of one archive (zip bombs).",
    )
    galaxy_url: str = Field(
        default="https://usegalaxy.eu",
        description="Base URL of the Galaxy instance to use.",
    )
    galaxy_workflow_id: str = Field(
        default="",
        description=(
            "ID of the harvest workflow in the acting Galaxy account. Until the "
            "real tools exist, this points to a placeholder workflow."
        ),
    )

    def load(self):
        from nomad.actions import Action  # noqa: PLC0415

        from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import (  # noqa: PLC0415
            prepare_run,
        )
        from nomad_eosc_galaxy_actions.actions.zenodo_harvest.workflows import (  # noqa: PLC0415
            ZenodoHarvestWorkflow,
        )

        return Action(
            task_queue=self.task_queue,
            workflow=ZenodoHarvestWorkflow,
            activities=[prepare_run],
        )


zenodo_harvest_action = ZenodoHarvestActionEntryPoint(
    name="ZenodoHarvestAction",
    description=(
        "Harvests openly licensed datasets from Zenodo, converts them to NeXus "
        "on Galaxy and tracks processed files (EOSC demonstrator)."
    ),
)
