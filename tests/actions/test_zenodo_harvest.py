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
from temporalio.client import WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import Worker

from nomad_eosc_galaxy_actions import zenodo_client
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.activities import (
    CANDIDATES_FILE,
    CANDIDATES_SCHEMA_VERSION,
    MANIFEST_FILE,
    RESULTS_FILE,
    RUN_CONFIG_FILE,
    RUN_CONFIG_SCHEMA_VERSION,
    SKIPPED_FILES_FILE,
    STATE_FILE,
    WORK_LIST_FILE,
    ZENODO_HARVEST_ACTION,
    build_work_list,
    fetch_manifest,
    merge_state,
    poll_galaxy,
    prepare_run,
    run_galaxy,
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
    FetchManifestInput,
    MergeStateInput,
    PollGalaxyInput,
    PrepareRunInput,
    RunGalaxyInput,
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


@pytest.fixture
def galaxy_config(harvest_config_file, fake_galaxy, monkeypatch):
    """Extend the harvest config so Galaxy requests go to the local fake server."""
    content = json.loads(harvest_config_file.read_text())
    content |= {"galaxy_url": fake_galaxy.url, "galaxy_workflow_id": "wf-1"}
    harvest_config_file.write_text(json.dumps(content))
    monkeypatch.setenv("GALAXY_API_KEY", "test-key")
    return fake_galaxy


ALL_ACTIVITIES = [
    prepare_run,
    search_zenodo,
    build_work_list,
    run_galaxy,
    poll_galaxy,
    fetch_manifest,
    merge_state,
]


async def _run_workflow(
    workflow_id: str, max_records: int | None = 2, dry_run: bool = True
) -> dict:
    # Time-skipping test server: a local Temporal test server (downloaded on
    # first use) that fast-forwards timers such as the Galaxy polling sleep.
    task_queue = f"test-{uuid.uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[ZenodoHarvestWorkflow],
            activities=ALL_ACTIVITIES,
            activity_executor=ThreadPoolExecutor(max_workers=2),  # sync activities
        ):
            return await env.client.execute_workflow(
                ZenodoHarvestWorkflow.run,
                ZenodoHarvestWorkflowInput(
                    user_id="u1", max_records=max_records, dry_run=dry_run
                ),
                id=workflow_id,
                task_queue=task_queue,
            )


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
    workflow_id = f"zenodo-harvest-{uuid.uuid4()}"
    result = await _run_workflow(workflow_id, max_records)

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
    # The shipped config has no Galaxy workflow ID.
    assert result["galaxy"] == {"status": "skipped", "reason": "galaxy_not_configured"}
    assert result["merge"] == {"status": "skipped", "reason": "no_galaxy_run"}


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


# Galaxy round trip (against the local fake Galaxy server)


def _work_list(run_dir: Path, run_id: str = "run-1") -> Path:
    path = run_dir / WORK_LIST_FILE
    path.write_text(json.dumps({"schema_version": 1, "run_id": run_id, "records": []}))
    return path


def test_run_galaxy_activity(tmp_path, galaxy_config):
    path = _work_list(tmp_path)

    result = ActivityEnvironment().run(
        run_galaxy, RunGalaxyInput(work_list_path=str(path), run_id="run-1")
    )

    assert (result.history_id, result.invocation_id) == ("hist-1", "inv-1")
    assert galaxy_config.uploaded == path.read_bytes()
    assert galaxy_config.history_payload["name"] == "zenodo-harvest-run-1"
    payload = galaxy_config.invocation_payload
    assert payload["inputs"] == {"0": {"src": "hda", "id": "in-1"}}
    assert ("POST", "/api/workflows/wf-1/invocations") in galaxy_config.requests


def test_galaxy_needs_api_key(tmp_path, galaxy_config, monkeypatch):
    monkeypatch.delenv("GALAXY_API_KEY")
    path = _work_list(tmp_path)

    with pytest.raises(ValueError, match="GALAXY_API_KEY"):
        ActivityEnvironment().run(
            run_galaxy, RunGalaxyInput(work_list_path=str(path), run_id="run-1")
        )


@pytest.mark.parametrize(
    "case",
    [
        (["running"], False, "running", None),
        (["ok"], True, "ok", "out-1"),
        (["error"], True, "error", None),
    ],
)
def test_poll_galaxy_activity(galaxy_config, case):
    job_states, done, state, manifest_id = case
    galaxy_config.job_states = job_states

    result = ActivityEnvironment().run(
        poll_galaxy, PollGalaxyInput(invocation_id="inv-1")
    )

    assert (result.done, result.state) == (done, state)
    assert (result.outputs or {}).get("manifest") == manifest_id


