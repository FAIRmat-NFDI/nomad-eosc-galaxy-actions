# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Per-user secrets of an Action: from the Action form, else the worker.

A `SecretStr` form field is NOMAD's "Individual user secrets" pattern, the
worker's environment variable its "Institute-wide secrets" fallback
(https://docs.nomad-lab.eu/howto/plugins/types/actions.html#individual-user-secrets).
Every secret goes through `resolve_secret`, so a later NOMAD credential store
only changes this module.

Framework-agnostic: no NOMAD or Temporal imports.
"""

import os
from collections.abc import Mapping

from pydantic import SecretStr

# The worker's environment variable per service, used when the form is empty.
ENV_VARS = {"galaxy": "GALAXY_API_KEY", "zenodo": "ZENODO_TOKEN"}


class MissingSecretError(ValueError):
    """A required secret is neither in the form nor in the worker's environment."""


def resolve_secret(
    service: str,
    form_value: SecretStr | None,
    *,
    required: bool = True,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """The secret for `service`: the form value, else the environment variable.

    Raises:
        MissingSecretError: `required`, but neither is set.
    """
    if form_value is not None and form_value.get_secret_value():
        return form_value.get_secret_value()
    name = ENV_VARS[service]
    value = (os.environ if environ is None else environ).get(name)
    if value:
        return value
    if required:
        raise MissingSecretError(
            f"No {service} secret in the Action form, and no {name} "
            "environment variable set on the worker."
        )
    return None


def reveal(value: SecretStr | None) -> str | None:
    """The plain value, for `field_serializer(..., when_used="json")`.

    Temporal sends inputs as JSON; without this the activity would get the
    masked "**********" instead of the secret.
    """
    return value.get_secret_value() if value is not None else None
