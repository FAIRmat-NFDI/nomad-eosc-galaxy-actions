# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Generic bioblend wrapper for driving a Galaxy workflow from NOMAD.

Deliberately framework- and technique-agnostic (no Temporal/NOMAD imports,
no tool/workflow assumptions) — upload a file, invoke an already-imported
workflow, poll it, download a result. Exercisable directly without a
running Temporal worker.

Workflows are imported into the acting Galaxy account *once*, but not by this
module. A workflow's ID is deployment configuration (see e.g.
`FindPeaksActionEntryPoint.galaxy_workflow_id`), not something looked up or
re-imported on every run; `scripts/import_workflow.py` does that one-time step.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bioblend.galaxy import GalaxyInstance

GALAXY_URL = "https://usegalaxy.eu"

# Job states that can still change, as in Planemo's `NON_TERMINAL_JOB_STATES`.
# `paused` is not one: a paused job waits for a user and never starts by itself.
_ACTIVE_JOB_STATES = {
    "new",
    "ready",
    "resubmitted",
    "upload",
    "waiting",
    "queued",
    "running",
    "deleting",
    "stopping",
}
_SUCCESS_JOB_STATES = {"ok", "skipped"}
# `completed` (Galaxy 26.1+): every job has ended. Older Galaxy stops at `scheduled`.
_ENDED_INVOCATION_STATES = {"scheduled", "completed"}
_FAILED_INVOCATION_STATES = {"failed", "cancelled"}


def get_instance(api_key: str, galaxy_url: str = GALAXY_URL) -> GalaxyInstance:
    return GalaxyInstance(url=galaxy_url, key=api_key)


def upload_and_invoke(  # noqa: PLR0913
    api_key: str,
    workflow_id: str,
    file_path: str | Path,
    *,
    file_type: str = "auto",
    workflow_input_step: str = "0",
    tool_params: dict[str, dict[str, Any]] | None = None,
    parameter_inputs: dict[str, Any] | None = None,
    history_name: str | None = None,
    galaxy_url: str = GALAXY_URL,
) -> dict[str, str]:
    """Upload a file and invoke a workflow on it.

    Args:
        workflow_id: this account's copy of the workflow (see module docstring).
        file_path: local path of the file to upload.
        file_type: Galaxy datatype to force on upload. Defaults to Galaxy's own
            auto-detection ('auto') — pass the specific datatype explicitly
            when auto-detection can't be relied on (see the design doc's note
            on the `nxxps` sniffer bug for why XPS callers should).
        workflow_input_step: step index of the workflow's data-input step.
        tool_params: `{step_index: {param_name: value, ...}, ...}`, forwarded
            to `invoke_workflow`'s `params` as-is — shape depends entirely on
            the target tool(s)' own parameters.
        parameter_inputs: `{step_index: value}` for the workflow's parameter
            inputs (text, boolean, ...); unset ones keep their default.
        history_name: defaults to the uploaded file's stem if not given.

    Returns:
        `{"history_id": ..., "invocation_id": ...}`.
    """
    gi = get_instance(api_key, galaxy_url)

    history = gi.histories.create_history(
        name=history_name or f"{Path(file_path).stem}-{workflow_id}"
    )
    dataset = gi.tools.upload_file(
        str(file_path),
        history["id"],
        file_type=file_type,
        to_posix_lines=False,  # do not touch line endings — these are binary files
    )
    input_dataset_id = dataset["outputs"][0]["id"]

    invocation = gi.workflows.invoke_workflow(
        workflow_id,
        inputs={
            workflow_input_step: {"src": "hda", "id": input_dataset_id},
            **(parameter_inputs or {}),
        },
        params=tool_params,
        history_id=history["id"],
        inputs_by="step_index",
    )
    return {"history_id": history["id"], "invocation_id": invocation["id"]}


def poll_invocation(
    api_key: str, invocation_id: str, galaxy_url: str = GALAXY_URL
) -> dict[str, Any]:
    """Check an invocation's status once (no sleeping; that's the caller's job).

    Returns {"done": bool, "state": str, "outputs": dict[str, str] | None}.
    The run is done when Galaxy has created all jobs and none can still
    change (see `_ACTIVE_JOB_STATES`). `state` is then:

    * `ok`: every job ended `ok` or `skipped`.
    * `partial`: some jobs failed or are paused. Only outputs whose
      datasets are `ok` are returned, so a step that never ran has none.
    * `error`: the invocation itself failed or was cancelled; no outputs.

    outputs maps output names to Galaxy dataset IDs. They come from each
    single-job step's outputs (keyed by the tool's output names), overridden by
    the workflow's labelled outputs (`invocation["outputs"]`, and
    `invocation["output_collections"]` with collection IDs).
    """
    gi = get_instance(api_key, galaxy_url)
    invocation = gi.invocations.show_invocation(invocation_id)
    if invocation["state"] in _FAILED_INVOCATION_STATES:
        return {"done": True, "state": "error", "outputs": None}
    if invocation["state"] not in _ENDED_INVOCATION_STATES:  # still scheduling
        return {"done": False, "state": invocation["state"], "outputs": None}

    # The job summary counts all jobs, also those of mapped steps (no `job_id`).
    states = set(gi.invocations.get_invocation_summary(invocation_id)["states"])
    if invocation["state"] == "scheduled" and (
        not states or states & _ACTIVE_JOB_STATES
    ):
        return {"done": False, "state": "running", "outputs": None}

    outputs: dict[str, str] = {}
    for step in invocation["steps"]:
        if step.get("job_id"):
            job = gi.jobs.show_job(step["job_id"])
            outputs.update({name: ref["id"] for name, ref in job["outputs"].items()})
    outputs.update(
        {name: ref["id"] for name, ref in invocation.get("outputs", {}).items()}
    )
    collections = {
        name: ref["id"]
        for name, ref in invocation.get("output_collections", {}).items()
    }
    if states <= _SUCCESS_JOB_STATES:
        return {"done": True, "state": "ok", "outputs": outputs | collections}

    # Galaxy creates the outputs of paused steps too, so keep only finished ones.
    outputs = {
        name: dataset_id
        for name, dataset_id in outputs.items()
        if gi.datasets.show_dataset(dataset_id)["state"] == "ok"
    }
    for name, collection_id in collections.items():
        collection = gi.dataset_collections.show_dataset_collection(collection_id)
        if collection.get("populated_state") == "ok":
            outputs[name] = collection_id
    return {"done": True, "state": "partial", "outputs": outputs}


def collection_elements(
    api_key: str, collection_id: str, galaxy_url: str = GALAXY_URL
) -> dict[str, str]:
    """Element identifiers → dataset IDs of a (flat) dataset collection."""
    gi = get_instance(api_key, galaxy_url)
    collection = gi.dataset_collections.show_dataset_collection(collection_id)
    return {
        element["element_identifier"]: element["object"]["id"]
        for element in collection["elements"]
    }


def download_dataset(
    api_key: str, dataset_id: str, output_path: str | Path, galaxy_url: str = GALAXY_URL
) -> str:
    gi = get_instance(api_key, galaxy_url)
    gi.datasets.download_dataset(
        dataset_id, file_path=str(output_path), use_default_filename=False
    )
    return str(output_path)
