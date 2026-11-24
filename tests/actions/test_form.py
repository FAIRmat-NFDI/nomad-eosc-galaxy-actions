# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""The Action form (ZenodoHarvestWorkflowInput) and how its values reach a run."""

import json

import pytest
from pydantic import ValidationError

from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import apply_overrides
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.harvest_config import (
    DEFAULT_HARVEST_CONFIG,
    HarvestConfigError,
    load_harvest_config,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
    SHIPPED_CONFIG,
    ZENODO_QUERY_EXAMPLES,
    BuildWorkListInput,
    ConfigOverrides,
    ZenodoHarvestWorkflowInput,
)

MAX_DESCRIPTION = 120  # characters; the form shows each description under its field


def _form(**values) -> ZenodoHarvestWorkflowInput:
    return ZenodoHarvestWorkflowInput(user_id="u1", **values)


def test_form_defaults_are_the_shipped_config():
    properties = ZenodoHarvestWorkflowInput.model_json_schema()["properties"]

    for name in ConfigOverrides.model_fields:
        expected = getattr(SHIPPED_CONFIG, name) or None  # "" is shown empty
        assert properties[name].get("default") == expected, name
    assert SHIPPED_CONFIG == load_harvest_config(DEFAULT_HARVEST_CONFIG)


def test_form_defaults_without_a_config_value():
    form = _form()
    expected = (1, 1, 7, 1)
    assert (
        form.max_records,
        form.records_per_work_list,
        form.repeat_every_days,
        form.max_runs,
    ) == expected


def test_unchanged_defaults_leave_every_setting_to_the_config():
    form = _form()
    assert form.config_overrides() == dict.fromkeys(ConfigOverrides.model_fields)


def test_a_restarted_run_keeps_the_values():
    # A restart sends the previous run's input again, as JSON.
    form = _form(zenodo_query="AFM", max_records=3)

    restarted = ZenodoHarvestWorkflowInput.model_validate_json(form.model_dump_json())

    assert restarted == form
    assert restarted.config_overrides()["zenodo_query"] == "AFM"


def test_default_lists_are_not_shared():
    _form().raw_extensions.append(".xyz")
    assert _form().raw_extensions == SHIPPED_CONFIG.raw_extensions
    assert ".xyz" not in SHIPPED_CONFIG.raw_extensions


def test_notify_email_is_gone():
    properties = ZenodoHarvestWorkflowInput.model_json_schema()["properties"]
    assert "notify_email" not in properties
    # Input saved by an older version still loads; the value is dropped.
    assert not hasattr(_form(notify_email="name@example.org"), "notify_email")


def test_backend_settings_are_not_in_the_form():
    properties = ZenodoHarvestWorkflowInput.model_json_schema()["properties"]

    hidden = {"schema_version", "max_archive_bytes", "max_uncompressed_bytes"}
    hidden |= {"nomad_url", "state_dir", "eln_user"}
    assert not hidden & set(properties)
    assert set(ConfigOverrides.model_fields) <= set(properties)


def test_schema_shows_the_new_fields_and_short_descriptions():
    properties = ZenodoHarvestWorkflowInput.model_json_schema()["properties"]

    for name in ("raw_extensions", "context_extensions", "ignore_patterns", "licenses"):
        array = properties[name]["anyOf"][0]
        assert array == {"type": "array", "items": {"type": "string"}}, name
    assert "galaxy_workflow_id" in properties
    assert "run_number" not in properties
    for name, field in properties.items():
        if name == "user_id":
            continue
        assert len(field["description"]) <= MAX_DESCRIPTION, name
        if name not in ("galaxy_api_key", "zenodo_token"):
            assert "Example:" in field["description"], name


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ([".sxm"], [".sxm"]),
        (["SXM", " .dat ", "sxm"], [".sxm", ".dat"]),  # case, spaces, repeats
        (["..sm4", "", " "], [".sm4"]),  # empty entries are dropped
    ],
)
def test_raw_extensions_are_normalized(given, expected):
    assert _form(raw_extensions=given).raw_extensions == expected


def test_context_extensions_are_normalized():
    form = _form(context_extensions=["TXT", " .md", "txt", ""])
    assert form.context_extensions == [".txt", ".md"]


@pytest.mark.parametrize(
    "name", ["raw_extensions", "context_extensions", "ignore_patterns", "licenses"]
)
@pytest.mark.parametrize("given", [[], ["", " "], [" . "]])
def test_empty_lists_mean_not_set(name, given):
    if given == [" . "] and name in ("ignore_patterns", "licenses"):
        given = ["  "]  # "." is a valid pattern or ID, not an empty entry
    assert getattr(_form(**{name: given}), name) is None


