# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Generic bioblend wrapper for driving a Galaxy workflow from NOMAD.

Deliberately framework- and technique-agnostic (no Temporal/NOMAD imports,
no tool/workflow assumptions) -- upload a file, invoke an already-imported
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

_TERMINAL_ERROR_JOB_STATES = {
    "error",
    "failed",
    "deleted",
    "deleting",
    "stopped",
    "stopping",
    "paused",
}


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
    history_name: str | None = None,
    galaxy_url: str = GALAXY_URL,
) -> dict[str, str]:
    """Upload a file and invoke a workflow on it.

    Args:
        workflow_id: this account's copy of the workflow (see module docstring).
        file_path: local path of the file to upload.
        file_type: Galaxy datatype to force on upload. Defaults to Galaxy's own
            auto-detection ('auto') -- pass the specific datatype explicitly
            when auto-detection can't be relied on (see the design doc's note
            on the `nxxps` sniffer bug for why XPS callers should).
        workflow_input_step: step index of the workflow's data-input step.
        tool_params: `{step_index: {param_name: value, ...}, ...}`, forwarded
            to `invoke_workflow`'s `params` as-is -- shape depends entirely on
            the target tool(s)' own parameters.
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
        to_posix_lines=False,  # do not touch line endings -- these are binary files
    )
    input_dataset_id = dataset["outputs"][0]["id"]

    invocation = gi.workflows.invoke_workflow(
        workflow_id,
        inputs={workflow_input_step: {"src": "hda", "id": input_dataset_id}},
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
    outputs maps output names to Galaxy dataset IDs. It is populated only
    when the invocation is complete and all jobs succeeded.

    Outputs are collected from two sources and merged:

    * Each job's own outputs, keyed by the tool's output parameter names.
    * invocation["outputs"], keyed by labels declared as workflow outputs
    via workflow_outputs in the .ga file. These override job outputs
    with the same name.
    """
    gi = get_instance(api_key, galaxy_url)
    invocation = gi.invocations.show_invocation(invocation_id)

    job_ids = [step["job_id"] for step in invocation["steps"] if step.get("job_id")]
    if not job_ids:
        return {"done": False, "state": invocation["state"], "outputs": None}

    jobs = [gi.jobs.show_job(job_id) for job_id in job_ids]
    states = {job["state"] for job in jobs}

    if states & _TERMINAL_ERROR_JOB_STATES:
        return {"done": True, "state": "error", "outputs": None}
    if states != {"ok"}:
        return {"done": False, "state": "running", "outputs": None}

    outputs: dict[str, str] = {}
    for job in jobs:
        outputs.update({name: ref["id"] for name, ref in job["outputs"].items()})
    outputs.update(
        {name: ref["id"] for name, ref in invocation.get("outputs", {}).items()}
    )
    return {"done": True, "state": "ok", "outputs": outputs}


def download_dataset(
    api_key: str, dataset_id: str, output_path: str | Path, galaxy_url: str = GALAXY_URL
) -> str:
    gi = get_instance(api_key, galaxy_url)
    gi.datasets.download_dataset(
        dataset_id, file_path=str(output_path), use_default_filename=False
    )
    return str(output_path)
