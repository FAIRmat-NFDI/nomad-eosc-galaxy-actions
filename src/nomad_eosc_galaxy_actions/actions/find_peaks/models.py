"""Pydantic models for the find-peaks action's workflow and activity."""

from pydantic import BaseModel, Field


class BaseWorkflowInput(BaseModel):
    """Fields required by NOMAD to execute a workflow via an Action."""

    upload_id: str = Field(
        ..., description='Unique identifier for the upload associated with the workflow.'
    )
    user_id: str = Field(
        ..., description='Unique identifier for the user who initiated the workflow.'
    )


class FindPeaksWorkflowInput(BaseWorkflowInput):
    """Input model for the find-peaks workflow.

    This first version is deliberately an empty run: it only proves that data
    flows through Temporal and back. Locating and reading the spectrum's NeXus
    file, and the Galaxy call itself, are added once this skeleton passes.
    """

    spectrum_entry_id: str = Field(
        ..., description='NOMAD entry ID of the source XPS spectrum.'
    )


class EchoInput(BaseModel):
    """Input model for the placeholder activity."""

    spectrum_entry_id: str = Field(..., description='NOMAD entry ID of the source XPS spectrum.')
