# /// script
# dependencies = ["bioblend"]
# ///
# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Standalone check that the Galaxy round trip works -- no NOMAD, no Temporal.

Exercises exactly what the Action's activities do (upload, invoke, poll,
download), via the same galaxy_client module, so a failure here rules out
NOMAD/Temporal as the cause before debugging the full Action.

Usage:
    GALAXY_API_KEY=... GALAXY_WORKFLOW_ID=... uv run scripts/test_galaxy_roundtrip.py
    GALAXY_API_KEY=... GALAXY_WORKFLOW_ID=... uv run scripts/test_galaxy_roundtrip.py path/to/spectrum.nxs

GALAXY_WORKFLOW_ID is this account's own copy of the workflow, from
scripts/import_workflow.py -- not bgruening's published one.
"""

import importlib.util
import os
import sys
import time
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parent.parent / 'src'
DEFAULT_SPECTRUM = (
    Path(__file__).resolve().parent.parent
    / 'examples'
    / 'galaxy_demo'
    / 'example_data'
    / 'vms_regular_ref.nxs'
)
POLL_INTERVAL_SECONDS = 5
MAX_POLLS = 120  # 10 minutes


def _load_galaxy_client():
    """Import galaxy_client.py directly by path -- avoids needing nomad-lab
    installed just to run this script; galaxy_client.py itself only needs
    bioblend."""
    module_path = PACKAGE_SRC / 'nomad_eosc_galaxy_actions' / 'galaxy_client.py'
    spec = importlib.util.spec_from_file_location('galaxy_client', module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    api_key = os.environ.get('GALAXY_API_KEY')
    workflow_id = os.environ.get('GALAXY_WORKFLOW_ID')
    if not api_key or not workflow_id:
        print('Set GALAXY_API_KEY and GALAXY_WORKFLOW_ID first.', file=sys.stderr)
        raise SystemExit(1)

    spectrum_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SPECTRUM
    galaxy_client = _load_galaxy_client()

    print(f'Uploading {spectrum_path.name} and invoking workflow {workflow_id}...')
    run_result = galaxy_client.upload_and_invoke(
        api_key=api_key,
        workflow_id=workflow_id,
        file_path=spectrum_path,
        file_type='nxxps',
        tool_params={'1': {'peak_detection': {'prominence': 10000, 'distance': 20}}},
    )
    print(f'  history_id={run_result["history_id"]} invocation_id={run_result["invocation_id"]}')

    print('Polling...')
    for i in range(MAX_POLLS):
        poll_result = galaxy_client.poll_invocation(api_key, run_result['invocation_id'])
        print(f'  [{i * POLL_INTERVAL_SECONDS}s] state={poll_result["state"]} done={poll_result["done"]}')
        if poll_result['done']:
            break
        time.sleep(POLL_INTERVAL_SECONDS)
    else:
        print('Timed out waiting for the workflow to finish.', file=sys.stderr)
        raise SystemExit(1)

    if poll_result['state'] != 'ok':
        print(f'Workflow did not succeed: {poll_result["state"]}', file=sys.stderr)
        raise SystemExit(1)

    print('Outputs:', poll_result['outputs'])
    output_dataset_id = poll_result['outputs']['output_nxs']

    download_path = Path(__file__).resolve().parent / 'roundtrip_result.nxs'
    galaxy_client.download_dataset(api_key, output_dataset_id, download_path)
    print(f'Downloaded annotated NeXus to {download_path}')


if __name__ == '__main__':
    main()
