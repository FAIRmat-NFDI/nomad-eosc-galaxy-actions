# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Workflow definition for the zenodo-harvest action."""

import asyncio
import contextlib
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, CancelledError

with workflow.unsafe.imports_passed_through():
    from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import (
        build_work_list,
        create_projects,
        email_report,
        fetch_manifest,
        fetch_projects,
        finish_run,
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
        FinishRunInput,
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
    def __init__(self) -> None:
        self._step = ""  # the running step, named in the report of a failed run

    @workflow.run
    async def run(self, data: ZenodoHarvestWorkflowInput) -> dict:
        """One harvest run, or one run of a repeated series.

        A repeated series waits for the next start and then continues as new:
        the same Action instance restarts with an empty event history, so
        weekly runs never reach Temporal's history size limit.
        """
        # workflow.now(), not datetime.now(): workflow code must be
        # deterministic, so time comes from Temporal's replayable clock.
        started = workflow.now()
        if not data.repeat_every_days:
            return await self._harvest(data, workflow.info().workflow_id, None)

        number = data.run_number
        folder = f"run-{number:03d}"
        result = await self._harvest(
            data, f"{workflow.info().workflow_id}-{folder}", folder
        )
        if data.max_runs and number >= data.max_runs:
            return result | {"run_number": number}
        wait = started + timedelta(days=data.repeat_every_days) - workflow.now()
        if wait > timedelta(0):  # a run longer than the interval starts at once
            await workflow.sleep(wait)
        # One argument only: Temporal decodes it as the input model only when
        # the number of arguments matches run()'s parameters.
        workflow.continue_as_new(data.model_copy(update={"run_number": number + 1}))

    async def _harvest(
        self, data: ZenodoHarvestWorkflowInput, run_id: str, run_folder: str | None
    ) -> dict:
        retry_policy = RetryPolicy(maximum_attempts=3)
        try:
            return await self._prepare_and_run(data, run_id, run_folder, retry_policy)
        finally:
            # Also after a failure or Stop, so the user can start the next run.
            await workflow.execute_activity(
                finish_run,
                FinishRunInput(user_id=data.user_id, run_id=run_id),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=retry_policy,
            )

    async def _prepare_and_run(
        self,
        data: ZenodoHarvestWorkflowInput,
        run_id: str,
        run_folder: str | None,
        retry_policy: RetryPolicy,
    ) -> dict:
        prepare = workflow.start_activity(
            prepare_run,
            PrepareRunInput(
                action_instance_id=workflow.info().workflow_id,
                user_id=data.user_id,
                run_id=run_id,
                run_folder=run_folder,
                started_at=workflow.now().isoformat(),
                dry_run=data.dry_run,
                max_records=data.max_records,
            ),
            start_to_close_timeout=timedelta(minutes=1),
            retry_policy=retry_policy,
        )
        try:
            prepared = await asyncio.shield(prepare)
        except asyncio.CancelledError:
            # Stop: let it end first, so finish_run removes the mark it writes.
            with contextlib.suppress(ActivityError):
                await prepare
            raise
        steps: dict = {}
        try:
            return await self._steps(
                data, run_id, prepared.run_dir, steps, retry_policy
            )
        except (ActivityError, ApplicationError) as error:
            if _is_stop(error):
                raise
            # Report the failure, then fail the run as before.
            steps["failure"] = {"step": self._step, "error": _error_message(error)}
            try:
                await self._report(prepared.run_dir, steps, data.user_id, retry_policy)
            except ActivityError as report_error:
                workflow.logger.warning(
                    "No report for the failed run: %s", _error_message(report_error)
                )
            raise

    async def _steps(
        self,
        data: ZenodoHarvestWorkflowInput,
        run_id: str,
        run_dir: str,
        steps: dict,
        retry_policy: RetryPolicy,
    ) -> dict:
        """The run's steps; `steps` collects their summaries for the report."""
        # Long timeout: Zenodo may ask to wait up to a minute when rate-limited.
        self._step = "zenodo_search"
        searched = await workflow.execute_activity(
            search_zenodo,
            SearchZenodoInput(run_dir=run_dir, zenodo_token=data.zenodo_token),
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=retry_policy,
        )

        self._step = "work_list"
        planned = await workflow.execute_activity(
            build_work_list,
            BuildWorkListInput(
                run_dir=run_dir,
                user_id=data.user_id,
                run_id=run_id,
                max_records=data.max_records,
                allow_duplicate_projects=data.allow_duplicate_projects,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )

        self._step = "galaxy"
        galaxy, fetched, collection_id = await self._run_galaxy(
            run_dir, planned, run_id, data, retry_policy
        )
        steps["galaxy"] = galaxy
        skip = _skip_reason(fetched, data.dry_run)
        if skip:
            nomad = merge = {"status": "skipped", "reason": skip}
        else:
            self._step = "nomad"
            nomad, projects = await self._create_projects(
                run_dir,
                planned,
                fetched=fetched,
                data=data,
                collection_id=collection_id,
                retry_policy=retry_policy,
            )
            steps["nomad"] = nomad
            self._step = "merge"
            merge = await self._merge_state(
                fetched, projects, data.user_id, retry_policy
            )
        steps |= {"nomad": nomad, "merge": merge}

        self._step = "report"
        report, email = await self._report(run_dir, steps, data.user_id, retry_policy)
        return {
            "run_id": run_id,
            "run_dir": run_dir,
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

    async def _run_galaxy(  # noqa: PLR0913
        self,
        run_dir: str,
        planned: BuildWorkListResult,
        run_id: str,
        data: ZenodoHarvestWorkflowInput,
        retry_policy: RetryPolicy,
    ) -> tuple[dict, FetchManifestResult | None, str | None]:
        """Send the work list to Galaxy, wait for it and fetch the manifest.

        Returns the step's summary, the fetched files and the ID of the
        `projects` collection.
        """
        galaxy_api_key = data.galaxy_api_key
        if planned.records == 0:
            return {"status": "skipped", "reason": "no_work"}, None, None
        if not planned.galaxy_configured:
            skipped = {"status": "skipped", "reason": "galaxy_not_configured"}
            return skipped, None, None

        started = await workflow.execute_activity(
            run_galaxy,
            RunGalaxyInput(
                work_list_path=planned.work_list_path,
                run_id=run_id,
                galaxy_api_key=galaxy_api_key,
            ),
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=retry_policy,
        )
        for _ in range(GALAXY_MAX_POLLS):
            polled = await workflow.execute_activity(
                poll_galaxy,
                PollGalaxyInput(
                    invocation_id=started.invocation_id, galaxy_api_key=galaxy_api_key
                ),
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
                user_id=data.user_id,
                work_list_path=planned.work_list_path,
                manifest_dataset_id=polled.outputs[GALAXY_MANIFEST_OUTPUT],
                results_dataset_id=polled.outputs.get(GALAXY_RESULTS_OUTPUT),
                galaxy_api_key=galaxy_api_key,
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

    async def _create_projects(  # noqa: PLR0913
        self,
        run_dir: str,
        planned: BuildWorkListResult,
        *,
        fetched: FetchManifestResult,
        data: ZenodoHarvestWorkflowInput,
        collection_id: str,
        retry_policy: RetryPolicy,
    ) -> tuple[dict, CreateProjectsResult]:
        """Download Galaxy's project zips and create one NOMAD project each."""
        downloaded = await workflow.execute_activity(
            fetch_projects,
            FetchProjectsInput(
                run_dir=run_dir,
                collection_id=collection_id,
                galaxy_api_key=data.galaxy_api_key,
            ),
            start_to_close_timeout=timedelta(hours=2),
            retry_policy=retry_policy,
        )
        projects = await workflow.execute_activity(
            create_projects,
            CreateProjectsInput(
                work_list_path=planned.work_list_path,
                manifest_path=fetched.manifest_path,
                results_path=fetched.results_path,
                user_id=data.user_id,
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
        user_id: str,
        retry_policy: RetryPolicy,
    ) -> dict:
        """Write Galaxy's outcome and the projects into the user's state."""
        merged = await workflow.execute_activity(
            merge_state,
            MergeStateInput(
                manifest_path=fetched.manifest_path,
                results_path=fetched.results_path,
                user_id=user_id,
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


def _is_stop(error: ActivityError | ApplicationError) -> bool:
    """True when an activity ended because the Action was stopped."""
    return isinstance(error, ActivityError) and isinstance(error.cause, CancelledError)


def _error_message(error: ActivityError | ApplicationError) -> str:
    """The activity's own error, not Temporal's "Activity task failed"."""
    if isinstance(error, ActivityError) and error.cause is not None:
        return str(error.cause)
    return str(error)


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
