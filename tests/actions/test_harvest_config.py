# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import json
import re

import pytest
from pydantic import ValidationError

from nomad_eosc_galaxy_actions.actions.zenodo_harvest.harvest_config import (
    DEFAULT_HARVEST_CONFIG,
    HARVEST_CONFIG_SCHEMA_VERSION,
    HarvestConfig,
    HarvestConfigError,
    load_harvest_config,
    upgrade,
    upgrade_content,
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
    "change, message",
    [
        ({"zenodo_request_delay": -1}, "zenodo_request_delay: Input should be"),
        ({"max_archive_bytes": 0}, "max_archive_bytes: Input should be"),
        # typo: unknown key  cspell:disable-next-line
        ({"licences": ["cc-zero"]}, "licences: Extra inputs are not permitted"),
    ],
)
def test_load_rejects_invalid_settings(tmp_path, shipped, change, message):
    path = _write(tmp_path, shipped | change)
    with pytest.raises(
        HarvestConfigError, match=f"^{re.escape(str(path))}: .*{message}"
    ):
        load_harvest_config(path)


def test_load_rejects_missing_setting(tmp_path, shipped):
    del shipped["zenodo_query"]
    path = _write(tmp_path, shipped)
    with pytest.raises(
        HarvestConfigError, match=f"^{re.escape(str(path))}: zenodo_query: Field"
    ):
        load_harvest_config(path)


@pytest.mark.parametrize(
    "text, message",
    [
        ('{"schema_version": 2,', "not valid JSON"),
        ("", "not valid JSON"),
        ("[]", "must hold a JSON object"),
    ],
)
def test_load_rejects_a_file_that_is_no_config(tmp_path, text, message):
    path = tmp_path / "harvest_config.json"
    path.write_text(text)
    with pytest.raises(HarvestConfigError, match=f"^{re.escape(str(path))}: {message}"):
        load_harvest_config(path)


def test_load_names_the_file_for_a_bad_version(tmp_path, shipped):
    path = _write(tmp_path, shipped | {"schema_version": 0})
    with pytest.raises(
        HarvestConfigError, match=f"^{re.escape(str(path))}: schema_version must"
    ):
        load_harvest_config(path)


@pytest.mark.parametrize("version", [None, 0, -1, "1", 1.0, True])
def test_upgrade_rejects_bad_version(version):
    content = {} if version is None else {"schema_version": version}
    with pytest.raises(HarvestConfigError, match="positive integer"):
        upgrade(content)


def test_upgrade_rejects_newer_version():
    with pytest.raises(HarvestConfigError, match="newer than supported"):
        upgrade({"schema_version": HARVEST_CONFIG_SCHEMA_VERSION + 1})


def test_upgrade_validates_current_version_as_it_is(shipped):
    assert upgrade_content(shipped) == shipped
    assert upgrade(shipped).model_dump(mode="json") == shipped


def test_model_accepts_only_the_current_version(shipped):
    with pytest.raises(ValidationError, match="schema_version"):
        HarvestConfig.model_validate(shipped | {"schema_version": 1})


# Fake upgrade steps for an imagined version 3 layout: v1 -> v2 renames a key,
# v2 -> v3 adds one. Real steps are added to UPGRADES when the layout changes.
FAKE_UPGRADES = {
    1: lambda c: {**{k: v for k, v in c.items() if k != "query"}, "q": c["query"]},
    2: lambda c: c | {"page_size": 25},
}


def test_upgrade_runs_each_step_in_order():
    content = {"schema_version": 1, "query": "STM"}

    result = upgrade_content(content, current=3, upgrades=FAKE_UPGRADES)

    assert result == {"schema_version": 3, "q": "STM", "page_size": 25}
    assert content == {"schema_version": 1, "query": "STM"}  # input untouched


def test_upgrade_from_middle_version():
    result = upgrade_content(
        {"schema_version": 2, "q": "STM"}, current=3, upgrades=FAKE_UPGRADES
    )
    assert result == {"schema_version": 3, "q": "STM", "page_size": 25}


def _version_1(shipped: dict) -> dict:
    content = {k: v for k, v in shipped.items() if k != "nomad_url"}
    return content | {"schema_version": 1}


def test_old_file_is_upgraded_and_never_rewritten(tmp_path, shipped):
    path = _write(tmp_path, _version_1(shipped))
    before = (path.read_bytes(), path.stat().st_mtime_ns)

    config = load_harvest_config(path)

    assert config.schema_version == HARVEST_CONFIG_SCHEMA_VERSION
    assert config.nomad_url == ""
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


def test_old_version_with_a_newer_setting_is_refused(tmp_path, shipped):
    # A version 1 file cannot have nomad_url; upgrading must not overwrite it.
    path = _write(tmp_path, _version_1(shipped) | {"nomad_url": "https://x/api/v1"})
    with pytest.raises(
        HarvestConfigError,
        match=f"^{re.escape(str(path))}: schema_version 1 has no setting 'nomad_url'",
    ):
        load_harvest_config(path)


def test_upgrade_missing_step():
    with pytest.raises(HarvestConfigError, match="no upgrade from schema_version 1"):
        upgrade_content({"schema_version": 1}, current=2, upgrades={})
