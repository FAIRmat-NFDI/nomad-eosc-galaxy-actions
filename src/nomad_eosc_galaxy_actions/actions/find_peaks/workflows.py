"""Workflow definition for the find-peaks action."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from nomad_eosc_galaxy_actions.actions.find_peaks.activities import (
        echo_spectrum_entry_id,
    )
    from nomad_eosc_galaxy_actions.actions.find_peaks.models import (
        EchoInput,
        FindPeaksWorkflowInput,
    )


@workflow.defn
class FindPeaksWorkflow:
    @workflow.run
    async def run(self, data: FindPeaksWorkflowInput) -> dict:
        retry_policy = RetryPolicy(maximum_attempts=3)
        return await workflow.execute_activity(
            echo_spectrum_entry_id,
            EchoInput(spectrum_entry_id=data.spectrum_entry_id),
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=retry_policy,
        )