def _fetch(run_dir: Path, results_dataset_id: str | None = None):
    return ActivityEnvironment().run(
        fetch_manifest,
        FetchManifestInput(
            run_dir=str(run_dir),
            work_list_path=str(run_dir / WORK_LIST_FILE),
            manifest_dataset_id="out-1",
            results_dataset_id=results_dataset_id,
        ),
    )


def test_fetch_manifest_detects_placeholder(tmp_path, galaxy_config):
    galaxy_config.uploaded = _work_list(tmp_path).read_bytes()  # echoed back

    result = _fetch(tmp_path)

    assert result.placeholder is True
    assert Path(result.manifest_path) == tmp_path / MANIFEST_FILE
    assert result.results_path is None


def test_fetch_manifest_real_output(tmp_path, galaxy_config):
    _work_list(tmp_path)
    galaxy_config.output = json.dumps({"run_id": "run-1", "files": []}).encode()
    galaxy_config.results = b'{"run_id": "run-1"}'

    result = _fetch(tmp_path, results_dataset_id="out-2")

    assert result.placeholder is False
    assert json.loads(Path(result.manifest_path).read_text())["files"] == []
    assert Path(result.results_path) == tmp_path / RESULTS_FILE
    assert Path(result.results_path).read_bytes() == galaxy_config.results


def test_fetch_manifest_rejects_other_run(tmp_path, galaxy_config):
    _work_list(tmp_path)
    galaxy_config.output = json.dumps({"run_id": "another-run"}).encode()

    with pytest.raises(ApplicationError, match="another-run") as error:
        _fetch(tmp_path, results_dataset_id="out-2")
    assert error.value.non_retryable
    assert ("GET", "/api/datasets/out-2/display") not in galaxy_config.requests


def _galaxy_outputs(run_id: str) -> tuple[bytes, bytes]:
    """A manifest with one converted raw file, and its results."""
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "tool_version": "0.1",
        "archives": [
            {
                "concept_record_id": "c1",
                "key": "d.zip",
                "md5": "zip-md5",
                "status": "extracted",
            }
        ],
        "files": [
            {
                "concept_record_id": "c1",
                "record_id": "r1",
                "source": "archive",
                "archive_key": "d.zip",
                "path": "d/a.sxm",
                "size": 1,
                "sha256": "sha-a",
                "role": "raw",
                "action": "process",
            }
        ],
    }
    results = {
        "schema_version": 1,
        "run_id": run_id,
        "files": [{"sha256": "sha-a", "status": "success"}],
    }
    return json.dumps(manifest).encode(), json.dumps(results).encode()


def test_merge_state_activity(tmp_path, actions_fs):
    from nomad_eosc_galaxy_actions.state_store import StateStore  # noqa: PLC0415

    manifest, results = _galaxy_outputs("run-1")
    (tmp_path / MANIFEST_FILE).write_bytes(manifest)
    (tmp_path / RESULTS_FILE).write_bytes(results)

    summary = ActivityEnvironment().run(
        merge_state,
        MergeStateInput(
            manifest_path=str(tmp_path / MANIFEST_FILE),
            results_path=str(tmp_path / RESULTS_FILE),
        ),
    )

    assert (summary.success, summary.archives_resolved) == (1, 1)
    with StateStore(actions_fs / "artifacts" / STATE_FILE) as store:
        assert store.known_sha256("c1") == ["sha-a"]
        assert store.is_archive_resolved("c1", "zip-md5")


@pytest.mark.parametrize(
    "results",
    [
        b'{"schema_version": 2, "run_id": "run-1"}',  # unknown layout
        b'{"schema_version": 1, "run_id": "run-2"}',  # another run
    ],
)
def test_merge_state_rejects_bad_outputs(tmp_path, actions_fs, results):
    (tmp_path / MANIFEST_FILE).write_bytes(_galaxy_outputs("run-1")[0])
    (tmp_path / RESULTS_FILE).write_bytes(results)

    with pytest.raises(ApplicationError, match="Cannot merge") as error:
        ActivityEnvironment().run(
            merge_state,
            MergeStateInput(
                manifest_path=str(tmp_path / MANIFEST_FILE),
                results_path=str(tmp_path / RESULTS_FILE),
            ),
        )
    assert error.value.non_retryable


