# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import io
import json
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
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
    PROJECTS_DIR,
    REPORT_FILE,
    REPORT_MARKDOWN_FILE,
    RESULTS_FILE,
    RUN_CONFIG_FILE,
    RUN_CONFIG_SCHEMA_VERSION,
    SKIPPED_FILES_FILE,
    SKIPPED_FILES_SCHEMA_VERSION,
    STATE_FILE,
    WORK_LIST_FILE,
    ZENODO_HARVEST_ACTION,
    build_work_list,
    create_projects,
    email_report,
    fetch_manifest,
    fetch_projects,
    merge_state,
    poll_galaxy,
    prepare_run,
    run_galaxy,
    search_zenodo,
    write_candidates,
    write_report,
    write_run_config,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.harvest_config import (
    DEFAULT_HARVEST_CONFIG,
    HarvestConfig,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
    BuildWorkListInput,
    CreateProjectsInput,
    EmailReportInput,
    FetchManifestInput,
    FetchProjectsInput,
    MergeStateInput,
    PollGalaxyInput,
    PrepareRunInput,
    RunGalaxyInput,
    SearchZenodoInput,
    WriteReportInput,
    ZenodoHarvestWorkflowInput,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.workflows import (
    ZenodoHarvestWorkflow,
)
from nomad_eosc_galaxy_actions.report import REPORT_SCHEMA_VERSION
from nomad_eosc_galaxy_actions.work_list import WORK_LIST_SCHEMA_VERSION


def _prepare_input(**overrides) -> PrepareRunInput:
    values = {
        "action_instance_id": "run-1",
        "run_id": "run-1",
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


@pytest.mark.parametrize("field", ["max_records", "repeat_every_days", "max_runs"])
@pytest.mark.parametrize("value", [0, -1])
def test_workflow_input_rejects_non_positive_numbers(field, value):
    with pytest.raises(ValidationError):
        ZenodoHarvestWorkflowInput(user_id="u1", **{field: value})


def test_workflow_input_defaults_to_dry_run():
    assert ZenodoHarvestWorkflowInput(user_id="u1").dry_run is True


def test_run_number_is_not_in_the_form():
    schema = ZenodoHarvestWorkflowInput.model_json_schema()

    assert "run_number" not in schema["properties"]
    assert {"repeat_every_days", "max_runs"} <= schema["properties"].keys()


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
    for name in ("HOST", "PORT", "FROM", "USER", "PASSWORD"):
        monkeypatch.delenv(f"HARVEST_SMTP_{name}", raising=False)
    return path


@pytest.fixture
def galaxy_config(harvest_config_file, fake_galaxy, monkeypatch):
    """Extend the harvest config so Galaxy requests go to the local fake server."""
    content = json.loads(harvest_config_file.read_text())
    content |= {"galaxy_url": fake_galaxy.url, "galaxy_workflow_id": "wf-1"}
    harvest_config_file.write_text(json.dumps(content))
    monkeypatch.setenv("GALAXY_API_KEY", "test-key")
    return fake_galaxy


@pytest.fixture
def nomad_config(harvest_config_file, galaxy_config, fake_nomad, monkeypatch):
    """Extend the harvest config so projects are created in the local fake NOMAD."""
    content = json.loads(harvest_config_file.read_text())
    content |= {"nomad_url": fake_nomad.url}
    harvest_config_file.write_text(json.dumps(content))
    monkeypatch.setenv("NOMAD_PAT", "test-pat")
    return fake_nomad


ALL_ACTIVITIES = [
    prepare_run,
    search_zenodo,
    build_work_list,
    run_galaxy,
    poll_galaxy,
    fetch_manifest,
    fetch_projects,
    create_projects,
    merge_state,
    write_report,
    email_report,
]


async def _run_workflow(
    workflow_id: str, max_records: int | None = 2, dry_run: bool = True, **inputs
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
                    user_id="u1", max_records=max_records, dry_run=dry_run, **inputs
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
    assert run_config["action_instance_id"] == run_config["run_id"] == workflow_id
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
    assert (
        result["nomad"]
        == result["merge"]
        == {
            "status": "skipped",
            "reason": "no_galaxy_run",
        }
    )
    report = json.loads(Path(result["report_path"]).read_text())
    assert report["run_id"] == workflow_id
    assert [r["record_id"] for r in report["records"]] == ["10843653", "10443995"]
    assert "too_large" in (run_dir / REPORT_MARKDOWN_FILE).read_text()
    assert result["email"] == {"status": "skipped", "reason": "smtp_not_configured"}


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


def test_fetch_manifest_real_output(tmp_path, galaxy_config, caplog):
    _work_list(tmp_path)
    galaxy_config.output = json.dumps({"run_id": "run-1", "files": []}).encode()
    galaxy_config.results = b'{"run_id": "run-1"}'

    with caplog.at_level("INFO"):
        result = _fetch(tmp_path, results_dataset_id="out-2")

    assert result.placeholder is False
    assert "Galaxy manifest for run run-1: 0 files (none)" in caplog.text
    assert "results: yes" in caplog.text
    assert json.loads(Path(result.manifest_path).read_text())["files"] == []
    assert Path(result.results_path) == tmp_path / RESULTS_FILE
    assert Path(result.results_path).read_bytes() == galaxy_config.results


def test_fetch_manifest_logs_a_summary(tmp_path, galaxy_config, caplog):
    _work_list(tmp_path)
    files = [{"action": a} for a in ("process", "process", "skipped", "already_known")]
    manifest = {"run_id": "run-1", "tool_version": "0.1", "files": files}
    galaxy_config.output = json.dumps(manifest).encode()

    with caplog.at_level("INFO"):
        _fetch(tmp_path)

    assert (
        "Galaxy manifest for run run-1: 4 files (1 already_known, 2 process, "
        "1 skipped), tool_version 0.1; results: no"
    ) in caplog.text


def test_fetch_manifest_rejects_other_run(tmp_path, galaxy_config):
    _work_list(tmp_path)
    galaxy_config.output = json.dumps({"run_id": "another-run"}).encode()

    with pytest.raises(ApplicationError, match="another-run") as error:
        _fetch(tmp_path, results_dataset_id="out-2")
    assert error.value.non_retryable
    assert ("GET", "/api/datasets/out-2/display") not in galaxy_config.requests


# First record of the recorded Zenodo search (on every work list).
CID, RECORD = "10843652", "10843653"


def _galaxy_outputs(run_id: str) -> tuple[bytes, bytes]:
    """A manifest with one converted raw file, and its results."""
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "tool_version": "0.1",
        "archives": [
            {
                "concept_record_id": CID,
                "record_id": RECORD,
                "key": "d.zip",
                "md5": "zip-md5",
                "status": "extracted",
            }
        ],
        "files": [
            {
                "concept_record_id": CID,
                "record_id": RECORD,
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


def _project_zip() -> bytes:
    """A project zip as Galaxy builds it: one folder per raw file."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("a/a.nxs", b"nexus")
        archive.writestr("a/a.sxm", b"raw")
        archive.writestr("a/eln_data.yaml", b"eln")
    return buffer.getvalue()


def test_fetch_projects_activity(tmp_path, galaxy_config):
    galaxy_config.collections = {"coll-1": {"101": b"zip-1", "102": b"zip-2"}}

    result = ActivityEnvironment().run(
        fetch_projects,
        FetchProjectsInput(run_dir=str(tmp_path), collection_id="coll-1"),
    )

    folder = tmp_path / PROJECTS_DIR
    assert result.zip_paths == {
        "101": str(folder / "101.zip"),
        "102": str(folder / "102.zip"),
    }
    assert (folder / "102.zip").read_bytes() == b"zip-2"


def test_fetch_projects_rejects_bad_identifier(tmp_path, galaxy_config):
    galaxy_config.collections = {"coll-1": {"../101": b"zip"}}

    with pytest.raises(ApplicationError, match="not a Zenodo record ID") as error:
        ActivityEnvironment().run(
            fetch_projects,
            FetchProjectsInput(run_dir=str(tmp_path), collection_id="coll-1"),
        )
    assert error.value.non_retryable


def _project_inputs(tmp_path, *record_ids: str) -> CreateProjectsInput:
    """A work list with these records (101 without DOI) and a zip for each."""
    records = [
        {
            "record_id": record_id,
            "concept_record_id": "100",
            "version_doi": None
            if record_id == "101"
            else f"10.5281/zenodo.{record_id}",
        }
        for record_id in record_ids
    ]
    work_list = tmp_path / WORK_LIST_FILE
    work_list.write_text(json.dumps({"run_id": "run-1", "records": records}))
    zip_paths = {}
    for record_id in record_ids:
        path = tmp_path / f"{record_id}.zip"
        path.write_bytes(_project_zip())
        zip_paths[record_id] = str(path)
    return CreateProjectsInput(
        work_list_path=str(work_list), user_id="u1", zip_paths=zip_paths
    )


def test_create_projects_activity(tmp_path, nomad_config, fake_zenodo):
    nomad_config.projects = {  # left from an earlier, interrupted attempt
        "old": {"upload_name": "zgn-demo-100-102", "published": False}
    }

    result = ActivityEnvironment().run(
        create_projects, _project_inputs(tmp_path, "101", "102")
    )

    assert result.upload_ids == {"101": "up-2", "102": "old"}
    assert (result.created, result.left_out) == (["101"], {})
    with zipfile.ZipFile(io.BytesIO(nomad_config.projects["up-2"]["content"])) as z:
        metadata = json.loads(z.read("nomad.json"))
    # No DOI: the record page on the configured Zenodo (the fake server here).
    assert metadata["references"] == [f"{fake_zenodo.url}/records/101"]
    assert set(nomad_config.auth_headers) == {"Bearer test-pat"}


def test_create_projects_leaves_out_records_over_the_limit(tmp_path, nomad_config):
    nomad_config.projects = {
        f"u{i}": {"upload_name": f"zgn-demo-1-{i}", "published": False}
        for i in range(9)
    }

    result = ActivityEnvironment().run(
        create_projects, _project_inputs(tmp_path, "101", "102", "103")
    )

    assert result.created == ["101"]  # the 10th demo project
    assert result.left_out == {"102": "project_limit", "103": "project_limit"}
    posts = [r for r in nomad_config.requests if r[0] == "POST"]
    assert len(posts) == 1


def test_create_projects_needs_pat(tmp_path, nomad_config, monkeypatch):
    monkeypatch.delenv("NOMAD_PAT")

    with pytest.raises(ApplicationError, match="NOMAD_PAT") as error:
        ActivityEnvironment().run(create_projects, _project_inputs(tmp_path, "101"))
    assert error.value.non_retryable
    assert nomad_config.requests == []


def test_create_projects_rejects_unknown_record(tmp_path, nomad_config):
    data = _project_inputs(tmp_path, "101")
    data.zip_paths["999"] = data.zip_paths["101"]

    with pytest.raises(ApplicationError, match="not in the work list") as error:
        ActivityEnvironment().run(create_projects, data)
    assert error.value.non_retryable


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
        assert store.known_sha256(CID) == ["sha-a"]
        assert store.is_archive_resolved(CID, "zip-md5")


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
    assert result["nomad"] == {"status": "skipped", "reason": "placeholder"}
    assert result["merge"] == {"status": "skipped", "reason": "placeholder"}


def _real_galaxy(galaxy, workflow_id: str) -> None:
    """Make the fake Galaxy return real outputs for this run."""
    galaxy.output, galaxy.results = _galaxy_outputs(workflow_id)
    galaxy.invocation_outputs |= {"results": {"id": "out-2", "src": "hda"}}
    galaxy.collections = {"coll-1": {RECORD: _project_zip()}}
    galaxy.invocation_collections = {"projects": {"id": "coll-1", "src": "hdca"}}


@pytest.mark.asyncio
async def test_workflow_creates_projects_and_merges(
    actions_fs, nomad_config, fake_galaxy
):
    from nomad_eosc_galaxy_actions.state_store import StateStore  # noqa: PLC0415

    workflow_id = f"zenodo-harvest-{uuid.uuid4()}"
    _real_galaxy(fake_galaxy, workflow_id)

    result = await _run_workflow(workflow_id, dry_run=False)

    assert result["galaxy"]["placeholder"] is False
    assert result["nomad"] == {
        "status": "ok",
        "upload_ids": {RECORD: "up-1"},
        "created": [RECORD],
        "left_out": {},
    }
    assert result["merge"] == {
        "status": "ok",
        "success": 1,
        "failed": 0,
        "archives_resolved": 1,
        "archives_unresolved": 0,
        "retried": 0,
        "records_left_out": 0,
    }
    project = nomad_config.projects["up-1"]
    assert project["upload_name"] == f"zgn-demo-{CID}-{RECORD}"
    with zipfile.ZipFile(io.BytesIO(project["content"])) as archive:
        assert "a/a.nxs" in archive.namelist()
        assert json.loads(archive.read("nomad.json"))["references"] == [
            "https://doi.org/10.5061/dryad.1g1jwsv49"
        ]
    with StateStore(actions_fs / "artifacts" / STATE_FILE) as store:
        row = store._db.execute("SELECT upload_id FROM files").fetchone()
    assert row == ("up-1",)
    report = json.loads(Path(result["report_path"]).read_text())
    assert report["records"][0]["upload_id"] == "up-1"
    assert report["failed_files"] == []


@pytest.mark.asyncio
async def test_every_written_file_has_its_schema_version(
    actions_fs, nomad_config, fake_galaxy
):
    from nomad_eosc_galaxy_actions import state_store  # noqa: PLC0415

    workflow_id = f"zenodo-harvest-{uuid.uuid4()}"
    _real_galaxy(fake_galaxy, workflow_id)

    result = await _run_workflow(workflow_id, dry_run=False)

    run_dir = Path(result["run_dir"])
    versions = {
        RUN_CONFIG_FILE: RUN_CONFIG_SCHEMA_VERSION,
        CANDIDATES_FILE: CANDIDATES_SCHEMA_VERSION,
        WORK_LIST_FILE: WORK_LIST_SCHEMA_VERSION,
        SKIPPED_FILES_FILE: SKIPPED_FILES_SCHEMA_VERSION,
        REPORT_FILE: REPORT_SCHEMA_VERSION,
    }
    for name, version in versions.items():
        content = json.loads((run_dir / name).read_text())
        assert content["schema_version"] == version, name
    with state_store.StateStore(actions_fs / "artifacts" / STATE_FILE) as store:
        assert store.schema_version == state_store.STATE_SCHEMA_VERSION


@pytest.mark.asyncio
async def test_workflow_dry_run_changes_nothing(actions_fs, nomad_config, fake_galaxy):
    from nomad_eosc_galaxy_actions.state_store import StateStore  # noqa: PLC0415

    workflow_id = f"zenodo-harvest-{uuid.uuid4()}"
    _real_galaxy(fake_galaxy, workflow_id)

    result = await _run_workflow(workflow_id, dry_run=True)

    assert result["nomad"] == {"status": "skipped", "reason": "dry_run"}
    assert result["merge"] == {"status": "skipped", "reason": "dry_run"}
    assert nomad_config.requests == []
    with StateStore(actions_fs / "artifacts" / STATE_FILE) as store:
        assert store.known_sha256(CID) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["results", "projects"])
async def test_workflow_fails_without_output(
    actions_fs, nomad_config, fake_galaxy, missing
):
    workflow_id = f"zenodo-harvest-{uuid.uuid4()}"
    _real_galaxy(fake_galaxy, workflow_id)
    fake_galaxy.invocation_outputs.pop(missing, None)
    fake_galaxy.invocation_collections.pop(missing, None)

    with pytest.raises(WorkflowFailureError) as error:
        await _run_workflow(workflow_id, dry_run=False)
    assert f"no output named {missing!r}" in str(error.value.cause)
    assert nomad_config.requests == []  # nothing written to NOMAD
    [report_path] = actions_fs.rglob(REPORT_FILE)
    assert json.loads(report_path.read_text())["failure"]["step"] == "galaxy"


@pytest.mark.asyncio
async def test_workflow_fails_when_galaxy_fails(actions_fs, galaxy_config):
    galaxy_config.job_states = ["error"]

    with pytest.raises(WorkflowFailureError) as error:
        await _run_workflow(f"zenodo-harvest-{uuid.uuid4()}")
    assert "ended in state 'error'" in str(error.value.cause)
    assert ("GET", "/api/datasets/out-1/display") not in galaxy_config.requests
    # The failed run still writes its report.
    [report_path] = actions_fs.rglob(REPORT_FILE)
    failure = json.loads(report_path.read_text())["failure"]
    assert failure["step"] == "galaxy"
    assert "ended in state 'error'" in failure["error"]
    assert (
        "**Run failed** in step `galaxy`"
        in (report_path.parent / REPORT_MARKDOWN_FILE).read_text()
    )


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


# Repeated runs


@pytest.mark.asyncio
async def test_workflow_repeats_weekly(actions_fs, harvest_config_file, fake_zenodo):
    workflow_id = f"zenodo-harvest-{uuid.uuid4()}"

    result = await _run_workflow(workflow_id, repeat_every_days=7, max_runs=2)

    assert result["run_number"] == 2  # noqa: PLR2004
    assert result["run_id"] == f"{workflow_id}-run-002"
    instance_dir = Path(result["run_dir"]).parent  # the Action instance's folder
    assert str(instance_dir).startswith(str(actions_fs / workflow_id))
    first, second = (
        json.loads((instance_dir / f"run-00{n}" / RUN_CONFIG_FILE).read_text())
        for n in (1, 2)
    )
    assert first["run_id"] == f"{workflow_id}-run-001"
    # Start to start, so the weekday stays the same; the test server skips the wait.
    gap = datetime.fromisoformat(second["started_at"]) - datetime.fromisoformat(
        first["started_at"]
    )
    assert timedelta(days=7) <= gap < timedelta(days=7, minutes=1)
    # Each run searches Zenodo and writes its own report.
    assert len(fake_zenodo.requests) == 2 * len(fake_zenodo.pages)
    assert (instance_dir / "run-001" / REPORT_FILE).is_file()
    assert Path(result["report_path"]).parent == instance_dir / "run-002"


@pytest.mark.asyncio
async def test_failed_run_ends_the_repeated_runs(actions_fs, galaxy_config):
    galaxy_config.job_states = ["error"]

    with pytest.raises(WorkflowFailureError):
        await _run_workflow(f"zenodo-harvest-{uuid.uuid4()}", repeat_every_days=7)

    [report_path] = actions_fs.rglob(REPORT_FILE)  # no second run
    assert report_path.parent.name == "run-001"
    assert json.loads(report_path.read_text())["failure"]["step"] == "galaxy"


@pytest.mark.asyncio
async def test_stopping_ends_the_repeated_runs(actions_fs, harvest_config_file):
    task_queue = f"test-{uuid.uuid4()}"
    async with await WorkflowEnvironment.start_time_skipping() as env:
        async with Worker(
            env.client,
            task_queue=task_queue,
            workflows=[ZenodoHarvestWorkflow],
            activities=ALL_ACTIVITIES,
            activity_executor=ThreadPoolExecutor(max_workers=2),
        ):
            handle = await env.client.start_workflow(
                ZenodoHarvestWorkflow.run,
                ZenodoHarvestWorkflowInput(user_id="u1", repeat_every_days=7),
                id=f"zenodo-harvest-{uuid.uuid4()}",
                task_queue=task_queue,
            )
            await handle.cancel()  # what NOMAD's Stop button does

            with pytest.raises(WorkflowFailureError):
                await handle.result()
    # Stopping is no failure, so no failure report is written.
    for report_path in actions_fs.rglob(REPORT_FILE):
        assert json.loads(report_path.read_text())["failure"] is None


# Report


def test_write_report_after_placeholder(tmp_path):
    # The placeholder echoes the work list as manifest, which is no manifest.
    work_list = {"run_id": "run-1", "records": [{"record_id": "101"}]}
    for name in (WORK_LIST_FILE, MANIFEST_FILE):
        (tmp_path / name).write_text(json.dumps(work_list))
    steps = {"galaxy": {"status": "ok", "placeholder": True}}

    result = ActivityEnvironment().run(
        write_report, WriteReportInput(run_dir=str(tmp_path), steps=steps)
    )

    report = json.loads(Path(result.report_path).read_text())
    assert Path(result.report_path) == tmp_path / REPORT_FILE
    assert [r["record_id"] for r in report["records"]] == ["101"]
    assert report["failed_files"] == []
    assert Path(result.markdown_path).read_text().startswith("# Zenodo harvest")


def _email_input(tmp_path) -> EmailReportInput:
    steps = {"galaxy": {"status": "skipped", "reason": "no_work"}}
    written = ActivityEnvironment().run(
        write_report, WriteReportInput(run_dir=str(tmp_path), steps=steps)
    )
    return EmailReportInput(
        report_path=written.report_path,
        markdown_path=written.markdown_path,
        user_id="u1",
    )


def test_email_report_without_smtp(tmp_path, monkeypatch):
    monkeypatch.delenv("HARVEST_SMTP_HOST", raising=False)

    result = ActivityEnvironment().run(email_report, _email_input(tmp_path))

    assert (result.sent, result.reason) == (False, "smtp_not_configured")


def test_email_report_with_incomplete_smtp(tmp_path, monkeypatch):
    monkeypatch.setenv("HARVEST_SMTP_HOST", "127.0.0.1")
    monkeypatch.delenv("HARVEST_SMTP_FROM", raising=False)

    with pytest.raises(ApplicationError, match="HARVEST_SMTP_FROM") as error:
        ActivityEnvironment().run(email_report, _email_input(tmp_path))
    assert error.value.non_retryable
