# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Resolving inputs the Action's activities need but only get an id/None for.

One shared place for this so each activity (resolved fresh on each retry,
not threaded through as separately-passed fields that could drift out of
sync) agrees on exactly what counts as a valid spectrum or API key.
"""

import os
from dataclasses import dataclass

from pydantic import SecretStr


@dataclass
class ResolvedSpectrum:
    upload_id: str
    mainfile: str


def resolve_spectrum(entry_id: str, user_id: str) -> ResolvedSpectrum:
    """Resolve a spectrum's entry_id to its upload_id and mainfile path.

    Enforces the requesting user's access and that the mainfile is a NeXus
    file, so a bad entry_id fails clearly and immediately (as an early
    activity failure) rather than deep inside a Galaxy upload with a file
    it can't make sense of.

    Raises:
        ValueError: no such entry, or its mainfile isn't a `.nxs` file.
        PermissionError: the user isn't authorized for that entry's upload.
    """
    from nomad.processing.data import Entry  # noqa: PLC0415
    from nomad.uploads import get_upload  # noqa: PLC0415

    entry = Entry.objects(entry_id=entry_id).first()
    if entry is None:
        raise ValueError(f"No entry found with entry_id {entry_id}.")

    get_upload(entry.upload_id, user_id)  # raises PermissionError if not authorized

    if not entry.mainfile or not entry.mainfile.endswith(".nxs"):
        raise ValueError(
            f"Entry {entry_id} has mainfile {entry.mainfile!r}, which is not a "
            "NeXus (.nxs) file — find-peaks only works on NeXus spectra."
        )

    return ResolvedSpectrum(upload_id=entry.upload_id, mainfile=entry.mainfile)


def resolve_api_key(secret: SecretStr | None) -> str:
    """Resolve the Galaxy API key to use: the one passed in the trigger's
    input if given, otherwise the worker's own GALAXY_API_KEY environment
    variable (an institute-wide/shared key, per the "Institute-wide secrets"
    pattern for NOMAD Actions).

    Raises:
        ValueError: neither a per-trigger key nor the environment variable
            is set.
    """
    if secret is not None:
        return secret.get_secret_value()

    api_key = os.environ.get("GALAXY_API_KEY")
    if not api_key:
        raise ValueError(
            "No Galaxy API key given, and no GALAXY_API_KEY environment "
            "variable set on the worker."
        )
    return api_key
