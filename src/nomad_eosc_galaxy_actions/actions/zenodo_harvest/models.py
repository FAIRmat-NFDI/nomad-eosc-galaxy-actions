# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Pydantic models for the zenodo-harvest action's workflow and activities."""

from pydantic import BaseModel, Field


class ZenodoHarvestWorkflowInput(BaseModel):
    """Input of one harvest run. NOMAD renders the `/actions` form from it.

    Unlike find-peaks, no `upload_id` is needed: a harvest run creates its own
    uploads (one per Zenodo record version) instead of working inside one.
    """

    user_id: str = Field(
        ..., description="Unique identifier for the user who initiated the workflow."
    )
    dry_run: bool = Field(
        True,
        description="Search, plan and convert, but do not create NOMAD uploads.",
    )
    max_records: int | None = Field(
        None,
        ge=1,
        description="Process at most this many Zenodo records in this run "
        "(useful for small test runs). Empty means no limit.",
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
