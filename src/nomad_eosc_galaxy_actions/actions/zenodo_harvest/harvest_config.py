# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Harvest configuration: what to harvest and where to convert it.

The settings ship with the Action in `config/harvest_config.json`. A NOMAD
installation can use its own file instead (entry point `harvest_config_path`).
Every file carries a `schema_version`, and older versions are upgraded on load.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Version of the harvest config layout (keys and their meaning), not of the
# values. When the layout changes: bump it and add an upgrade to UPGRADES.
HARVEST_CONFIG_SCHEMA_VERSION = 3
DEFAULT_HARVEST_CONFIG = Path(__file__).parent / "config" / "harvest_config.json"


def _add_setting(content: dict[str, Any], key: str, value: Any) -> dict[str, Any]:
    """Add a setting that a newer version introduced; refuse it if already there."""
    if key in content:
        raise HarvestConfigError(
            f"schema_version {content['schema_version']} has no setting {key!r}; "
            "check the schema_version of the file"
        )
    return content | {key: value}


def _add_nomad_url(content: dict[str, Any]) -> dict[str, Any]:
    """Version 2 adds `nomad_url`; empty keeps projects in the local NOMAD."""
    return _add_setting(content, "nomad_url", "")


def _add_state_dir(content: dict[str, Any]) -> dict[str, Any]:
    """Version 3 adds `state_dir`; empty keeps the states in NOMAD's artifacts."""
    return _add_setting(content, "state_dir", "")


# UPGRADES[n] turns the content of a version-n file into version n + 1.
UPGRADES: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _add_nomad_url,
    2: _add_state_dir,
}


class HarvestConfigError(ValueError):
    """The harvest config cannot be used: bad JSON, schema version or setting."""


class HarvestConfig(BaseModel):
    """Settings of a harvest run, read from a harvest config file."""

    # Unknown keys are errors, so a typo in the file does not go unnoticed.
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[HARVEST_CONFIG_SCHEMA_VERSION] = (
        HARVEST_CONFIG_SCHEMA_VERSION
    )
    zenodo_url: str = Field(
        description="Base URL of the Zenodo (InvenioRDM) instance to search."
    )
    zenodo_query: str = Field(
        description="Zenodo search query (Elasticsearch query string syntax)."
    )
    zenodo_request_delay: float = Field(
        ge=0,
        description=(
            "Seconds to wait between search pages. Zenodo allows 30 requests "
            "per minute (https://developers.zenodo.org/#rate-limiting)."
        ),
    )
    # TODO: Add all the file extensions once we test for .sxm files in the
    # development workflow.
    raw_extensions: list[str] = Field(
        description="File extensions converted to NeXus (role 'raw')."
    )
    context_extensions: list[str] = Field(
        description=(
            "File extensions that describe the experiment (role 'context'); "
            "passed along with every raw file of the same record."
        )
    )
    ignore_patterns: list[str] = Field(
        description="Glob patterns of files that are neither raw nor context."
    )
    licenses: list[str] = Field(
        description=(
            "Zenodo license IDs (as in the records API's metadata.license.id, "
            "e.g. 'cc-zero' for CC0) that allow re-publication in NOMAD."
        )
    )
    max_archive_bytes: int = Field(
        gt=0,
        description="Archives larger than this are skipped (reported as too_large).",
    )
    max_uncompressed_bytes: int = Field(
        gt=0,
        description="Upper limit for the extracted size of one archive (zip bombs).",
    )
    galaxy_url: str = Field(description="Base URL of the Galaxy instance to use.")
    galaxy_workflow_id: str = Field(
        description=(
            "ID of the harvest workflow in the acting Galaxy account. Until the "
            "real tools exist, this points to a placeholder workflow."
        )
    )
    nomad_url: str = Field(
        description=(
            "NOMAD API URL (ending in /api/v1) where projects are created, "
            "using the worker's NOMAD_PAT. Empty: the NOMAD that runs the Action."
        )
    )
    state_dir: str = Field(
        description=(
            "Folder of the per-user harvest states and the checksum index, e.g. a "
            "mounted volume. Empty: NOMAD's persistent action artifacts folder "
            "(<fs.actions>/artifacts/zenodo_harvest)."
        )
    )


def upgrade_content(
    content: dict[str, Any],
    *,
    current: int = HARVEST_CONFIG_SCHEMA_VERSION,
    upgrades: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = UPGRADES,
) -> dict[str, Any]:
    """Bring config content of any supported version to version `current`.

    The steps run in memory; the file itself is never rewritten.

    Raises:
        HarvestConfigError: the version is missing, not a positive integer,
            newer than `current`, has no upgrade step, or does not match the content.
    """
    version = content.get("schema_version")
    if type(version) is not int or version < 1:
        raise HarvestConfigError(
            f"schema_version must be a positive integer, got {version!r}"
        )
    if version > current:
        raise HarvestConfigError(
            f"schema_version {version} is newer than supported ({current}); "
            "update nomad-eosc-galaxy-actions"
        )
    while version < current:
        if version not in upgrades:
            raise HarvestConfigError(f"no upgrade from schema_version {version}")
        content = upgrades[version](dict(content))
        version += 1
        content["schema_version"] = version
    return content


def upgrade(content: dict[str, Any]) -> HarvestConfig:
    """Validate config content, upgrading it first when it has an older version.

    Raises:
        HarvestConfigError: unsupported version, or a setting is missing,
            unknown or invalid; the message names each bad setting.
    """
    content = upgrade_content(content)
    try:
        return HarvestConfig.model_validate(content)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or 'file'}: {e['msg']}"
            for e in error.errors()
        )
        raise HarvestConfigError(problems) from error


def load_harvest_config(path: str | Path | None = None) -> HarvestConfig:
    """Read, upgrade and validate a harvest config file.

    Args:
        path: config file; the one shipped with the Action if None.

    Raises:
        FileNotFoundError: the file does not exist.
        HarvestConfigError: the file is not valid JSON or not a valid config;
            the message starts with the file path.
    """
    path = Path(path or DEFAULT_HARVEST_CONFIG)
    try:
        content = json.loads(path.read_text("utf-8"))
        if not isinstance(content, dict):
            raise HarvestConfigError("must hold a JSON object")
        return upgrade(content)
    except json.JSONDecodeError as error:
        raise HarvestConfigError(f"{path}: not valid JSON ({error})") from error
    except HarvestConfigError as error:
        raise HarvestConfigError(f"{path}: {error}") from error
