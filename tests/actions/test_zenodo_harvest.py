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
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import (
    HARVEST_SETTINGS,
    RUN_CONFIG_FILE,
    RUN_CONFIG_SCHEMA_VERSION,
    prepare_run,
    write_run_config,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
    PrepareRunInput,
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
    assert content["settings"] == {"zenodo_url": "https://zenodo.org"}


def test_write_run_config_empty_settings(tmp_path):
    path = write_run_config(tmp_path, {}, _prepare_input(max_records=1))

    content = json.loads(path.read_text())
    assert content["settings"] == {}
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


@pytest.fixture
def actions_fs(tmp_path, monkeypatch):
    """Point NOMAD's actions folder at a temporary directory."""
    from nomad.config import config  # noqa: PLC0415

    monkeypatch.setattr(config.fs, "actions", str(tmp_path))
    return tmp_path


@pytest.mark.asyncio
async def test_workflow_writes_run_config(actions_fs):
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
            activities=[prepare_run],
            activity_executor=ThreadPoolExecutor(max_workers=2),  # prepare_run is sync
        ):
            result = await env.client.execute_workflow(
                ZenodoHarvestWorkflow.run,
                ZenodoHarvestWorkflowInput(user_id="u1", max_records=max_records),
                id=workflow_id,
                task_queue=task_queue,
            )

    assert result["dry_run"] is True
    assert result["run_dir"].startswith(str(actions_fs / workflow_id))

    run_config = json.loads((Path(result["run_dir"]) / RUN_CONFIG_FILE).read_text())
    assert run_config["action_instance_id"] == workflow_id
    assert run_config["max_records"] == max_records
    assert set(run_config["settings"]) == set(HARVEST_SETTINGS)
