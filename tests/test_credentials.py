# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import pytest
from pydantic import SecretStr

from nomad_eosc_galaxy_actions.credentials import (
    MissingSecretError,
    resolve_secret,
    reveal,
)

ENV = {"GALAXY_API_KEY": "worker-key", "ZENODO_TOKEN": "worker-token"}


def test_form_value_wins():
    assert resolve_secret("galaxy", SecretStr("form-key"), environ=ENV) == "form-key"


@pytest.mark.parametrize("form_value", [None, SecretStr("")])
def test_empty_form_falls_back_to_the_worker(form_value):
    assert resolve_secret("galaxy", form_value, environ=ENV) == "worker-key"
    assert resolve_secret("zenodo", form_value, environ=ENV) == "worker-token"


def test_missing_required_secret_names_the_variable():
    with pytest.raises(MissingSecretError, match="GALAXY_API_KEY"):
        resolve_secret("galaxy", None, environ={})


def test_missing_optional_secret_is_none():
    assert resolve_secret("zenodo", None, required=False, environ={}) is None
    assert (
        resolve_secret("zenodo", None, required=False, environ={"ZENODO_TOKEN": ""})
        is None
    )


def test_unknown_service_is_a_programming_error():
    with pytest.raises(KeyError):
        resolve_secret("unknown", None, environ=ENV)


def test_reveal():
    assert reveal(SecretStr("s")) == "s"
    assert reveal(None) is None