@pytest.mark.asyncio
async def test_workflow_galaxy_round_trip(actions_fs, galaxy_config):
    galaxy_config.job_states = ["running", "running", "ok"]  # needs two sleeps
    workflow_id = f"zenodo-harvest-{uuid.uuid4()}"

    result = await _run_workflow(workflow_id)

    galaxy = result["galaxy"]
    assert galaxy["status"] == "ok" and galaxy["placeholder"] is True
    assert galaxy["history_id"] == "hist-1"
    run_dir = Path(result["run_dir"])
    manifest = json.loads(Path(galaxy["manifest_path"]).read_text())
    assert manifest == json.loads((run_dir / WORK_LIST_FILE).read_text())
    polls = [r for r in galaxy_config.requests if r[1] == "/api/jobs/job-1"]
    assert len(polls) == 3  # noqa: PLR2004
    assert result["merge"] == {"status": "skipped", "reason": "placeholder"}


def _real_galaxy(galaxy, workflow_id: str) -> None:
    """Make the fake Galaxy return a real manifest and results for this run."""
    galaxy.output, galaxy.results = _galaxy_outputs(workflow_id)
    galaxy.invocation_outputs |= {"results": {"id": "out-2", "src": "hda"}}


@pytest.mark.asyncio
async def test_workflow_merges_galaxy_outputs(actions_fs, galaxy_config):
    from nomad_eosc_galaxy_actions.state_store import StateStore  # noqa: PLC0415

    workflow_id = f"zenodo-harvest-{uuid.uuid4()}"
    _real_galaxy(galaxy_config, workflow_id)

    result = await _run_workflow(workflow_id, dry_run=False)

    assert result["galaxy"]["placeholder"] is False
    assert result["merge"] == {
        "status": "ok",
        "success": 1,
        "failed": 0,
        "archives_resolved": 1,
        "archives_unresolved": 0,
        "retried": 0,
    }
    with StateStore(actions_fs / "artifacts" / STATE_FILE) as store:
        assert store.known_sha256("c1") == ["sha-a"]


@pytest.mark.asyncio
async def test_workflow_dry_run_leaves_state_unchanged(actions_fs, galaxy_config):
    from nomad_eosc_galaxy_actions.state_store import StateStore  # noqa: PLC0415

    workflow_id = f"zenodo-harvest-{uuid.uuid4()}"
    _real_galaxy(galaxy_config, workflow_id)

    result = await _run_workflow(workflow_id, dry_run=True)

    assert result["merge"] == {"status": "skipped", "reason": "dry_run"}
    with StateStore(actions_fs / "artifacts" / STATE_FILE) as store:
        assert store.known_sha256("c1") == []


@pytest.mark.asyncio
async def test_workflow_fails_without_results_output(actions_fs, galaxy_config):
    workflow_id = f"zenodo-harvest-{uuid.uuid4()}"
    galaxy_config.output = _galaxy_outputs(workflow_id)[0]  # no 'results' output

    with pytest.raises(WorkflowFailureError) as error:
        await _run_workflow(workflow_id, dry_run=False)
    assert "no output named 'results'" in str(error.value.cause)


@pytest.mark.asyncio
async def test_workflow_fails_when_galaxy_fails(actions_fs, galaxy_config):
    galaxy_config.job_states = ["error"]

    with pytest.raises(WorkflowFailureError) as error:
        await _run_workflow(f"zenodo-harvest-{uuid.uuid4()}")
    assert "ended in state 'error'" in str(error.value.cause)
    assert ("GET", "/api/datasets/out-1/display") not in galaxy_config.requests


@pytest.mark.asyncio
async def test_workflow_fails_without_manifest_output(actions_fs, galaxy_config):
    galaxy_config.invocation_outputs = {}  # workflow lacks the 'manifest' label

    with pytest.raises(WorkflowFailureError) as error:
        await _run_workflow(f"zenodo-harvest-{uuid.uuid4()}")
    assert "no output named 'manifest'" in str(error.value.cause)


@pytest.mark.asyncio
async def test_workflow_skips_galaxy_without_work(
    actions_fs, galaxy_config, fake_zenodo
):
    fake_zenodo.pages = {1: {"hits": {"hits": []}, "links": {}}}

    result = await _run_workflow(f"zenodo-harvest-{uuid.uuid4()}")

    assert result["galaxy"] == {"status": "skipped", "reason": "no_work"}
    assert galaxy_config.requests == []
