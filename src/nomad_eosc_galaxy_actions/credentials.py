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
    worker: bool = True,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """The secret for `service`: the form value, else the environment variable.

    `worker=False` never uses the environment variable, e.g. for a server
    that the form chose: the worker's secret belongs to the configured one.

    Raises:
        MissingSecretError: `required`, but no allowed value is set.
    """
    name = ENV_VARS[service]
    if form_value is not None and form_value.get_secret_value():
        return form_value.get_secret_value()
    value = (os.environ if environ is None else environ).get(name) if worker else None
    if value:
        return value
    if required:
        reason = (
            f"no {name} environment variable set on the worker"
            if worker
            else f"the worker's {name} is only sent to the harvest config's server"
        )
        raise MissingSecretError(
            f"No {service} secret in the Action form, and {reason}."
        )
    return None


def reveal(value: SecretStr | None) -> str | None:
    """The plain value, for `field_serializer(..., when_used="json")`.

    Temporal sends inputs as JSON; without this the activity would get the
    masked "**********" instead of the secret. NOMAD encrypts these inputs,
    except in development mode without `temporal.payload_codec_key`.
    """
    return value.get_secret_value() if value is not None else None
