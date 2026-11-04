# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Pydantic models for the zenodo-harvest action's workflow and activities."""

from typing import Any

from pydantic import BaseModel, Field, SecretStr, field_serializer, field_validator
from pydantic.json_schema import SkipJsonSchema

from nomad_eosc_galaxy_actions.credentials import reveal

# Raw file extensions that pynxtools-spm can read (pynxtools_spm/reader.py).
DEFAULT_RAW_EXTENSIONS = [".sxm", ".dat", ".sm4", ".spm", ".flt"]
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _normalize_extensions(values: list[str] | None) -> list[str] | None:
    """`SXM`, `.sxm ` → `.sxm`; empty entries and repeats are dropped."""
    if values is None:
        return None
    normalized = [f".{v.strip().lstrip('.').lower()}" for v in values if v.strip(" .")]
    return list(dict.fromkeys(normalized))


class ConfigOverrides(BaseModel):
    """Form values that replace the harvest config's ones for one run."""

    galaxy_workflow_id: str | None = Field(
        None, description="Replaces the config's galaxy_workflow_id."
    )
    raw_extensions: list[str] | None = Field(
        None, description="Replaces the config's raw_extensions."
    )


class ZenodoHarvestWorkflowInput(BaseModel):
    """Input of one harvest run. NOMAD renders the `/actions` form from it.

    No `upload_id` is needed: a harvest run does not work inside an existing
    project; it creates its own projects, one per Zenodo concept record.
    """

    user_id: str = Field(
        ..., description="Unique identifier for the user who initiated the workflow."
    )
    dry_run: bool = Field(
        True,
        description="Convert, but create no NOMAD projects and keep the state "
        "unchanged. Example: on for a first test.",
    )
    galaxy_workflow_id: str | None = Field(
        None,
        description="ID of the harvest workflow in your Galaxy account; empty uses "
        "the harvest config. Example: a82009b719022643",
    )
    raw_extensions: list[str] = Field(
        default_factory=lambda: list(DEFAULT_RAW_EXTENSIONS),
        description="Raw file extensions converted to NeXus. Example: .sxm",
    )
    max_records: int | None = Field(
        None,
        ge=1,
        description="At most this many Zenodo records with new work; empty means "
        "no limit. Example: 2",
    )
    records_per_work_list: int | None = Field(
        1,
        ge=1,
        description="Zenodo records per Galaxy run; empty sends all in one run. "
        "Example: 1",
    )
    repeat_every_days: int | None = Field(
        None,
        ge=1,
        description="Repeat the run every N days until stopped; empty runs once. "
        "Example: 7",
    )
    max_runs: int | None = Field(
        None,
        ge=1,
        description="With repeat_every_days: stop after N runs. Example: 4",
    )
    allow_duplicate_projects: bool = Field(
        False,
        description="Also convert raw files already in one of your NOMAD projects. "
        "Example: off",
    )
    notify_email: str | None = Field(
        None,
        pattern=EMAIL_PATTERN,
        description="Extra address that also gets the run report. "
        "Example: name@example.org",
    )
    galaxy_api_key: SecretStr | None = Field(
        None,
        description="Your Galaxy API key (User > Preferences > Manage API Key); "
        "empty uses the worker's GALAXY_API_KEY.",
    )
    zenodo_token: SecretStr | None = Field(
        None,
        description="Optional Zenodo token for larger search pages; empty uses "
        "the worker's ZENODO_TOKEN.",
    )
    # Set by the workflow when it continues as new; SkipJsonSchema keeps it
    # out of the JSON schema, so the form does not show it.
    run_number: SkipJsonSchema[int] = 1

    @field_validator("raw_extensions")
    @classmethod
    def _check_extensions(cls, value: list[str]) -> list[str]:
        value = _normalize_extensions(value)
        if not value:
            raise ValueError("give at least one raw file extension")
        return value

    @field_validator("galaxy_workflow_id", "notify_email", mode="before")
    @classmethod
    def _empty_is_none(cls, value: str | None) -> str | None:
        return value.strip() or None if isinstance(value, str) else value

    @field_serializer("galaxy_api_key", "zenodo_token", when_used="json")
    def _dump_secret(self, value: SecretStr | None) -> str | None:
        return reveal(value)

    def config_overrides(self) -> dict[str, Any]:
        """Keyword arguments for the activity inputs based on ConfigOverrides."""
        return {
            "galaxy_workflow_id": self.galaxy_workflow_id,
            "raw_extensions": self.raw_extensions,
        }


class GalaxyKeyInput(BaseModel):
    """Base of activity inputs that call Galaxy with the user's API key."""

    galaxy_api_key: SecretStr | None = Field(
        None, description="From the form; empty uses the worker's GALAXY_API_KEY."
    )

    @field_serializer("galaxy_api_key", when_used="json")
    def _dump_secret(self, value: SecretStr | None) -> str | None:
        return reveal(value)


class PrepareRunInput(ConfigOverrides):
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


class SearchZenodoInput(ConfigOverrides):
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


class BuildWorkListInput(ConfigOverrides):
    """Input for the activity that compares the candidates with the state."""

    run_dir: str
    user_id: str = Field(..., description="Whose state and projects to compare with.")
    run_id: str = Field(..., description="Written into the work list for Galaxy.")
    max_records: int | None = Field(
        None, description="Limit for records with work; empty means no limit."
    )
    allow_duplicate_projects: bool = False
    dry_run: bool = Field(False, description="Do not save the state synced from NOMAD.")


class BuildWorkListResult(BaseModel):
    work_list_path: str
    records: int = Field(..., description="Records with something to download.")
    record_ids: list[str] = Field(..., description="Their IDs, in work list order.")
    files: int = Field(..., description="Files to download, context files included.")
    skipped: int = Field(..., description="Files not downloaded, with a reason.")
    galaxy_configured: bool = Field(
        ...,
        description="A Galaxy workflow ID is set in the form or the harvest config.",
    )


class PrepareBatchInput(BaseModel):
    """Input for the activity that writes the work list of one batch."""

    run_dir: str
    folder: str = Field(..., description="The batch's subfolder, e.g. batch-001.")
    user_id: str
    run_id: str = Field(..., description="The batch's own run ID, sent to Galaxy.")
    record_ids: list[str]
    allow_duplicate_projects: bool = False


class PrepareBatchResult(BaseModel):
    batch_dir: str
    work_list_path: str


class RunGalaxyInput(GalaxyKeyInput, ConfigOverrides):
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
    notify_email: str | None = Field(None, description="One more recipient.")


class EmailReportResult(BaseModel):
    sent: bool
    reason: str | None = Field(None, description="Why no email was sent.")
