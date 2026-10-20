# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import json

import pytest
from pydantic import ValidationError

from nomad_eosc_galaxy_actions.actions.zenodo_harvest.harvest_config import (
    DEFAULT_HARVEST_CONFIG,
    HARVEST_CONFIG_SCHEMA_VERSION,
    HarvestConfigError,
    load_harvest_config,
    upgrade,
)


@pytest.fixture
def shipped() -> dict:
    """Content of the config file shipped with the Action."""
    return json.loads(DEFAULT_HARVEST_CONFIG.read_text())


def _write(tmp_path, content: dict):
    path = tmp_path / "harvest_config.json"
    path.write_text(json.dumps(content))
    return path


def test_shipped_config_is_current_and_valid(shipped):
    config = load_harvest_config()

    assert shipped["schema_version"] == HARVEST_CONFIG_SCHEMA_VERSION
    assert config.schema_version == HARVEST_CONFIG_SCHEMA_VERSION
    assert ".sxm" in config.raw_extensions
    assert "cc-zero" in config.licenses


def test_load_from_own_path(tmp_path, shipped):
    path = _write(tmp_path, shipped | {"zenodo_query": "Nanonis"})
    assert load_harvest_config(path).zenodo_query == "Nanonis"
    assert load_harvest_config(str(path)).zenodo_query == "Nanonis"


def test_load_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_harvest_config(tmp_path / "missing.json")


@pytest.mark.parametrize(
    "change",
    [
        {"zenodo_request_delay": -1},  # below minimum
        {"max_archive_bytes": 0},  # must be positive
        {"licences": ["cc-zero"]},  # typo: unknown key  cspell:disable-line
    ],
)
def test_load_rejects_invalid_settings(tmp_path, shipped, change):
    with pytest.raises(ValidationError):
        load_harvest_config(_write(tmp_path, shipped | change))


def test_load_rejects_missing_setting(tmp_path, shipped):
    del shipped["zenodo_query"]
    with pytest.raises(ValidationError):
        load_harvest_config(_write(tmp_path, shipped))


@pytest.mark.parametrize("version", [None, 0, -1, "1", 1.0, True])
def test_upgrade_rejects_bad_version(version):
    content = {} if version is None else {"schema_version": version}
    with pytest.raises(HarvestConfigError, match="positive integer"):
        upgrade(content)


def test_upgrade_rejects_newer_version():
    with pytest.raises(HarvestConfigError, match="newer than supported"):
        upgrade({"schema_version": HARVEST_CONFIG_SCHEMA_VERSION + 1})


def test_upgrade_current_version_is_unchanged(shipped):
    assert upgrade(shipped) == shipped


# Fake upgrade steps for an imagined version 3 layout: v1 -> v2 renames a key,
# v2 -> v3 adds one. Real steps are added to UPGRADES when the layout changes.
FAKE_UPGRADES = {
    1: lambda c: {**{k: v for k, v in c.items() if k != "query"}, "q": c["query"]},
    2: lambda c: c | {"page_size": 25},
}


def test_upgrade_runs_each_step_in_order():
    content = {"schema_version": 1, "query": "STM"}

    result = upgrade(content, current=3, upgrades=FAKE_UPGRADES)

    assert result == {"schema_version": 3, "q": "STM", "page_size": 25}
    assert content == {"schema_version": 1, "query": "STM"}  # input untouched


def test_upgrade_from_middle_version():
    result = upgrade(
        {"schema_version": 2, "q": "STM"}, current=3, upgrades=FAKE_UPGRADES
    )
    assert result == {"schema_version": 3, "q": "STM", "page_size": 25}


def test_upgrade_missing_step():
    with pytest.raises(HarvestConfigError, match="no upgrade from schema_version 1"):
        upgrade({"schema_version": 1}, current=2, upgrades={})
