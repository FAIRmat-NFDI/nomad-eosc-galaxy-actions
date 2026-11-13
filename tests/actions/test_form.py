# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""The Action form (ZenodoHarvestWorkflowInput) and how its values reach a run."""

import json

import pytest
from pydantic import ValidationError

from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import (
    apply_overrides,
    recipients,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.harvest_config import (
    DEFAULT_HARVEST_CONFIG,
    load_harvest_config,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
    DEFAULT_RAW_EXTENSIONS,
    ZENODO_QUERY_EXAMPLES,
    BuildWorkListInput,
    ZenodoHarvestWorkflowInput,
)

MAX_DESCRIPTION = 120  # characters; the form shows each description under its field


def _form(**values) -> ZenodoHarvestWorkflowInput:
    return ZenodoHarvestWorkflowInput(user_id="u1", **values)


def test_defaults():
    form = _form()
    assert form.raw_extensions == DEFAULT_RAW_EXTENSIONS
    assert form.galaxy_workflow_id is None
    assert form.notify_email is None


def test_schema_shows_the_new_fields_and_short_descriptions():
    properties = ZenodoHarvestWorkflowInput.model_json_schema()["properties"]

    assert properties["raw_extensions"]["type"] == "array"
    assert properties["raw_extensions"]["items"] == {"type": "string"}
    assert {"galaxy_workflow_id", "notify_email"} <= set(properties)
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


@pytest.mark.parametrize("given", [[], ["", "."]])
def test_raw_extensions_need_one_extension(given):
    with pytest.raises(ValidationError, match="at least one raw file extension"):
        _form(raw_extensions=given)


def test_empty_text_fields_mean_not_set():
    form = _form(galaxy_workflow_id="  ", zenodo_query=" ", notify_email="")
    assert (form.galaxy_workflow_id, form.zenodo_query, form.notify_email) == (
        None,
        None,
        None,
    )


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


@pytest.mark.parametrize("address", ["no-at-sign", "a@b", "a b@c.org", "@c.org"])
def test_bad_email_is_refused(address):
    with pytest.raises(ValidationError):
        _form(notify_email=address)


def test_good_email_is_kept():
    assert _form(notify_email="name@example.org").notify_email == "name@example.org"


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


def test_form_values_replace_the_config():
    form = _form(
        galaxy_workflow_id="wf-form", raw_extensions=[".dat"], zenodo_query="AFM"
    )
    data = BuildWorkListInput(
        run_dir="r", user_id="u1", run_id="run-1", **form.config_overrides()
    )

    config = apply_overrides(_config(), data)

    assert (config.galaxy_workflow_id, config.raw_extensions) == ("wf-form", [".dat"])
    assert config.zenodo_query == "AFM"
    assert config.licenses == _config().licenses  # the rest is unchanged


def test_empty_form_values_keep_the_config():
    data = BuildWorkListInput(run_dir="r", user_id="u1", run_id="run-1")

    assert apply_overrides(_config(), data) == _config()
    assert apply_overrides(_config(), None) == _config()


# recipients


@pytest.mark.parametrize(
    ("user", "extra", "expected"),
    [
        ("u@x.org", None, "u@x.org"),
        ("u@x.org", "e@y.org", "u@x.org, e@y.org"),
        ("u@x.org", "u@x.org", "u@x.org"),  # no repeat
        (None, "e@y.org", "e@y.org"),  # the user has no address
        (None, None, ""),
    ],
)
def test_recipients(user, extra, expected):
    assert recipients(user, extra) == expected
