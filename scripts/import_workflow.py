# /// script
# dependencies = ["bioblend"]
# ///
# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""One-time setup: import the XPS peak finding workflow into a Galaxy account.

The plugin calls an already-imported workflow by ID (see
`FindPeaksActionEntryPoint.galaxy_workflow_id` in
`actions/find_peaks/__init__.py`) -- it never imports the workflow itself.
Run this once per Galaxy account the Action will use, and put the printed
workflow_id into that config field.

Usage:
    GALAXY_API_KEY=... uv run scripts/import_workflow.py
"""

import os
import sys
from pathlib import Path

from bioblend.galaxy import GalaxyInstance

GALAXY_URL = 'https://usegalaxy.eu'
WORKFLOW_NAME = 'XPS peak finding'
WORKFLOW_FILE = (
    Path(__file__).resolve().parent.parent
    / 'src'
    / 'nomad_eosc_galaxy_actions'
    / 'actions'
    / 'find_peaks'
    / 'data'
    / 'Galaxy-Workflow-XPS_peak_finding.ga'
)


def main() -> None:
    api_key = os.environ.get('GALAXY_API_KEY')
    if not api_key:
        print('Set GALAXY_API_KEY first.', file=sys.stderr)
        raise SystemExit(1)

    gi = GalaxyInstance(url=GALAXY_URL, key=api_key)

    existing = gi.workflows.get_workflows(name=WORKFLOW_NAME)
    if existing:
        print(f'Already imported as workflow_id: {existing[0]["id"]}')
        if len(existing) > 1:
            print(
                f'Note: {len(existing)} workflows named "{WORKFLOW_NAME}" exist in this '
                'account -- using the first; check for duplicates by hand if that\'s wrong.',
                file=sys.stderr,
            )
        return

    imported = gi.workflows.import_workflow_from_local_path(str(WORKFLOW_FILE))
    print(f'Imported workflow_id: {imported["id"]}')
    print('Set this as galaxy_workflow_id for the find_peaks action entry point.')


if __name__ == '__main__':
    main()
