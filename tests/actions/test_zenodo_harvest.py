# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import ValidationError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

from nomad_eosc_galaxy_actions import zenodo_client
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import (
    CANDIDATES_FILE,
    CANDIDATES_SCHEMA_VERSION,
    RUN_CONFIG_FILE,
    RUN_CONFIG_SCHEMA_VERSION,
    SKIPPED_FILES_FILE,
    STATE_FILE,
    WORK_LIST_FILE,
    ZENODO_HARVEST_ACTION,
    build_work_list,
    prepare_run,
    search_zenodo,
    write_candidates,
    write_run_config,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.harvest_config import (
    DEFAULT_HARVEST_CONFIG,
    HarvestConfig,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
    BuildWorkListInput,
    PrepareRunInput,
    SearchZenodoInput,
    ZenodoHarvestWorkflowInput,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.workflows import (
    ZenodoHarvestWorkflow,
)


def _prepare_input(**overrides) -> PrepareRunInput:
    values = {
        "action_instance_id": "run-1",
        "started_at": "2026-10-15T12:00:00+00:00",
        "dry_run": True,
        "max_records": None,
    }
    return PrepareRunInput(**(values | overrides))


def test_write_run_config_creates_missing_dirs(tmp_path):
    run_dir = tmp_path / "a" / "b"
    path = write_run_config(
        run_dir, {"zenodo_url": "https://zenodo.org"}, _prepare_input()
    )

    assert path == run_dir / RUN_CONFIG_FILE
    content = json.loads(path.read_text())
    assert content["schema_version"] == RUN_CONFIG_SCHEMA_VERSION
    assert content["action_instance_id"] == "run-1"
    assert content["max_records"] is None
    assert content["harvest_config"] == {"zenodo_url": "https://zenodo.org"}


def test_write_run_config_empty_settings(tmp_path):
    path = write_run_config(tmp_path, {}, _prepare_input(max_records=1))

    content = json.loads(path.read_text())
    assert content["harvest_config"] == {}
    assert content["max_records"] == 1


def test_write_run_config_overwrites_previous_run(tmp_path):
    write_run_config(tmp_path, {}, _prepare_input(dry_run=True))
    path = write_run_config(tmp_path, {}, _prepare_input(dry_run=False))

    assert json.loads(path.read_text())["dry_run"] is False


@pytest.mark.parametrize("max_records", [0, -1])
def test_workflow_input_rejects_non_positive_max_records(max_records):
    with pytest.raises(ValidationError):
        ZenodoHarvestWorkflowInput(user_id="u1", max_records=max_records)


def test_workflow_input_defaults_to_dry_run():
    assert ZenodoHarvestWorkflowInput(user_id="u1").dry_run is True


def test_write_candidates(tmp_path):
    record = zenodo_client.ZenodoRecord(record_id="1", concept_record_id="0")
    rejection = zenodo_client.Rejection(record_id="2", reason="duplicate")

    path = write_candidates(tmp_path, "STM", [record], [rejection])

    content = json.loads(path.read_text())
    assert path == tmp_path / CANDIDATES_FILE
    assert content["schema_version"] == CANDIDATES_SCHEMA_VERSION
    assert content["query"] == "STM"
    assert content["candidates"][0]["concept_doi"] is None
    assert content["rejected"] == [{"record_id": "2", "reason": "duplicate"}]


def test_write_candidates_empty(tmp_path):
    content = json.loads(write_candidates(tmp_path, "", [], []).read_text())
    assert content["candidates"] == content["rejected"] == []


@pytest.fixture
def actions_fs(tmp_path, monkeypatch):
    """Point NOMAD's actions folder at a temporary directory."""
    from nomad.config import config  # noqa: PLC0415

    monkeypatch.setattr(config.fs, "actions", str(tmp_path))
    return tmp_path


@pytest.fixture
def harvest_config_file(tmp_path, fake_zenodo, monkeypatch):
    """A harvest config file that sends Zenodo requests to the local fake server."""
    from nomad.config import config  # noqa: PLC0415

    content = json.loads(DEFAULT_HARVEST_CONFIG.read_text())
    content |= {"zenodo_url": fake_zenodo.url, "zenodo_request_delay": 0}
    path = tmp_path / "harvest_config.json"
    path.write_text(json.dumps(content))

    config.load_plugins()  # a NOMAD worker does this at start-up
    entry_point = config.get_plugin_entry_point(ZENODO_HARVEST_ACTION)
    monkeypatch.setattr(entry_point, "harvest_config_path", str(path))
    monkeypatch.delenv("ZENODO_TOKEN", raising=False)
    return path


def test_search_zenodo_activity(tmp_path, fake_zenodo, harvest_config_file):
    result = ActivityEnvironment().run(
        search_zenodo, SearchZenodoInput(run_dir=str(tmp_path))
    )

    assert (result.candidates, result.rejected) == (6, 0)
    assert len(fake_zenodo.requests) == len(fake_zenodo.pages)  # all pages read
    content = json.loads(Path(result.candidates_path).read_text())
    assert len(content["candidates"]) == result.candidates


@pytest.mark.asyncio
async def test_workflow_runs_all_steps(actions_fs, harvest_config_file):
    max_records = 2
    task_queue = f"test-{uuid.uuid4()}"
    workflow_id = f"zenodo-harvest-{uuid.uuid4()}"

    # Time-skipping test server: a local Temporal test server (downloaded on
    # first use) that fast-forwards timers.
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[ZenodoHarvestWorkflow],
            activities=[prepare_run, search_zenodo, build_work_list],
            activity_executor=ThreadPoolExecutor(max_workers=2),  # sync activities
        ):
            result = await env.client.execute_workflow(
                ZenodoHarvestWorkflow.run,
                ZenodoHarvestWorkflowInput(user_id="u1", max_records=max_records),
                id=workflow_id,
                task_queue=task_queue,
            )

    assert result["dry_run"] is True
    assert result["run_dir"].startswith(str(actions_fs / workflow_id))
    assert (result["candidates"], result["rejected"]) == (6, 0)  # full search

    run_dir = Path(result["run_dir"])
    run_config = json.loads((run_dir / RUN_CONFIG_FILE).read_text())
    assert run_config["action_instance_id"] == workflow_id
    assert run_config["max_records"] == max_records
    used = HarvestConfig.model_validate(run_config["harvest_config"])
    assert used.zenodo_url == json.loads(harvest_config_file.read_text())["zenodo_url"]
    assert (run_dir / CANDIDATES_FILE).is_file()
    # First two hits: 10843653 (zip + README) and 10443995 (small zip + two
    # context files; its 9.3 GB zip is skipped as too large).
    assert result["work_list_records"] == 2  # noqa: PLR2004
    assert result["work_list_files"] == 5  # noqa: PLR2004
    assert result["skipped_files"] == 1
    assert json.loads((run_dir / WORK_LIST_FILE).read_text())["run_id"] == workflow_id


def _search(run_dir) -> None:
    ActivityEnvironment().run(search_zenodo, SearchZenodoInput(run_dir=str(run_dir)))


def test_build_work_list_activity(tmp_path, actions_fs, harvest_config_file):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _search(run_dir)

    result = ActivityEnvironment().run(
        build_work_list, BuildWorkListInput(run_dir=str(run_dir), run_id="run-1")
    )

    assert (result.records, result.skipped) == (6, 1)
    work_list = json.loads(Path(result.work_list_path).read_text())
    assert work_list["run_id"] == "run-1"
    assert sum(len(r["download"]) for r in work_list["records"]) == result.files
    skipped = json.loads((run_dir / SKIPPED_FILES_FILE).read_text())
    assert skipped["skipped"][0]["reason"] == "too_large"
    assert (actions_fs / "artifacts" / STATE_FILE).is_file()  # created on first use


def test_build_work_list_activity_skips_resolved_records(
    tmp_path, actions_fs, harvest_config_file
):
    from nomad_eosc_galaxy_actions.state_store import StateStore  # noqa: PLC0415

    with (
        StateStore(actions_fs / "artifacts" / STATE_FILE) as store,
        store.transaction(),
    ):
        # Record 17312296 has a single archive, Data_v2.zip.
        store.set_archive_resolved("15920712", "6a2df9509c1a034a4aefae8890da6df0", True)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _search(run_dir)

    result = ActivityEnvironment().run(
        build_work_list, BuildWorkListInput(run_dir=str(run_dir), run_id="run-2")
    )

    work_list = json.loads(Path(result.work_list_path).read_text())
    assert "17312296" not in [r["record_id"] for r in work_list["records"]]
    assert (result.records, result.skipped) == (5, 2)
