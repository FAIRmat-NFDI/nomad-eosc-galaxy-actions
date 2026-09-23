# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Workflow definition for the find-peaks action."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from nomad_eosc_galaxy_actions.actions.find_peaks.activities import (
        create_result_entry,
        download_galaxy_result,
        poll_galaxy_invocation,
        read_spectrum,
        run_galaxy_workflow,
    )
    from nomad_eosc_galaxy_actions.actions.find_peaks.models import (
        CreateResultEntryInput,
        DownloadGalaxyResultInput,
        FindPeaksWorkflowInput,
        PollGalaxyInvocationInput,
        ReadSpectrumInput,
        RunGalaxyWorkflowInput,
    )

POLL_INTERVAL = timedelta(seconds=15)
MAX_POLLS = 240  # 1 hour at the interval above


@workflow.defn
class FindPeaksWorkflow:
    @workflow.run
    async def run(self, data: FindPeaksWorkflowInput) -> dict:
        retry_policy = RetryPolicy(maximum_attempts=3)
        action_instance_id = workflow.info().workflow_id

        spectrum_path = await workflow.execute_activity(
            read_spectrum,
            ReadSpectrumInput(
                spectrum_entry_id=data.spectrum_entry_id,
                user_id=data.user_id,
                action_instance_id=action_instance_id,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        run_result = await workflow.execute_activity(
            run_galaxy_workflow,
            RunGalaxyWorkflowInput(
                galaxy_api_key=data.galaxy_api_key,
                spectrum_path=spectrum_path,
                prominence=data.prominence,
                distance=data.distance,
                height=data.height,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        poll_result = None
        for _ in range(MAX_POLLS):
            poll_result = await workflow.execute_activity(
                poll_galaxy_invocation,
                PollGalaxyInvocationInput(
                    galaxy_api_key=data.galaxy_api_key,
                    invocation_id=run_result.invocation_id,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_policy,
            )
            if poll_result.done:
                break
            await workflow.sleep(POLL_INTERVAL)
        else:
            raise ApplicationError(
                f"Timed out waiting for Galaxy invocation {run_result.invocation_id}"
            )

        if poll_result.state != "ok":
            raise ApplicationError(
                f"Galaxy workflow did not complete successfully: {poll_result.state}"
            )

        result_path = await workflow.execute_activity(
            download_galaxy_result,
            DownloadGalaxyResultInput(
                galaxy_api_key=data.galaxy_api_key,
                dataset_id=poll_result.outputs["output_nxs"],
                action_instance_id=action_instance_id,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        entry_result = await workflow.execute_activity(
            create_result_entry,
            CreateResultEntryInput(
                user_id=data.user_id,
                spectrum_entry_id=data.spectrum_entry_id,
                result_path=result_path,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        return {"entry_id": entry_result.entry_id}
