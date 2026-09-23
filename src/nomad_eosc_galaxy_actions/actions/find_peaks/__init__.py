# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Entry point for the find-peaks Action.

Sends an XPS spectrum to Galaxy, waits for the peak-finding workflow to
finish, and links the result back into NOMAD.
"""

from nomad.actions import TaskQueue
from pydantic import Field
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from nomad.config.models.plugins import ActionEntryPoint


class FindPeaksActionEntryPoint(ActionEntryPoint):
    task_queue: str = Field(
        default=TaskQueue.CPU, description="Determines the task queue for this action"
    )
    galaxy_url: str = Field(
        default="https://usegalaxy.eu",
        description="Base URL of the Galaxy instance to use.",
    )
    galaxy_workflow_id: str = Field(
        default="",
        description=(
            'ID of the "XPS peak finding" workflow in the acting Galaxy account. '
            "Import it once with scripts/import_workflow.py -- the action never "
            "imports it itself."
        ),
    )

    def load(self):
        from nomad.actions import Action  # noqa: PLC0415

        from nomad_eosc_galaxy_actions.actions.find_peaks.activities import (  # noqa: PLC0415
            create_result_entry,
            download_galaxy_result,
            poll_galaxy_invocation,
            read_spectrum,
            run_galaxy_workflow,
        )
        from nomad_eosc_galaxy_actions.actions.find_peaks.workflows import (  # noqa: PLC0415
            FindPeaksWorkflow,
        )

        return Action(
            task_queue=self.task_queue,
            workflow=FindPeaksWorkflow,
            activities=[
                read_spectrum,
                run_galaxy_workflow,
                poll_galaxy_invocation,
                download_galaxy_result,
                create_result_entry,
            ],
        )


find_peaks_action = FindPeaksActionEntryPoint(
    name="FindPeaksAction",
    description=(
        "Sends an XPS spectrum to Galaxy for peak finding and links the "
        "result back to NOMAD (EOSC demonstrator). Currently an empty-run "
        "skeleton."
    ),
)
