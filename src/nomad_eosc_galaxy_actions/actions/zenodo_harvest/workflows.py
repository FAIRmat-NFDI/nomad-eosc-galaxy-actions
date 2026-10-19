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
    from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import (
        build_work_list,
        prepare_run,
        search_zenodo,
    )
    from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
        BuildWorkListInput,
        PrepareRunInput,
        SearchZenodoInput,
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

        # Long timeout: Zenodo may ask to wait up to a minute when rate-limited.
        searched = await workflow.execute_activity(
            search_zenodo,
            SearchZenodoInput(run_dir=prepared.run_dir),
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=retry_policy,
        )

        planned = await workflow.execute_activity(
            build_work_list,
            BuildWorkListInput(
                run_dir=prepared.run_dir,
                run_id=action_instance_id,
                max_records=data.max_records,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        return {
            "run_dir": prepared.run_dir,
            "dry_run": data.dry_run,
            "candidates": searched.candidates,
            "rejected": searched.rejected,
            "work_list_records": planned.records,
            "work_list_files": planned.files,
            "skipped_files": planned.skipped,
        }
