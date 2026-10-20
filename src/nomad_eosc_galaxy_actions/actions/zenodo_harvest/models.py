# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Pydantic models for the zenodo-harvest action's workflow and activities."""

from pydantic import BaseModel, Field


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
        description="Search, plan and convert, but do not create NOMAD projects.",
    )
    max_records: int | None = Field(
        None,
        ge=1,
        description="Process at most this many Zenodo records with new work in "
        "this run (useful for small test runs); already handled records do not "
        "count. Empty means no limit.",
    )


class PrepareRunInput(BaseModel):
    """Input for the activity that sets up the run's artifacts folder."""

    action_instance_id: str
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


class SearchZenodoResult(BaseModel):
    """Only counts and a path: the records themselves stay in candidates.json,
    because Temporal limits the size of activity results."""

    candidates_path: str
    candidates: int
    rejected: int


class BuildWorkListInput(BaseModel):
    """Input for the activity that compares the candidates with the state."""

    run_dir: str
    run_id: str = Field(..., description="Written into the work list for Galaxy.")
    max_records: int | None = Field(
        None, description="Limit for records with work; empty means no limit."
    )


class BuildWorkListResult(BaseModel):
    work_list_path: str
    records: int = Field(..., description="Records with something to download.")
    files: int = Field(..., description="Files to download, context files included.")
    skipped: int = Field(..., description="Files not downloaded, with a reason.")
    galaxy_configured: bool = Field(
        ..., description="A Galaxy workflow ID is set in the harvest config."
    )


class RunGalaxyInput(BaseModel):
    """Input for the activity that sends the work list to Galaxy."""

    work_list_path: str
    run_id: str


class RunGalaxyResult(BaseModel):
    history_id: str
    invocation_id: str


class PollGalaxyInput(BaseModel):
    invocation_id: str


class PollGalaxyResult(BaseModel):
    done: bool
    state: str
    outputs: dict[str, str] | None = Field(
        None, description="Output name → Galaxy dataset ID, once finished."
    )


class FetchManifestInput(BaseModel):
    """Input for the activity that downloads Galaxy's manifest and results."""

    run_dir: str
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


class MergeStateInput(BaseModel):
    """Input for the activity that merges Galaxy's outputs into the state."""

    manifest_path: str
    results_path: str
