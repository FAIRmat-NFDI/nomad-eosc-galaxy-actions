# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import pytest


@pytest.mark.skip(
    reason=(
        "Never adapted from the cookiecutter-nomad-plugin template: "
        "actions.simple_action (SimpleWorkflow/SimpleWorkflowInput/greet) does not "
        "exist in this plugin, which implements actions.find_peaks instead. No "
        "equivalent WorkflowEnvironment test exists yet for FindPeaksWorkflow."
    )
)
def test_simple_workflow():
    pass
