# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Workflow definition for the zenodo-harvest action."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError

with workflow.unsafe.imports_passed_through():
    from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import (
        build_work_list,
        fetch_manifest,
        merge_state,
        poll_galaxy,
        prepare_run,
        run_galaxy,
        search_zenodo,
    )
    from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
        BuildWorkListInput,
        BuildWorkListResult,
        FetchManifestInput,
        FetchManifestResult,
        MergeStateInput,
        PollGalaxyInput,
        PrepareRunInput,
        RunGalaxyInput,
        SearchZenodoInput,
        ZenodoHarvestWorkflowInput,
    )

# Galaxy may need hours for large archives; check once a minute for a day.
GALAXY_POLL_INTERVAL = timedelta(minutes=1)
GALAXY_MAX_POLLS = 24 * 60
# Names of the Galaxy workflow outputs; the .ga file must use these labels.
GALAXY_MANIFEST_OUTPUT = "manifest"
GALAXY_RESULTS_OUTPUT = "results"


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

        galaxy, fetched = await self._run_galaxy(
            prepared.run_dir, planned, action_instance_id, retry_policy
        )
        merge = await self._merge_state(fetched, data.dry_run, retry_policy)

        return {
            "run_dir": prepared.run_dir,
            "dry_run": data.dry_run,
            "candidates": searched.candidates,
            "rejected": searched.rejected,
            "work_list_records": planned.records,
            "work_list_files": planned.files,
            "skipped_files": planned.skipped,
            "galaxy": galaxy,
            "merge": merge,
        }

    async def _run_galaxy(
        self,
        run_dir: str,
        planned: BuildWorkListResult,
        run_id: str,
        retry_policy: RetryPolicy,
    ) -> tuple[dict, FetchManifestResult | None]:
        """Send the work list to Galaxy, wait for it and fetch its outputs."""
        if planned.records == 0:
            return {"status": "skipped", "reason": "no_work"}, None
        if not planned.galaxy_configured:
            return {"status": "skipped", "reason": "galaxy_not_configured"}, None

        started = await workflow.execute_activity(
            run_galaxy,
            RunGalaxyInput(work_list_path=planned.work_list_path, run_id=run_id),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )
        for _ in range(GALAXY_MAX_POLLS):
            polled = await workflow.execute_activity(
                poll_galaxy,
                PollGalaxyInput(invocation_id=started.invocation_id),
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=retry_policy,
            )
            if polled.done:
                break
            await workflow.sleep(GALAXY_POLL_INTERVAL)
        else:
            raise ApplicationError(
                f"Timed out waiting for Galaxy invocation {started.invocation_id}"
            )
        if polled.state != "ok":
            raise ApplicationError(
                f"Galaxy invocation {started.invocation_id} ended in state "
                f"{polled.state!r}; see history {started.history_id}"
            )
        if GALAXY_MANIFEST_OUTPUT not in polled.outputs:
            raise ApplicationError(
                f"Galaxy workflow has no output named {GALAXY_MANIFEST_OUTPUT!r}"
            )

        fetched = await workflow.execute_activity(
            fetch_manifest,
            FetchManifestInput(
                run_dir=run_dir,
                work_list_path=planned.work_list_path,
                manifest_dataset_id=polled.outputs[GALAXY_MANIFEST_OUTPUT],
                results_dataset_id=polled.outputs.get(GALAXY_RESULTS_OUTPUT),
            ),
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=retry_policy,
        )
        galaxy = {
            "status": "ok",
            "history_id": started.history_id,
            "manifest_path": fetched.manifest_path,
            "placeholder": fetched.placeholder,
        }
        return galaxy, fetched

    async def _merge_state(
        self,
        fetched: FetchManifestResult | None,
        dry_run: bool,
        retry_policy: RetryPolicy,
    ) -> dict:
        """Write Galaxy's outcome into the state shared by all runs.

        A dry run leaves the state unchanged, so a real run still handles
        the same files. F6 will create the NOMAD projects before this step.
        """
        if fetched is None:
            return {"status": "skipped", "reason": "no_galaxy_run"}
        if fetched.placeholder:
            return {"status": "skipped", "reason": "placeholder"}
        if fetched.results_path is None:
            raise ApplicationError(
                f"Galaxy workflow has no output named {GALAXY_RESULTS_OUTPUT!r}"
            )
        if dry_run:
            return {"status": "skipped", "reason": "dry_run"}
        merged = await workflow.execute_activity(
            merge_state,
            MergeStateInput(
                manifest_path=fetched.manifest_path,
                results_path=fetched.results_path,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )
        return {"status": "ok", **merged.model_dump()}
