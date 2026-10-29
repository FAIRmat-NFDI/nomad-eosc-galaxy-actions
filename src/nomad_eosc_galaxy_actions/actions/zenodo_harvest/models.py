# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Pydantic models for the zenodo-harvest action's workflow and activities."""

from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_serializer
from pydantic.json_schema import SkipJsonSchema

from nomad_eosc_galaxy_actions.credentials import reveal


class ZenodoHarvestWorkflowInput(BaseModel):
    """Input of one harvest run. NOMAD renders the `/actions` form from it.

    No `upload_id` is needed: a harvest run does not work inside an existing
    project; it creates its own projects, one per Zenodo record version.
    """

    user_id: str = Field(
        ..., description="Unique identifier for the user who initiated the workflow."
    )
    dry_run: bool = Field(
        True,
        description="Search, plan and convert, but create no NOMAD projects and "
        "leave the record of processed files unchanged, so a later real run "
        "handles the same files.",
    )
    max_records: int | None = Field(
        None,
        ge=1,
        description="Process at most this many Zenodo records with new work in "
        "this run (useful for small test runs); already handled records do not "
        "count. Empty means no limit.",
    )
    repeat_every_days: int | None = Field(
        None,
        ge=1,
        description="Start a new run this many days after the previous one "
        "started (7 = weekly), until the Action is stopped. Empty means one run. "
        "A failed run stops the Action and the repeats; its report is still "
        "emailed. Start the Action again to resume.",
    )
    max_runs: int | None = Field(
        None,
        ge=1,
        description="With repeat_every_days: stop after this many runs. Empty "
        "means until the Action is stopped.",
    )
    allow_duplicate_projects: bool = Field(
        False,
        description="Also convert raw files that are already in one of your NOMAD "
        "projects (same SHA-256 checksum). Off: such files are skipped and listed "
        "as duplicate in the report.",
    )
    galaxy_api_key: SecretStr | None = Field(
        None,
        description="Your Galaxy API key (in Galaxy: User > Preferences > Manage "
        "API Key). Empty uses the worker's GALAXY_API_KEY, if the administrators "
        "set one.",
    )
    zenodo_token: SecretStr | None = Field(
        None,
        description="Optional Zenodo personal access token; allows larger search "
        "pages. Empty uses the worker's ZENODO_TOKEN, if set.",
    )
    # Set by the workflow when it continues as new; SkipJsonSchema keeps it
    # out of the JSON schema, so the form does not show it.
    run_number: SkipJsonSchema[int] = 1

    @field_serializer("galaxy_api_key", "zenodo_token", when_used="json")
    def _dump_secret(self, value: SecretStr | None) -> str | None:
        return reveal(value)


class GalaxyKeyInput(BaseModel):
    """Base of activity inputs that call Galaxy with the user's API key."""

    galaxy_api_key: SecretStr | None = Field(
        None, description="From the form; empty uses the worker's GALAXY_API_KEY."
    )

    @field_serializer("galaxy_api_key", when_used="json")
    def _dump_secret(self, value: SecretStr | None) -> str | None:
        return reveal(value)


class PrepareRunInput(BaseModel):
    """Input for the activity that sets up the run's artifacts folder."""

    action_instance_id: str
    user_id: str = Field(..., description="Only one run per user at a time.")
    run_id: str = Field(
        ..., description="Unique per run; equals action_instance_id for one run."
    )
    run_folder: str | None = Field(
        None, description="Subfolder of a repeated run; empty for one run."
    )
    started_at: str = Field(
        ..., description="ISO 8601 start time, taken from the workflow clock."
    )
    dry_run: bool
    max_records: int | None = None


class PrepareRunResult(BaseModel):
    run_dir: str = Field(..., description="This run's artifacts directory.")
    run_config_path: str = Field(
        ..., description="Path of run_config.json inside run_dir."
    )


class SearchZenodoInput(BaseModel):
    """Input for the activity that searches Zenodo."""

    run_dir: str
    zenodo_token: SecretStr | None = Field(
        None, description="From the form; empty uses the worker's ZENODO_TOKEN."
    )

    @field_serializer("zenodo_token", when_used="json")
    def _dump_secret(self, value: SecretStr | None) -> str | None:
        return reveal(value)


class SearchZenodoResult(BaseModel):
    """Only counts and a path: the records themselves stay in candidates.json,
    because Temporal limits the size of activity results."""

    candidates_path: str
    candidates: int
    rejected: int