def test_ignore_patterns_are_trimmed_and_keep_their_case():
    form = _form(ignore_patterns=[" __MACOSX/* ", "*.MP4", "*.MP4"])
    assert form.ignore_patterns == ["__MACOSX/*", "*.MP4"]


def test_licenses_are_lowercase():
    assert _form(licenses=[" CC-BY-4.0", "cc-by-4.0"]).licenses == ["cc-by-4.0"]


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("https://sandbox.zenodo.org/", "https://sandbox.zenodo.org"),
        (" http://localhost:8080 ", "http://localhost:8080"),
        ("  ", None),
    ],
)
def test_urls_are_trimmed(given, expected):
    form = _form(zenodo_url=given, galaxy_url=given, galaxy_api_key="key")
    assert (form.zenodo_url, form.galaxy_url) == (expected, expected)


@pytest.mark.parametrize("given", ["usegalaxy.eu", "ftp://usegalaxy.eu", "file:///x"])
@pytest.mark.parametrize("name", ["zenodo_url", "galaxy_url"])
def test_urls_need_http(name, given):
    with pytest.raises(ValidationError, match="starting with https://"):
        _form(**{name: given}, galaxy_api_key="key")


@pytest.mark.parametrize("key", [None, ""])
def test_own_galaxy_url_needs_own_key(key):
    with pytest.raises(ValidationError, match="also give your galaxy_api_key"):
        _form(galaxy_url="https://usegalaxy.org", galaxy_api_key=key)


@pytest.mark.parametrize("url", [SHIPPED_CONFIG.galaxy_url, "https://usegalaxy.eu/"])
def test_default_galaxy_url_needs_no_key(url):
    form = _form(galaxy_url=url)
    assert form.config_overrides()["galaxy_url"] is None  # the worker's key is used


def test_own_galaxy_url_with_own_key():
    form = _form(galaxy_url="https://usegalaxy.org", galaxy_api_key="key")
    assert form.galaxy_url == "https://usegalaxy.org"


def test_negative_delay_is_refused():
    with pytest.raises(ValidationError):
        _form(zenodo_request_delay=-1)


def test_empty_text_fields_mean_not_set():
    form = _form(galaxy_workflow_id="  ", zenodo_query=" ")
    assert (form.galaxy_workflow_id, form.zenodo_query) == (None, None)


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # Copied from a JSON file: decoded once.
        (
            r'"(\"scanning tunneling microscopy\" OR Nanonis) AND type:dataset"',
            '("scanning tunneling microscopy" OR Nanonis) AND type:dataset',
        ),
        (r' "\"STM\"" ', '"STM"'),  # outer spaces are trimmed first
        (r'" \"STM\" "', '"STM"'),  # inner spaces too
        (r'" \" "', '"'),  # a lone escaped quote
        # Kept as given.
        ('"scanning tunneling microscopy"', '"scanning tunneling microscopy"'),
        ('"STM" AND "AFM"', '"STM" AND "AFM"'),  # no escaped quote
        (r"STM AND \"AFM\"", r"STM AND \"AFM\""),  # no outer quotes
        (r'"\"STM\" \x"', r'"\"STM\" \x"'),  # not valid JSON
        ('"', '"'),
    ],
)
def test_zenodo_query_copied_from_json_is_decoded(given, expected):
    assert _form(zenodo_query=given).zenodo_query == expected


def test_zenodo_query_examples_are_in_the_schema_and_used_as_given():
    query = ZenodoHarvestWorkflowInput.model_json_schema()["properties"]["zenodo_query"]

    assert query["examples"] == ZENODO_QUERY_EXAMPLES
    for example in ZENODO_QUERY_EXAMPLES:
        assert _form(zenodo_query=example).zenodo_query == example


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ([], []),
        ([" 15147607 ", "", "15147606", "15147607"], ["15147607", "15147606"]),
    ],
)
def test_record_ids_are_trimmed(given, expected):
    assert _form(zenodo_record_ids=given).zenodo_record_ids == expected


@pytest.mark.parametrize("given", [["10.5281/zenodo.1"], ["abc"], ["1", "-2"]])
def test_record_ids_must_be_numbers(given):
    with pytest.raises(ValidationError, match="record IDs are numbers"):
        _form(zenodo_record_ids=given)


def test_workflow_id_is_trimmed():
    assert _form(galaxy_workflow_id=" abc ").galaxy_workflow_id == "abc"


