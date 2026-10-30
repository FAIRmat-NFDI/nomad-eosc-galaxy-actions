# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Activities for the zenodo-harvest action.

Each activity is a thin wrapper that resolves NOMAD-specific things (config,
storage folders) and then calls a plain function, so that the logic itself
is testable without a NOMAD installation or a Temporal worker.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from nomad_eosc_galaxy_actions import (
    checksum_index,
    credentials,
    galaxy_client,
    mailer,
    nomad_projects,
    report,
    work_list,
    zenodo_client,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.harvest_config import (
    HarvestConfig,
    HarvestConfigError,
    load_harvest_config,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.local_projects import (
    LocalProjects,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
    BuildWorkListInput,
    BuildWorkListResult,
    CreateProjectsInput,
    CreateProjectsResult,
    EmailReportInput,
    EmailReportResult,
    FetchManifestInput,
    FetchManifestResult,
    FetchProjectsInput,
    FetchProjectsResult,
    FinishRunInput,
    MergeStateInput,
    PollGalaxyInput,
    PollGalaxyResult,
    PrepareBatchInput,
    PrepareBatchResult,
    PrepareRunInput,
    PrepareRunResult,
    RunGalaxyInput,
    RunGalaxyResult,
    SearchZenodoInput,
    SearchZenodoResult,
    WriteReportInput,
    WriteReportResult,
)
from nomad_eosc_galaxy_actions.manifest import (
    Manifest,
    MergeSummary,
    Results,
    merge_manifest,
)
from nomad_eosc_galaxy_actions.state_store import (
    RunLockedError,
    StateStore,
    acquire_run,
    release_run,
)

ZENODO_HARVEST_ACTION = (
    "nomad_eosc_galaxy_actions.actions.zenodo_harvest:zenodo_harvest_action"
)
RUN_CONFIG_FILE = "run_config.json"
# Version of the run_config.json layout. Bump it when a key is renamed,
# removed or changes meaning, so readers can recognize older files.
RUN_CONFIG_SCHEMA_VERSION = 2
CANDIDATES_FILE = "candidates.json"
# Version of the candidates.json layout (same rule as RUN_CONFIG_SCHEMA_VERSION).
CANDIDATES_SCHEMA_VERSION = 1
WORK_LIST_FILE = "work_list.json"
SKIPPED_FILES_FILE = "skipped_files.json"
# Version of the skipped_files.json layout (same rule as RUN_CONFIG_SCHEMA_VERSION).
SKIPPED_FILES_SCHEMA_VERSION = 1
# Default folder of the states (harvest config `state_dir`), inside NOMAD's
# persistent action artifacts folder: users/<user_id>/state.json and the
# checksum index of all users.
STATE_DIR = "zenodo_harvest"
STATE_FILE = "state.json"
CHECKSUM_INDEX_FILE = "checksum_index.json"
# Longer than any run (Galaxy is polled for at most a day), so an older mark
# of an active run is from a run that ended without removing it.
RUN_MAX_AGE = timedelta(days=2)
# NOMAD user IDs are UUIDs; anything else must not become a folder name.
USER_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+")
# Each batch of records has its own folder in the run folder: batch-001, ...
BATCH_FOLDER_PREFIX = "batch-"
DUPLICATES_FILE = "duplicates.json"
# Version of the duplicates.json layout (same rule as RUN_CONFIG_SCHEMA_VERSION).
DUPLICATES_SCHEMA_VERSION = 1
MANIFEST_FILE = "manifest.json"
RESULTS_FILE = "results.json"
PROJECTS_DIR = "projects"
REPORT_FILE = "report.json"
REPORT_MARKDOWN_FILE = "report.md"
# Step index of the work list input in the harvest's Galaxy workflow.
GALAXY_WORK_LIST_STEP = "0"
# Zenodo allows larger pages for authenticated requests.
PAGE_SIZE_ANONYMOUS = 25
PAGE_SIZE_AUTHENTICATED = 100


def _harvest_config() -> HarvestConfig:
    """Load the harvest config file named by the entry point (or the shipped one)."""
    from nomad.config import config  # noqa: PLC0415

    entry_point = config.get_plugin_entry_point(ZENODO_HARVEST_ACTION)
    try:
        return load_harvest_config(entry_point.harvest_config_path)
    except (FileNotFoundError, HarvestConfigError) as error:
        # Loading again gives the same error, so do not retry.
        raise ApplicationError(str(error), non_retryable=True) from error


def _secret(service: str, form_value, *, required: bool = True) -> str | None:
    """A secret from the form or the worker; a missing one is not retried."""
    try:
        return credentials.resolve_secret(service, form_value, required=required)
    except credentials.MissingSecretError as error:
        raise ApplicationError(str(error), non_retryable=True) from error


def _state_dir(harvest_config: HarvestConfig) -> Path:
    from nomad.actions.manager import action_artifacts_dir  # noqa: PLC0415

    return Path(harvest_config.state_dir or Path(action_artifacts_dir()) / STATE_DIR)


def _user_dir(harvest_config: HarvestConfig, user_id: str) -> Path:
    """The folder of one user's state; the user ID must be a safe name."""
    if not USER_ID_PATTERN.fullmatch(user_id):
        raise ApplicationError(f"unexpected user ID {user_id!r}", non_retryable=True)
    return _state_dir(harvest_config) / "users" / user_id


def _state_path(harvest_config: HarvestConfig, user_id: str) -> Path:
    return _user_dir(harvest_config, user_id) / STATE_FILE


def _checksum_index(harvest_config: HarvestConfig) -> checksum_index.ChecksumIndex:
    return checksum_index.ChecksumIndex(
        _state_dir(harvest_config) / CHECKSUM_INDEX_FILE
    )


def write_run_config(
    run_dir: str | Path, harvest_config: dict[str, Any], data: PrepareRunInput
) -> Path:
    """Write the effective configuration of one run to `run_dir/run_config.json`.

    The file holds:

    - `schema_version`: version of this file's layout (keys and their meaning),
      not of the data. A reader that finds an older version knows the layout
      differs and can convert it. See RUN_CONFIG_SCHEMA_VERSION.
    - `action_instance_id`, `run_id`, `started_at`, `dry_run`, `max_records`:
      the run's inputs (`run_id` added in version 2).
    - `harvest_config`: the harvest config used by this run (already upgraded
      to the current version), so the run can be explained even after the
      config file changes.

    Args:
        run_dir: the run's artifacts directory (created if missing).
        harvest_config: content of the loaded harvest config.
        data: the run's own inputs.

    Returns:
        Path of the written file.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    run_config = {
        "schema_version": RUN_CONFIG_SCHEMA_VERSION,
        "action_instance_id": data.action_instance_id,
        "run_id": data.run_id,
        "started_at": data.started_at,
        "dry_run": data.dry_run,
        "max_records": data.max_records,
        "harvest_config": harvest_config,
    }
    path = run_dir / RUN_CONFIG_FILE
    path.write_text(json.dumps(run_config, indent=2), encoding="utf-8")
    return path


@activity.defn
def prepare_run(data: PrepareRunInput) -> PrepareRunResult:
    """Mark the run as the user's active one, create its artifacts folder and
    record its effective configuration.

    A second run of the same user fails at once: both would plan the same files.
    """
    from nomad.actions.manager import action_instance_artifacts_dir  # noqa: PLC0415

    harvest_config = _harvest_config()
    try:
        acquire_run(
            _user_dir(harvest_config, data.user_id),
            data.run_id,
            now=datetime.now(timezone.utc),
            max_age=RUN_MAX_AGE,
        )
    except RunLockedError as error:
        raise ApplicationError(
            f"{error}; wait until it ends or stop it", non_retryable=True
        ) from error
    run_dir = Path(action_instance_artifacts_dir(data.action_instance_id))
    if data.run_folder:
        run_dir /= data.run_folder
    path = write_run_config(run_dir, harvest_config.model_dump(mode="json"), data)
    activity.logger.info("Prepared harvest run in %s", run_dir)
    return PrepareRunResult(run_dir=str(run_dir), run_config_path=str(path))


def write_candidates(
    run_dir: str | Path,
    query: str,
    candidates: list[zenodo_client.ZenodoRecord],
    rejections: list[zenodo_client.Rejection],
) -> Path:
    """Write the search result to `run_dir/candidates.json`.

    The file holds `schema_version` (see CANDIDATES_SCHEMA_VERSION), the
    `query`, the `candidates` (full records) and the `rejected` records with
    their reason, so every skipped record can be explained in the report.
    """
    content = {
        "schema_version": CANDIDATES_SCHEMA_VERSION,
        "query": query,
        "candidates": [record.model_dump(mode="json") for record in candidates],
        "rejected": [rejection.model_dump(mode="json") for rejection in rejections],
    }
    path = Path(run_dir) / CANDIDATES_FILE
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return path


@activity.defn
def search_zenodo(data: SearchZenodoInput) -> SearchZenodoResult:
    """Search Zenodo and write the candidates of this run to candidates.json.

    An optional Zenodo token (form field, else the worker's ZENODO_TOKEN)
    allows larger pages.
    """
    harvest_config = _harvest_config()
    token = _secret("zenodo", data.zenodo_token, required=False)
    hits = zenodo_client.iter_search_hits(
        zenodo_client.make_session(token),
        harvest_config.zenodo_query,
        zenodo_url=harvest_config.zenodo_url,
        page_size=PAGE_SIZE_AUTHENTICATED if token else PAGE_SIZE_ANONYMOUS,
        request_delay=harvest_config.zenodo_request_delay,
    )
    candidates, rejections = zenodo_client.collect_candidates(
        hits,
        raw_extensions=harvest_config.raw_extensions,
        licenses=harvest_config.licenses,
    )
    path = write_candidates(
        data.run_dir, harvest_config.zenodo_query, candidates, rejections
    )
    activity.logger.info(
        "Zenodo search: %d candidates, %d rejected", len(candidates), len(rejections)
    )
    return SearchZenodoResult(
        candidates_path=str(path),
        candidates=len(candidates),
        rejected=len(rejections),
    )


def write_work_list(
    run_dir: str | Path,
    content: dict[str, Any],
    skipped: list[work_list.SkippedFile],
) -> Path:
    """Write `work_list.json` (for Galaxy) and `skipped_files.json` (for the report).

    The skipped files stay out of the work list, which is Galaxy's input.
    """
    run_dir = Path(run_dir)
    skipped_content = {
        "schema_version": SKIPPED_FILES_SCHEMA_VERSION,
        "skipped": [file.model_dump(mode="json") for file in skipped],
    }
    (run_dir / SKIPPED_FILES_FILE).write_text(
        json.dumps(skipped_content, indent=2), encoding="utf-8"
    )
    path = run_dir / WORK_LIST_FILE
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return path


@activity.defn
def build_work_list(data: BuildWorkListInput) -> BuildWorkListResult:
    """Compare this run's candidates with the user's state and write the work list.

    Only reads the state; it is updated after Galaxy's manifest is complete.
    Unless duplicates are allowed, raw files already in one of the user's
    projects are sent to Galaxy as known, so they are not converted again.
    """
    harvest_config = _harvest_config()
    settings = work_list.WorkListSettings.model_validate(
        harvest_config.model_dump(include=set(work_list.WorkListSettings.model_fields))
    )
    search_result = json.loads(
        (Path(data.run_dir) / CANDIDATES_FILE).read_text(encoding="utf-8")
    )
    candidates = [
        zenodo_client.ZenodoRecord.model_validate(record)
        for record in search_result["candidates"]
    ]
    in_projects = _in_projects(
        harvest_config, data.user_id, data.allow_duplicate_projects
    )
    with StateStore(_state_path(harvest_config, data.user_id)) as store:
        content, skipped = work_list.build_work_list(
            candidates,
            store,
            settings,
            run_id=data.run_id,
            max_records=data.max_records,
            in_projects=in_projects,
        )
    path = write_work_list(data.run_dir, content, skipped)
    records = content["records"]
    files = sum(len(record["download"]) for record in records)
    activity.logger.info(
        "Work list: %d records, %d files, %d skipped", len(records), files, len(skipped)
    )
    return BuildWorkListResult(
        work_list_path=str(path),
        records=len(records),
        record_ids=[record["record_id"] for record in records],
        files=files,
        skipped=len(skipped),
        galaxy_configured=bool(harvest_config.galaxy_workflow_id),
    )


def _in_projects(harvest_config: HarvestConfig, user_id: str, allowed: bool):
    """Raw files in the user's projects; none when duplicates are allowed."""
    if allowed:
        return []
    return _checksum_index(harvest_config).known_sha256(user_id)


@activity.defn
def prepare_batch(data: PrepareBatchInput) -> PrepareBatchResult:
    """Write the work list of one batch into its own folder of the run.

    Reads the state again, so files that an earlier batch converted count as
    known (see `work_list.batch_work_list`).
    """
    harvest_config = _harvest_config()
    run_dir = Path(data.run_dir)
    content = json.loads((run_dir / WORK_LIST_FILE).read_text(encoding="utf-8"))
    in_projects = _in_projects(
        harvest_config, data.user_id, data.allow_duplicate_projects
    )
    with StateStore(_state_path(harvest_config, data.user_id)) as store:
        batch = work_list.batch_work_list(
            content,
            data.record_ids,
            run_id=data.run_id,
            store=store,
            in_projects=in_projects,
        )
    batch_dir = run_dir / data.folder
    batch_dir.mkdir(parents=True, exist_ok=True)
    path = batch_dir / WORK_LIST_FILE
    path.write_text(json.dumps(batch, indent=2), encoding="utf-8")
    return PrepareBatchResult(batch_dir=str(batch_dir), work_list_path=str(path))


@activity.defn
def run_galaxy(data: RunGalaxyInput) -> RunGalaxyResult:
    """Upload the work list to a new Galaxy history and start the workflow.

    The API key comes from the form, else the worker's GALAXY_API_KEY; it is
    never written to the run's files, logs or Galaxy parameters.
    """
    harvest_config = _harvest_config()
    result = galaxy_client.upload_and_invoke(
        api_key=_secret("galaxy", data.galaxy_api_key),
        workflow_id=harvest_config.galaxy_workflow_id,
        file_path=data.work_list_path,
        file_type="json",
        workflow_input_step=GALAXY_WORK_LIST_STEP,
        history_name=f"zenodo-harvest-{data.run_id}",
        galaxy_url=harvest_config.galaxy_url,
    )
    activity.logger.info("Galaxy invocation %s started", result["invocation_id"])
    return RunGalaxyResult(**result)


@activity.defn
def poll_galaxy(data: PollGalaxyInput) -> PollGalaxyResult:
    """Check the invocation's status once; the workflow sleeps between calls."""
    result = galaxy_client.poll_invocation(
        api_key=_secret("galaxy", data.galaxy_api_key),
        invocation_id=data.invocation_id,
        galaxy_url=_harvest_config().galaxy_url,
    )
    return PollGalaxyResult(**result)


@activity.defn
def fetch_manifest(data: FetchManifestInput) -> FetchManifestResult:
    """Download Galaxy's manifest (and results) into the run folder and check it.

    The manifest must carry this run's `run_id`, which proves it belongs to
    the work list that was sent.
    """
    harvest_config = _harvest_config()
    api_key = _secret("galaxy", data.galaxy_api_key)
    run_dir = Path(data.run_dir)

    def download(dataset_id: str, name: str) -> Path:
        path = run_dir / name
        galaxy_client.download_dataset(
            api_key=api_key,
            dataset_id=dataset_id,
            output_path=path,
            galaxy_url=harvest_config.galaxy_url,
        )
        return path

    path = download(data.manifest_dataset_id, MANIFEST_FILE)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    sent = json.loads(Path(data.work_list_path).read_text(encoding="utf-8"))
    if manifest.get("run_id") != sent["run_id"]:
        # Downloading again would give the same file, so do not retry.
        raise ApplicationError(
            f"Galaxy manifest has run_id {manifest.get('run_id')!r}, "
            f"expected {sent['run_id']!r}",
            non_retryable=True,
        )
    placeholder = manifest == sent  # the work list came back unchanged
    if placeholder:
        activity.logger.info("Galaxy returned the work list unchanged (placeholder)")
    else:
        files = manifest.get("files", [])
        actions = Counter(file.get("action") for file in files)
        activity.logger.info(
            "Galaxy manifest for run %s: %d files (%s), tool_version %s; results: %s",
            sent["run_id"],
            len(files),
            ", ".join(f"{n} {action}" for action, n in sorted(actions.items()))
            or "none",
            manifest.get("tool_version"),
            "yes" if data.results_dataset_id else "no",
        )
        _write_duplicates(run_dir, manifest, harvest_config, data.user_id)
    results_path = None
    if data.results_dataset_id:
        results_path = str(download(data.results_dataset_id, RESULTS_FILE))
    return FetchManifestResult(
        manifest_path=str(path), results_path=results_path, placeholder=placeholder
    )


def _write_duplicates(
    run_dir: Path, manifest: dict[str, Any], harvest_config: HarvestConfig, user_id: str
) -> None:
    """Log the raw files Galaxy skipped because a project of the user has them,
    and write them to `duplicates.json` for the report."""
    duplicates = checksum_index.find_duplicates(
        manifest, _checksum_index(harvest_config), user_id
    )
    for duplicate in duplicates:
        activity.logger.info(
            "Raw file %s (sha256 %s) is already in NOMAD project %s; skipped",
            duplicate.key,
            duplicate.sha256[:12],
            duplicate.upload_id,
        )
    content = {
        "schema_version": DUPLICATES_SCHEMA_VERSION,
        "duplicates": [duplicate.model_dump(mode="json") for duplicate in duplicates],
    }
    (run_dir / DUPLICATES_FILE).write_text(
        json.dumps(content, indent=2), encoding="utf-8"
    )


@activity.defn
def fetch_projects(data: FetchProjectsInput) -> FetchProjectsResult:
    """Download Galaxy's project zips to `run_dir/projects/<record ID>.zip`."""
    harvest_config = _harvest_config()
    api_key = _secret("galaxy", data.galaxy_api_key)
    elements = galaxy_client.collection_elements(
        api_key, data.collection_id, galaxy_url=harvest_config.galaxy_url
    )
    folder = Path(data.run_dir) / PROJECTS_DIR
    folder.mkdir(parents=True, exist_ok=True)
    zip_paths = {}
    for record_id, dataset_id in elements.items():
        if not record_id.isdigit():  # also keeps the name a safe file name
            raise ApplicationError(
                f"Galaxy project {record_id!r} is not a Zenodo record ID",
                non_retryable=True,
            )
        zip_paths[record_id] = galaxy_client.download_dataset(
            api_key,
            dataset_id,
            folder / f"{record_id}.zip",
            galaxy_url=harvest_config.galaxy_url,
        )
    return FetchProjectsResult(zip_paths=zip_paths)


def _projects(harvest_config: HarvestConfig, user_id: str) -> nomad_projects.Projects:
    """The NOMAD in `nomad_url` (with the worker's NOMAD_PAT), else the local one.

    A missing PAT is an error, not a fallback: projects must never end up
    in another NOMAD than the configured one.
    """
    if not harvest_config.nomad_url:
        return LocalProjects(user_id)
    pat = os.environ.get("NOMAD_PAT")
    if not pat:
        raise ApplicationError(
            "nomad_url is set, but the worker has no NOMAD_PAT environment variable",
            non_retryable=True,
        )
    return nomad_projects.RestProjects(harvest_config.nomad_url, pat)


@activity.defn
def create_projects(data: CreateProjectsInput) -> CreateProjectsResult:
    """Create one private NOMAD project per record version zip.

    Each zip gets `nomad.json` (references) and `zenodo_harvest.json` (record
    and checksums). Retry-safe: an existing project with the same name is reused.
    Once the demo project limit is reached, the remaining records are left
    out (reason `project_limit`) and handled by a later run.
    """
    harvest_config = _harvest_config()
    projects = _projects(harvest_config, data.user_id)
    content = json.loads(Path(data.work_list_path).read_text(encoding="utf-8"))
    records = {record["record_id"]: record for record in content["records"]}
    manifest = Manifest.model_validate_json(
        Path(data.manifest_path).read_text(encoding="utf-8")
    )
    results = Results.model_validate_json(
        Path(data.results_path).read_text(encoding="utf-8")
    )
    result = CreateProjectsResult(upload_ids={}, created=[], left_out={})
    for record_id, zip_path in sorted(data.zip_paths.items()):
        record = records.get(record_id)
        if record is None:
            raise ApplicationError(
                f"Galaxy returned a project for record {record_id}, "
                "which is not in the work list",
                non_retryable=True,
            )
        if result.left_out:  # the limit was reached for an earlier record
            result.left_out[record_id] = "project_limit"
            continue
        doi = record.get("version_doi")
        reference = (
            f"https://doi.org/{doi}"
            if doi
            else f"{harvest_config.zenodo_url.rstrip('/')}/records/{record_id}"
        )
        nomad_projects.add_project_metadata(zip_path, [reference])
        nomad_projects.add_harvest_metadata(zip_path, record, manifest, results)
        name = nomad_projects.project_name(record["concept_record_id"], record_id)
        try:
            upload_id, created = nomad_projects.create_project(
                projects, name, Path(zip_path)
            )
        except nomad_projects.ProjectLimitReached as error:
            activity.logger.warning("%s", error)
            result.left_out[record_id] = "project_limit"
            continue
        result.upload_ids[record_id] = upload_id
        if created:
            result.created.append(record_id)
    activity.logger.info(
        "NOMAD projects: %d created, %d reused, %d left out",
        len(result.created),
        len(result.upload_ids) - len(result.created),
        len(result.left_out),
    )
    return result


@activity.defn
def merge_state(data: MergeStateInput) -> MergeSummary:
    """Merge Galaxy's manifest and results into the user's state (all or nothing),
    then add the converted raw files to the checksum index."""
    harvest_config = _harvest_config()
    try:
        manifest = Manifest.model_validate_json(
            Path(data.manifest_path).read_text(encoding="utf-8")
        )
        results = Results.model_validate_json(
            Path(data.results_path).read_text(encoding="utf-8")
        )
        with StateStore(_state_path(harvest_config, data.user_id)) as store:
            summary = merge_manifest(
                store, manifest, results, data.upload_ids, data.left_out
            )
        converted = checksum_index.converted_files(
            manifest, results, data.upload_ids, data.user_id
        )
        _checksum_index(harvest_config).add(converted)
    except ValueError as error:  # includes pydantic's ValidationError
        # The same files would fail again, so do not retry.
        raise ApplicationError(
            f"Cannot merge Galaxy outputs: {error}", non_retryable=True
        ) from error
    activity.logger.info("State merged: %s", summary)
    return summary


@activity.defn
def finish_run(data: FinishRunInput) -> bool:
    """Remove the run's mark as the user's active run (also after a failure)."""
    return release_run(_user_dir(_harvest_config(), data.user_id), data.run_id)


def _read_json(path: Path) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _joined_outputs(
    run_dir: Path,
) -> tuple[Manifest | None, Results | None, dict[str, Any]]:
    """Galaxy's manifests, results and duplicates of all batches, joined.

    A placeholder batch returns its work list as manifest, which is no
    manifest, so it is left out.
    """
    manifests, results, duplicates = [], [], []
    for batch_dir in sorted(run_dir.glob(f"{BATCH_FOLDER_PREFIX}*")):
        manifest = _read_json(batch_dir / MANIFEST_FILE)
        if manifest and manifest != _read_json(batch_dir / WORK_LIST_FILE):
            manifests.append(Manifest.model_validate(manifest))
        if result := _read_json(batch_dir / RESULTS_FILE):
            results.append(Results.model_validate(result))
        duplicates += (_read_json(batch_dir / DUPLICATES_FILE) or {}).get(
            "duplicates", []
        )
    joined_manifest = (
        Manifest(
            schema_version=1,
            run_id=manifests[0].run_id,
            tool_version=manifests[0].tool_version,
            archives=[a for m in manifests for a in m.archives],
            files=[f for m in manifests for f in m.files],
        )
        if manifests
        else None
    )
    joined_results = (
        Results(
            schema_version=1,
            run_id=results[0].run_id,
            files=[f for r in results for f in r.files],
        )
        if results
        else None
    )
    return joined_manifest, joined_results, {"duplicates": duplicates}


@activity.defn
def write_report(data: WriteReportInput) -> WriteReportResult:
    """Write `report.json` and `report.md` into the run folder."""
    run_dir = Path(data.run_dir)
    manifest, results, duplicates = _joined_outputs(run_dir)
    content = report.build_report(
        run_config=_read_json(run_dir / RUN_CONFIG_FILE) or {},
        candidates=_read_json(run_dir / CANDIDATES_FILE) or {},
        work_list=_read_json(run_dir / WORK_LIST_FILE),
        skipped_files=_read_json(run_dir / SKIPPED_FILES_FILE),
        manifest=manifest,
        results=results,
        steps=data.steps,
        duplicates=duplicates,
    )
    report_path = run_dir / REPORT_FILE
    report_path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    markdown_path = run_dir / REPORT_MARKDOWN_FILE
    markdown_path.write_text(report.render_markdown(content), encoding="utf-8")
    return WriteReportResult(
        report_path=str(report_path), markdown_path=str(markdown_path)
    )


def _user_email(user_id: str) -> str | None:
    """Email address of a NOMAD user (needs NOMAD's user management)."""
    from nomad import datamodel  # noqa: PLC0415

    user = datamodel.User.get(user_id=user_id)
    return user.email if user else None


@activity.defn
def email_report(data: EmailReportInput) -> EmailReportResult:
    """Email `report.md` (with `report.json` attached) to the run's user.

    Also sent for a failed run; its subject then ends in "run failed".

    Sends nothing when NOMAD's `mail` config is not enabled (see `mailer`).
    """
    from nomad.config import config  # noqa: PLC0415

    try:
        settings = mailer.SmtpSettings.from_mail_config(config.mail)
    except mailer.MailConfigError as error:
        raise ApplicationError(str(error), non_retryable=True) from error
    if settings is None:
        return EmailReportResult(sent=False, reason="smtp_not_configured")
    address = _user_email(data.user_id)
    if not address:
        return EmailReportResult(sent=False, reason="no_email_address")
    content = json.loads(Path(data.report_path).read_text(encoding="utf-8"))
    message = mailer.build_message(
        settings.sender,
        address,
        subject=report.email_subject(content),
        body=Path(data.markdown_path).read_text(encoding="utf-8"),
        attachments=[Path(data.report_path)],
    )
    try:
        mailer.send_mail(settings, message)
    except mailer.MailConfigError as error:
        raise ApplicationError(str(error), non_retryable=True) from error
    return EmailReportResult(sent=True)