class BuildWorkListInput(BaseModel):
    """Input for the activity that compares the candidates with the state."""

    run_dir: str
    user_id: str = Field(..., description="Whose state and projects to compare with.")
    run_id: str = Field(..., description="Written into the work list for Galaxy.")
    max_records: int | None = Field(
        None, description="Limit for records with work; empty means no limit."
    )
    allow_duplicate_projects: bool = False


class BuildWorkListResult(BaseModel):
    work_list_path: str
    records: int = Field(..., description="Records with something to download.")
    files: int = Field(..., description="Files to download, context files included.")
    skipped: int = Field(..., description="Files not downloaded, with a reason.")
    galaxy_configured: bool = Field(
        ..., description="A Galaxy workflow ID is set in the harvest config."
    )


class RunGalaxyInput(GalaxyKeyInput):
    """Input for the activity that sends the work list to Galaxy."""

    work_list_path: str
    run_id: str


class RunGalaxyResult(BaseModel):
    history_id: str
    invocation_id: str


class PollGalaxyInput(GalaxyKeyInput):
    invocation_id: str


class PollGalaxyResult(BaseModel):
    done: bool
    state: str
    outputs: dict[str, str] | None = Field(
        None, description="Output name → Galaxy dataset ID, once finished."
    )


class FetchManifestInput(GalaxyKeyInput):
    """Input for the activity that downloads Galaxy's manifest and results."""

    run_dir: str
    user_id: str = Field(..., description="Whose projects count for duplicates.")
    work_list_path: str = Field(
        ..., description="The work list sent to Galaxy, to check the manifest."
    )
    manifest_dataset_id: str
    results_dataset_id: str | None = Field(
        None, description="Missing in the placeholder workflow."
    )


class FetchManifestResult(BaseModel):
    manifest_path: str
    results_path: str | None = None
    placeholder: bool = Field(
        ...,
        description="True if Galaxy returned the work list unchanged (placeholder "
        "workflow), so there is nothing to merge into the state.",
    )


class FetchProjectsInput(GalaxyKeyInput):
    """Input for the activity that downloads the project zips from Galaxy."""

    run_dir: str
    collection_id: str = Field(
        ..., description="Galaxy collection: one zip per Zenodo record version."
    )


class FetchProjectsResult(BaseModel):
    zip_paths: dict[str, str] = Field(..., description="Record ID → local zip.")


class CreateProjectsInput(BaseModel):
    """Input for the activity that creates one NOMAD project per zip."""

    work_list_path: str
    manifest_path: str = Field(..., description="For the checksums of each project.")
    results_path: str
    user_id: str = Field(
        ..., description="Owner of the projects when no nomad_url is configured."
    )
    zip_paths: dict[str, str] = Field(..., description="Record ID → local zip.")


class CreateProjectsResult(BaseModel):
    upload_ids: dict[str, str] = Field(..., description="Record ID → upload ID.")
    created: list[str] = Field(..., description="Records with a new project.")
    left_out: dict[str, str] = Field(
        ..., description="Record ID → reason, for records without a project."
    )


class MergeStateInput(BaseModel):
    """Input for the activity that merges Galaxy's outputs into the state."""

    manifest_path: str
    results_path: str
    user_id: str = Field(..., description="Whose state is updated.")
    upload_ids: dict[str, str] = Field(
        default_factory=dict, description="Record ID → NOMAD upload ID."
    )
    left_out: list[str] = Field(
        default_factory=list,
        description="Record IDs not merged (no project), so the next run retries them.",
    )


class FinishRunInput(BaseModel):
    """Input for the activity that ends the user's active run."""

    user_id: str
    run_id: str


class WriteReportInput(BaseModel):
    """Input for the activity that writes the run's report."""

    run_dir: str
    steps: dict[str, Any] = Field(
        ..., description="Summaries of the galaxy, nomad and merge steps."
    )


class WriteReportResult(BaseModel):
    report_path: str = Field(..., description="report.json, for programs.")
    markdown_path: str = Field(..., description="report.md, for people.")


class EmailReportInput(BaseModel):
    """Input for the activity that emails the report."""

    report_path: str
    markdown_path: str
    user_id: str = Field(..., description="Recipient: the user who started the run.")


class EmailReportResult(BaseModel):
    sent: bool
    reason: str | None = Field(None, description="Why no email was sent.")
