"""Entry point for the find-peaks Action.

An empty-run skeleton: the workflow round-trips through Temporal via a
placeholder activity. Reading the spectrum's NeXus file and calling Galaxy
are added once this skeleton is confirmed working end to end.
"""

from nomad.actions import TaskQueue
from pydantic import Field
from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from nomad.config.models.plugins import ActionEntryPoint


class FindPeaksActionEntryPoint(ActionEntryPoint):
    task_queue: str = Field(
        default=TaskQueue.CPU, description='Determines the task queue for this action'
    )

    def load(self):
        from nomad.actions import Action

        from nomad_eosc_galaxy_actions.actions.find_peaks.activities import (
            echo_spectrum_entry_id,
        )
        from nomad_eosc_galaxy_actions.actions.find_peaks.workflows import (
            FindPeaksWorkflow,
        )

        return Action(
            task_queue=self.task_queue,
            workflow=FindPeaksWorkflow,
            activities=[echo_spectrum_entry_id],
        )


find_peaks_action = FindPeaksActionEntryPoint(
    name='FindPeaksAction',
    description=(
        'Sends an XPS spectrum to Galaxy for peak finding and links the '
        'result back to NOMAD (EOSC demonstrator). Currently an empty-run '
        'skeleton.'
    ),
)
