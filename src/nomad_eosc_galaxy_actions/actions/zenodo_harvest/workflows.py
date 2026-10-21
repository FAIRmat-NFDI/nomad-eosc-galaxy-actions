# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Workflow definition for the zenodo-harvest action."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import (
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
    from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
        BuildWorkListInput,
        BuildWorkListResult,
        CreateProjectsInput,
        CreateProjectsResult,
        EmailReportInput,
        FetchManifestInput,
        FetchManifestResult,
        FetchProjectsInput,
        MergeStateInput,
        PollGalaxyInput,
        PrepareRunInput,
        RunGalaxyInput,
        SearchZenodoInput,
        WriteReportInput,
        ZenodoHarvestWorkflowInput,
    )

# Galaxy may need hours for large archives; check once a minute for a day.
GALAXY_POLL_INTERVAL = timedelta(minutes=1)
GALAXY_MAX_POLLS = 24 * 60
# Names of the Galaxy workflow outputs; the .ga file must use these labels.
GALAXY_MANIFEST_OUTPUT = "manifest"
GALAXY_RESULTS_OUTPUT = "results"
GALAXY_PROJECTS_OUTPUT = "projects"


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

        galaxy, fetched, collection_id = await self._run_galaxy(
            prepared.run_dir, planned, action_instance_id, retry_policy
        )
        skip = _skip_reason(fetched, data.dry_run)
        if skip:
            nomad = merge = {"status": "skipped", "reason": skip}
        else:
            nomad, projects = await self._create_projects(
                prepared.run_dir, planned, data.user_id, collection_id, retry_policy
            )
            merge = await self._merge_state(fetched, projects, retry_policy)

        steps = {"galaxy": galaxy, "nomad": nomad, "merge": merge}
        report, email = await self._report(
            prepared.run_dir, steps, data.user_id, retry_policy
        )
        return {
            "run_dir": prepared.run_dir,
            "dry_run": data.dry_run,
            "candidates": searched.candidates,
            "rejected": searched.rejected,
            "work_list_records": planned.records,
            "work_list_files": planned.files,
            "skipped_files": planned.skipped,
            "galaxy": galaxy,
            "nomad": nomad,
            "merge": merge,
            "report_path": report,
            "email": email,
        }

    async def _run_galaxy(
        self,
        run_dir: str,
        planned: BuildWorkListResult,
        run_id: str,
        retry_policy: RetryPolicy,
    ) -> tuple[dict, FetchManifestResult | None, str | None]:
        """Send the work list to Galaxy, wait for it and fetch the manifest.

        Returns the step's summary, the fetched files and the ID of the
        `projects` collection.
        """
        if planned.records == 0:
            return {"status": "skipped", "reason": "no_work"}, None, None
        if not planned.galaxy_configured:
            skipped = {"status": "skipped", "reason": "galaxy_not_configured"}
            return skipped, None, None

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
        if not fetched.placeholder:
            # Checked before anything is written to NOMAD or the state.
            for name in (GALAXY_RESULTS_OUTPUT, GALAXY_PROJECTS_OUTPUT):
                if name not in polled.outputs:
                    raise ApplicationError(
                        f"Galaxy workflow has no output named {name!r}"
                    )
        galaxy = {
            "status": "ok",
            "history_id": started.history_id,
            "manifest_path": fetched.manifest_path,
            "placeholder": fetched.placeholder,
        }
        return galaxy, fetched, polled.outputs.get(GALAXY_PROJECTS_OUTPUT)

    async def _create_projects(
        self,
        run_dir: str,
        planned: BuildWorkListResult,
        user_id: str,
        collection_id: str,
        retry_policy: RetryPolicy,
    ) -> tuple[dict, CreateProjectsResult]:
        """Download Galaxy's project zips and create one NOMAD project each."""
        downloaded = await workflow.execute_activity(
            fetch_projects,
            FetchProjectsInput(run_dir=run_dir, collection_id=collection_id),
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=retry_policy,
        )
        projects = await workflow.execute_activity(
            create_projects,
            CreateProjectsInput(
                work_list_path=planned.work_list_path,
                user_id=user_id,
                zip_paths=downloaded.zip_paths,
            ),
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=retry_policy,
        )
        nomad = {
            "status": "ok",
            "upload_ids": projects.upload_ids,
            "created": projects.created,
            "left_out": projects.left_out,
        }
        return nomad, projects

    async def _merge_state(
        self,
        fetched: FetchManifestResult,
        projects: CreateProjectsResult,
        retry_policy: RetryPolicy,
    ) -> dict:
        """Write Galaxy's outcome and the projects into the state of all runs."""
        merged = await workflow.execute_activity(
            merge_state,
            MergeStateInput(
                manifest_path=fetched.manifest_path,
                results_path=fetched.results_path,
                upload_ids=projects.upload_ids,
                left_out=list(projects.left_out),
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )
        return {"status": "ok", **merged.model_dump()}

    async def _report(
        self, run_dir: str, steps: dict, user_id: str, retry_policy: RetryPolicy
    ) -> tuple[str, dict]:
        """Write the report and email it; a failed email does not fail the run."""
        written = await workflow.execute_activity(
            write_report,
            WriteReportInput(run_dir=run_dir, steps=steps),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )
        try:
            emailed = await workflow.execute_activity(
                email_report,
                EmailReportInput(
                    report_path=written.report_path,
                    markdown_path=written.markdown_path,
                    user_id=user_id,
                ),
                start_to_close_timeout=timedelta(minutes=5),
                retry_policy=retry_policy,
            )
        except ActivityError as error:
            return written.report_path, {"status": "failed", "error": str(error.cause)}
        if not emailed.sent:
            return written.report_path, {"status": "skipped", "reason": emailed.reason}
        return written.report_path, {"status": "ok"}


def _skip_reason(fetched: FetchManifestResult | None, dry_run: bool) -> str | None:
    """Why no NOMAD projects are created and the state stays unchanged.

    A dry run changes neither, so a real run still handles the same files.
    """
    if fetched is None:
        return "no_galaxy_run"
    if fetched.placeholder:
        return "placeholder"
    if dry_run:
        return "dry_run"
    return None
