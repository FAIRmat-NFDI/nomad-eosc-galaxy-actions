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
    """Registers the Action with NOMAD.

    The harvest settings are not set here but in a harvest config file, so
    that they carry a schema version (see `harvest_config.py`).
    """

    task_queue: str = Field(
        default=TaskQueue.CPU, description="Determines the task queue for this action"
    )
    harvest_config_path: str | None = Field(
        default=None,
        description=(
            "Path of this installation's harvest config file. Empty means the "
            "file shipped with the Action (config/harvest_config.json)."
        ),
    )

    def load(self):
        from nomad.actions import Action  # noqa: PLC0415

        from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import (  # noqa: PLC0415
            build_work_list,
            create_projects,
            email_report,
            fetch_manifest,
            fetch_projects,
            merge_state,
            poll_galaxy,
            prepare_run,
            run_galaxy,
            search_zenodo,
            write_report,
        )
        from nomad_eosc_galaxy_actions.actions.zenodo_harvest.workflows import (  # noqa: PLC0415
            ZenodoHarvestWorkflow,
        )

        return Action(
            task_queue=self.task_queue,
            workflow=ZenodoHarvestWorkflow,
            activities=[
                prepare_run,
                search_zenodo,
                build_work_list,
                run_galaxy,
                poll_galaxy,
                fetch_manifest,
                fetch_projects,
                create_projects,
                merge_state,
                write_report,
                email_report,
            ],
        )


zenodo_harvest_action = ZenodoHarvestActionEntryPoint(
    name="ZenodoHarvestAction",
    description=(
        "Harvests openly licensed datasets from Zenodo, converts them to NeXus "
        "on Galaxy, stores them as NOMAD projects and tracks processed files "
        "(EOSC demonstrator)."
    ),
)
