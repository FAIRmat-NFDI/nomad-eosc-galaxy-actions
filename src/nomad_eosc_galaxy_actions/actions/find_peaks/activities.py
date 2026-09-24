# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Activities for the find-peaks action.

Each activity is a thin, idempotent-as-possible wrapper, including file I/O
and NOMAD API calls ; the actual Galaxy connection lives in `galaxy_client.py`
so it stays testable without a Temporal worker.
"""

import os
from pathlib import Path

from temporalio import activity

from nomad_eosc_galaxy_actions import galaxy_client
from nomad_eosc_galaxy_actions.actions.find_peaks.models import (
    CreateResultEntryInput,
    CreateResultEntryResult,
    DownloadGalaxyResultInput,
    PollGalaxyInvocationInput,
    PollGalaxyInvocationResult,
    ReadSpectrumInput,
    RunGalaxyWorkflowInput,
    RunGalaxyWorkflowResult,
)
from nomad_eosc_galaxy_actions.actions.find_peaks.resolve import resolve_spectrum

# Step layout of Galaxy-Workflow-XPS_peak_finding.ga — specific to this one
# workflow, not to galaxy_client's generic upload_and_invoke().
_WORKFLOW_INPUT_STEP = "0"
_WORKFLOW_TOOL_STEP = "1"


def _galaxy_entry_point():
    from nomad.config import config  # noqa: PLC0415

    return config.get_plugin_entry_point(
        "nomad_eosc_galaxy_actions.actions.find_peaks:find_peaks_action"
    )


@activity.defn
def read_spectrum(data: ReadSpectrumInput) -> str:
    """Copy the spectrum's raw file out of upload storage into this action
    instance's artifacts directory, and return the local path.

    Resolves upload_id/mainfile fresh from spectrum_entry_id (not passed in),
    so a retry always sees current metadata rather than a value captured once
    at trigger time.
    """
    from nomad.actions.manager import action_instance_artifacts_dir  # noqa: PLC0415
    from nomad.uploads import get_upload_files  # noqa: PLC0415

    spectrum = resolve_spectrum(data.spectrum_entry_id, data.user_id)

    artifacts_dir = action_instance_artifacts_dir(data.action_instance_id)
    output_path = os.path.join(artifacts_dir, os.path.basename(spectrum.mainfile))

    upload_files = get_upload_files(spectrum.upload_id, data.user_id)
    with upload_files.raw_file(spectrum.mainfile, "rb") as source:
        with open(output_path, "wb") as target:
            target.write(source.read())

    return output_path


@activity.defn
def run_galaxy_workflow(data: RunGalaxyWorkflowInput) -> RunGalaxyWorkflowResult:
    """Upload the spectrum to Galaxy and invoke the peak-finding workflow."""
    entry_point = _galaxy_entry_point()

    peak_detection: dict[str, float] = {}
    if data.prominence is not None:
        peak_detection["prominence"] = data.prominence
    if data.distance is not None:
        peak_detection["distance"] = data.distance
    if data.height is not None:
        peak_detection["height"] = data.height

    result = galaxy_client.upload_and_invoke(
        api_key=data.galaxy_api_key.get_secret_value(),
        workflow_id=entry_point.galaxy_workflow_id,
        file_path=data.spectrum_path,
        # TODO: pass file_type='auto' once galaxyproject/galaxy#23273 lands —
        # NXxps is missing from <sniffers>, so auto-detection currently
        # resolves a plain .nxs upload to generic h5, not nxxps.
        file_type="nxxps",
        workflow_input_step=_WORKFLOW_INPUT_STEP,
        tool_params={_WORKFLOW_TOOL_STEP: {"peak_detection": peak_detection}}
        if peak_detection
        else None,
        history_name=f"find-peaks-{Path(data.spectrum_path).stem}",
        galaxy_url=entry_point.galaxy_url,
    )
    return RunGalaxyWorkflowResult(**result)


@activity.defn
def poll_galaxy_invocation(
    data: PollGalaxyInvocationInput,
) -> PollGalaxyInvocationResult:
    """Check the invocation's status once. Called repeatedly by the workflow,
    which sleeps between calls — polling does not block inside one activity."""
    entry_point = _galaxy_entry_point()
    result = galaxy_client.poll_invocation(
        api_key=data.galaxy_api_key.get_secret_value(),
        invocation_id=data.invocation_id,
        galaxy_url=entry_point.galaxy_url,
    )
    return PollGalaxyInvocationResult(**result)


@activity.defn
def download_galaxy_result(data: DownloadGalaxyResultInput) -> str:
    """Download the annotated NeXus file back to this action instance's
    artifacts directory, and return the local path."""
    from nomad.actions.manager import action_instance_artifacts_dir  # noqa: PLC0415

    entry_point = _galaxy_entry_point()
    artifacts_dir = action_instance_artifacts_dir(data.action_instance_id)
    output_path = os.path.join(artifacts_dir, "result.nxs")
    return galaxy_client.download_dataset(
        api_key=data.galaxy_api_key.get_secret_value(),
        dataset_id=data.dataset_id,
        output_path=output_path,
        galaxy_url=entry_point.galaxy_url,
    )


@activity.defn
def create_result_entry(data: CreateResultEntryInput) -> CreateResultEntryResult:
    """Add the downloaded result file to the *same* upload as the original
    spectrum and process it into a new entry.

    Simpler than creating a separate upload, and the natural place for it:
    NOMAD entries stay editable until the upload is published, so adding a
    raw file this way is ordinary processing, not a workaround. Requires the
    original upload to still be unpublished — `put_file_and_process_local`
    asserts this itself.
    """
    from nomad.uploads import get_upload  # noqa: PLC0415

    spectrum = resolve_spectrum(data.spectrum_entry_id, data.user_id)

    # put_file_and_process_local() takes the destination filename from
    # os.path.basename(path) and copies it into the upload's raw storage
    # itself — rename our downloaded copy first so it doesn't collide with
    # a result from a different spectrum's run landing in the same target_dir.
    renamed_path = os.path.join(
        os.path.dirname(data.result_path), f"peaks_{data.spectrum_entry_id}.nxs"
    )
    # Retry-safe: a prior attempt of this same activity may have already
    # renamed the file before failing on a later step (put_file_and_process_local
    # itself already tolerates being called again — its own docstring says an
    # existing target path is overwritten, not rejected).
    if os.path.exists(data.result_path):
        os.replace(data.result_path, renamed_path)
    elif not os.path.exists(renamed_path):
        raise FileNotFoundError(
            f"Neither {data.result_path} nor {renamed_path} exist. "
            "Cannot recover the downloaded result on retry."
        )

    upload = get_upload(spectrum.upload_id, data.user_id)
    entry = upload.put_file_and_process_local(renamed_path, target_dir="galaxy_results")
    if entry is None:
        # No parser matched — most likely this NOMAD deployment doesn't have
        # pynxtools' NeXus parser entry point enabled. The raw file is still
        # in the upload either way; it just isn't processed into an entry.
        activity.logger.warning(
            "No parser matched %s after adding it to upload %s. The file "
            "is in the upload but no entry was created.",
            renamed_path,
            spectrum.upload_id,
        )
        return CreateResultEntryResult(entry_id=None)
    return CreateResultEntryResult(entry_id=entry.entry_id)
