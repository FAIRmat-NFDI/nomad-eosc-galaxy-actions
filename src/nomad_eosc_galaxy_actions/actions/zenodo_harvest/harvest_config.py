# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Harvest configuration: what to harvest and where to convert it.

The settings ship with the Action in `config/harvest_config.json`. A NOMAD
installation can change some of them with its own file (entry point
`harvest_config_path`): only the keys in that file replace the shipped ones.
The shipped file carries a `schema_version`; older versions are upgraded on load.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

# Version of the harvest config layout (keys and their meaning), not of the
# values. When the layout changes: bump it and add an upgrade to UPGRADES.
HARVEST_CONFIG_SCHEMA_VERSION = 4
DEFAULT_HARVEST_CONFIG = Path(__file__).parent / "config" / "harvest_config.json"
ZENODO_HARVEST_ACTION = (
    "nomad_eosc_galaxy_actions.actions.zenodo_harvest:zenodo_harvest_action"
)
# A multimodal model of LLM Hub on usegalaxy.eu: it gave the best ELN in a comparison
# (2026-11-12).
DEFAULT_LLM_MODEL = "mistral-medium-3.5-e-infra.cz"


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


def _add_eln_settings(content: dict[str, Any]) -> dict[str, Any]:
    """Version 4 adds the ELN settings: the run's user and the LLM Hub model."""
    content = _add_setting(content, "eln_user", True)
    return _add_setting(content, "llm_model", DEFAULT_LLM_MODEL)


# UPGRADES[n] turns the content of a version-n file into version n + 1.
UPGRADES: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _add_nomad_url,
    2: _add_state_dir,
    3: _add_eln_settings,
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
    # The pynxtools-spm reader's formats; Bruker text exports as `.spm.txt`, so a
    # readme `.txt` stays context (raw is matched first).
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
    eln_user: bool = Field(
        description=(
            "Write the NOMAD user who runs the harvest (name, email, affiliation) "
            "into the ELNs; it is sent to Galaxy in the work list."
        )
    )
    llm_model: str = Field(
        description=(
            "LLM Hub multimodal model of the ELN step when the form leaves it "
            "empty: a value of the server's genai_models table, e.g. "
            "mistral-medium-3.5-e-infra.cz."
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


def _read_object(path: Path) -> dict[str, Any]:
    """The JSON object in a config file."""
    try:
        content = json.loads(path.read_text("utf-8"))
    except json.JSONDecodeError as error:
        raise HarvestConfigError(f"not valid JSON ({error})") from error
    if not isinstance(content, dict):
        raise HarvestConfigError("must hold a JSON object")
    return content


def overlay_settings(content: dict[str, Any]) -> dict[str, Any]:
    """The settings of an installation file, without `schema_version`.

    A file with an older `schema_version` is upgraded first, but only its own
    keys are kept: settings an upgrade adds must not reset the shipped values.
    """
    keys = set(content) - {"schema_version"}
    if "schema_version" in content:
        content = upgrade_content(content)
    return {key: content[key] for key in keys}


def load_harvest_config(path: str | Path | None = None) -> HarvestConfig:
    """The shipped harvest config, with the settings of `path` in place of its own.

    Args:
        path: an installation file that may hold only some settings; None or
            empty gives the shipped config. A list in it replaces the shipped list.

    Raises:
        FileNotFoundError: a file does not exist.
        HarvestConfigError: a file is not valid JSON or gives a bad or unknown
            setting; the message starts with the file path.
    """
    shipped_path = Path(DEFAULT_HARVEST_CONFIG)
    try:
        shipped = upgrade(_read_object(shipped_path))
    except HarvestConfigError as error:
        raise HarvestConfigError(f"{shipped_path}: {error}") from error
    if not path or Path(path).resolve() == shipped_path.resolve():
        return shipped
    path = Path(path)
    try:
        settings = overlay_settings(_read_object(path))
        return upgrade(shipped.model_dump() | settings)
    except HarvestConfigError as error:
        raise HarvestConfigError(f"{path}: {error}") from error


def installation_config() -> HarvestConfig:
    """This installation's harvest config: the entry point's file over the shipped one.

    Before NOMAD loads its plugins (scripts, some tests), the shipped config.
    """
    from nomad.config import config  # noqa: PLC0415

    if config.plugins is None:
        return load_harvest_config()
    entry_point = config.get_plugin_entry_point(ZENODO_HARVEST_ACTION)
    return load_harvest_config(entry_point.harvest_config_path)