def test_overrides_reach_the_activity_inputs():
    form = _form(galaxy_workflow_id="wf-form", raw_extensions=[".dat"])

    data = BuildWorkListInput(
        run_dir="r", user_id="u1", run_id="run-1", **form.config_overrides()
    )

    assert (data.galaxy_workflow_id, data.raw_extensions) == ("wf-form", [".dat"])
    # Temporal sends activity inputs as JSON.
    assert json.loads(data.model_dump_json())["raw_extensions"] == [".dat"]


# apply_overrides


def _config():
    return load_harvest_config(DEFAULT_HARVEST_CONFIG)


def _overrides(**values) -> BuildWorkListInput:
    form = _form(**values)
    return BuildWorkListInput(
        run_dir="r", user_id="u1", run_id="run-1", **form.config_overrides()
    )


def test_form_values_replace_the_config():
    data = _overrides(
        galaxy_workflow_id="wf-form", raw_extensions=[".dat"], zenodo_query="AFM"
    )

    config = apply_overrides(_config(), data)

    assert (config.galaxy_workflow_id, config.raw_extensions) == ("wf-form", [".dat"])
    assert config.zenodo_query == "AFM"
    assert config.licenses == _config().licenses  # the rest is unchanged


def test_form_lists_replace_the_config_lists():
    data = _overrides(
        raw_extensions=[".sm4"], context_extensions=[".pdf"], ignore_patterns=["*.mp4"]
    )

    config = apply_overrides(_config(), data)

    # Replaced, not extended: the config's other items are gone.
    assert config.raw_extensions == [".sm4"]
    assert config.context_extensions == [".pdf"]
    assert config.ignore_patterns == ["*.mp4"]


def test_form_urls_replace_the_config():
    data = _overrides(
        zenodo_url="https://sandbox.zenodo.org",
        galaxy_url="https://usegalaxy.org",
        galaxy_api_key="key",
    )

    config = apply_overrides(_config(), data)

    assert (config.zenodo_url, config.galaxy_url) == (
        "https://sandbox.zenodo.org",
        "https://usegalaxy.org",
    )


def test_form_can_narrow_the_licenses():
    config = apply_overrides(_config(), _overrides(licenses=["cc-zero"]))
    assert config.licenses == ["cc-zero"]


def test_form_cannot_add_a_license():
    with pytest.raises(
        HarvestConfigError, match=r"\['cc-by-nc-4.0'\] are not accepted"
    ):
        apply_overrides(_config(), _overrides(licenses=["cc-zero", "cc-by-nc-4.0"]))


@pytest.mark.parametrize(("given", "expected"), [(5.0, 5.0), (0.5, 2.0), (0, 2.0)])
def test_form_can_only_lengthen_the_delay(given, expected):
    assert _config().zenodo_request_delay == 2.0  # noqa: PLR2004
    data = _overrides(zenodo_request_delay=given)
    assert apply_overrides(_config(), data).zenodo_request_delay == expected


def test_backend_settings_keep_the_config():
    config = apply_overrides(_config(), _overrides(zenodo_query="AFM"))

    for name in ("max_archive_bytes", "max_uncompressed_bytes", "nomad_url"):
        assert getattr(config, name) == getattr(_config(), name)


def _installation_config():
    """An installation's own harvest config, different from the shipped one."""
    return _config().model_copy(
        update={
            "zenodo_url": "https://sandbox.zenodo.org",
            "galaxy_url": "https://usegalaxy.org",
            "galaxy_workflow_id": "wf-installation",
            "licenses": ["cc-zero"],
            "llm_model": "gemma-4-31b-llmlb-freiburg",
            "zenodo_request_delay": 3.0,
        }
    )


def test_unchanged_defaults_keep_the_installation_config():
    config = _installation_config()
    # The shipped licenses are not a subset of the installation's, so no error.
    assert apply_overrides(config, _overrides()) == config


def test_changed_values_replace_the_installation_config():
    data = _overrides(zenodo_query="AFM", llm_model="mistral-small-4-llmlb-freiburg")

    config = apply_overrides(_installation_config(), data)

    assert config.zenodo_query == "AFM"
    assert config.llm_model == "mistral-small-4-llmlb-freiburg"
    assert config.galaxy_url == "https://usegalaxy.org"  # unchanged default


def test_empty_form_values_keep_the_config():
    data = BuildWorkListInput(run_dir="r", user_id="u1", run_id="run-1")

    assert apply_overrides(_config(), data) == _config()
    assert apply_overrides(_config(), None) == _config()
