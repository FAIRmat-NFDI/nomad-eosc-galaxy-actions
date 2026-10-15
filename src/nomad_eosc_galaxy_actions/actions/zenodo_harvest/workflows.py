# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Workflow definition for the zenodo-harvest action."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import prepare_run
    from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
        PrepareRunInput,
        ZenodoHarvestWorkflowInput,
    )


@workflow.defn
class ZenodoHarvestWorkflow:
    @workflow.run
    async def run(self, data: ZenodoHarvestWorkflowInput) -> dict:
        retry_policy = RetryPolicy(maximum_attempts=3)
        action_instance_id = workflow.info().workflow_id

        prepared = await workflow.execute_activity(
            prepare_run,
            PrepareRunInput(
                action_instance_id=action_instance_id,
                # workflow.now(), not datetime.now(): workflow code must be
                # deterministic, so time comes from Temporal's replayable clock.
                started_at=workflow.now().isoformat(),
                dry_run=data.dry_run,
                max_records=data.max_records,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=retry_policy,
        )

        return {"run_dir": prepared.run_dir, "dry_run": data.dry_run}
