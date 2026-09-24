# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Pydantic models for the find-peaks action's workflow and activities."""

from pydantic import BaseModel, Field, SecretStr, field_serializer


def _serialize_secret(value: SecretStr) -> str:
    """Expose the secret's plain value in JSON. Temporal serializes
    activity/workflow inputs with `model_dump_json`, so without this the
    activity on the other end would receive the masked placeholder instead
    of the real key."""
    return value.get_secret_value()


class BaseWorkflowInput(BaseModel):
    """Fields required by NOMAD to execute a workflow via an Action."""

    upload_id: str = Field(
        ...,
        description="Unique identifier for the upload associated with the workflow.",
    )
    user_id: str = Field(
        ..., description="Unique identifier for the user who initiated the workflow."
    )


class FindPeaksWorkflowInput(BaseWorkflowInput):
    """Input model for the find-peaks workflow."""

    spectrum_entry_id: str = Field(
        ..., description="NOMAD entry ID of the source XPS spectrum."
    )
    galaxy_api_key: SecretStr = Field(
        ..., description="API key for the Galaxy account the action runs as."
    )
    prominence: float | None = Field(
        None, description="Forwarded to scipy.signal.find_peaks via the Galaxy tool."
    )
    distance: float | None = Field(
        None, description="Forwarded to scipy.signal.find_peaks via the Galaxy tool."
    )
    height: float | None = Field(
        None, description="Forwarded to scipy.signal.find_peaks via the Galaxy tool."
    )

    @field_serializer("galaxy_api_key", when_used="json")
    def _dump_galaxy_api_key(self, value: SecretStr) -> str:
        return _serialize_secret(value)


class ReadSpectrumInput(BaseModel):
    """Input for the activity that copies the spectrum out of upload storage.

    Only `spectrum_entry_id` — not `upload_id`/`mainfile_path` — so the
    activity re-resolves both fresh via `resolve_spectrum()` on each retry,
    rather than trusting a value captured once at trigger time.
    """

    spectrum_entry_id: str
    user_id: str
    action_instance_id: str


class RunGalaxyWorkflowInput(BaseModel):
    """Input for the activity that uploads to Galaxy and invokes the workflow."""

    galaxy_api_key: SecretStr
    spectrum_path: str = Field(
        ..., description="Local path, as returned by read_spectrum."
    )
    prominence: float | None = None
    distance: float | None = None
    height: float | None = None

    @field_serializer("galaxy_api_key", when_used="json")
    def _dump_galaxy_api_key(self, value: SecretStr) -> str:
        return _serialize_secret(value)


class RunGalaxyWorkflowResult(BaseModel):
    history_id: str
    invocation_id: str


class PollGalaxyInvocationInput(BaseModel):
    galaxy_api_key: SecretStr
    invocation_id: str

    @field_serializer("galaxy_api_key", when_used="json")
    def _dump_galaxy_api_key(self, value: SecretStr) -> str:
        return _serialize_secret(value)


class PollGalaxyInvocationResult(BaseModel):
    done: bool
    state: str
    outputs: dict[str, str] | None = Field(
        None, description="Workflow output step label -> Galaxy dataset ID, once done."
    )


class DownloadGalaxyResultInput(BaseModel):
    galaxy_api_key: SecretStr
    dataset_id: str
    action_instance_id: str

    @field_serializer("galaxy_api_key", when_used="json")
    def _dump_galaxy_api_key(self, value: SecretStr) -> str:
        return _serialize_secret(value)


class CreateResultEntryInput(BaseModel):
    """Input for the activity that creates the new NOMAD entry from the result.

    Only `spectrum_entry_id` — not `upload_id` — for the same reason as
    `ReadSpectrumInput`: resolved fresh, not threaded through as a separate
    field that could drift out of sync with it.
    """

    user_id: str
    spectrum_entry_id: str
    result_path: str = Field(
        ..., description="Local path, as returned by download_galaxy_result."
    )


class CreateResultEntryResult(BaseModel):
    entry_id: str | None = Field(
        default=None,
        description="None if the raw file was added to the upload but no "
        "parser matched it. The file is still there, just not processed "
        "into an entry yet.",
    )
