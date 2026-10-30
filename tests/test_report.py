# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
from nomad_eosc_galaxy_actions.manifest import Manifest, Results
from nomad_eosc_galaxy_actions.report import (
    REPORT_SCHEMA_VERSION,
    build_report,
    email_subject,
    render_markdown,
)

RUN_CONFIG = {
    "action_instance_id": "run-1",
    "run_id": "run-1",
    "started_at": "2026-10-21T08:00:00+00:00",
    "dry_run": False,
}
CANDIDATES = {
    "candidates": [{}, {}],
    "rejected": [
        {"record_id": "1", "reason": "license_not_allowed"},
        {"record_id": "2", "reason": "license_not_allowed"},
        {"record_id": "3", "reason": "duplicate"},
    ],
}
WORK_LIST = {
    "records": [
        {"record_id": "101", "title": "STM | Au(111)\nscans", "version_doi": "d1"},
        {"record_id": "102", "title": "Second", "version_doi": None},
    ]
}
SKIPPED = {"skipped": [{"record_id": "101", "key": "big.zip", "reason": "too_large"}]}
STEPS = {
    "galaxy": {"status": "ok", "placeholder": False},
    "nomad": {
        "status": "ok",
        "upload_ids": {"101": "up-1"},
        "created": ["101"],
        "left_out": {"102": "project_limit"},
    },
    "merge": {"status": "ok", "success": 1, "failed": 3, "retried": 0},
}


def _file(sha256, *, action="process", role="raw", reason=None):
    return {
        "concept_record_id": "100",
        "record_id": "101",
        "source": "archive",
        "path": f"d/{sha256}.sxm",
        "size": 1,
        "sha256": sha256,
        "role": role,
        "action": action,
        "reason": reason,
    }


MANIFEST = Manifest(
    schema_version=1,
    run_id="run-1",
    tool_version="0.1",
    files=[
        _file("ok"),
        _file("bad"),
        _file("lost"),  # no result
        _file("member", action="skipped", reason="unreadable"),
        _file("known", action="already_known"),
        _file("readme", role="context"),
    ],
)
RESULTS = Results(
    schema_version=1,
    run_id="run-1",
    files=[
        {"sha256": "ok", "status": "success"},
        {"sha256": "bad", "status": "failed", "error": "reader error"},
    ],
)


def _report(**changes):
    inputs = {
        "run_config": RUN_CONFIG,
        "candidates": CANDIDATES,
        "work_list": WORK_LIST,
        "skipped_files": SKIPPED,
        "manifest": MANIFEST,
        "results": RESULTS,
        "steps": STEPS,
    } | changes
    return build_report(**inputs)


def test_full_report():
    report = _report()

    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert (report["run_id"], report["dry_run"]) == ("run-1", False)
    assert report["zenodo"] == {
        "candidates": 2,
        "rejected": {"duplicate": 1, "license_not_allowed": 2},
    }
    assert [
        (r["record_id"], r["upload_id"], r["left_out"]) for r in report["records"]
    ] == [
        ("101", "up-1", None),
        ("102", None, "project_limit"),
    ]
    assert report["skipped_files"] == SKIPPED["skipped"]
    assert [(f["path"], f["error"]) for f in report["failed_files"]] == [
        ("d/bad.sxm", "reader error"),
        ("d/lost.sxm", "no_result"),
        ("d/member.sxm", "unreadable"),
    ]


def test_report_of_a_run_without_galaxy():
    steps = {"galaxy": {"status": "skipped", "reason": "no_work"}}
    report = _report(
        work_list=None, skipped_files=None, manifest=None, results=None, steps=steps
    )

    assert report["records"] == report["skipped_files"] == report["failed_files"] == []


def test_placeholder_has_no_failed_files():
    steps = STEPS | {"galaxy": {"status": "ok", "placeholder": True}}

    assert _report(steps=steps)["failed_files"] == []


def test_markdown():
    text = render_markdown(_report())

    assert text.startswith("# Zenodo harvest report\n\nRun `run-1`, started 2026-10-21")
    assert (
        "| Zenodo search | 2 candidates; rejected: 1 duplicate, 2 license_not_allowed |"
        in text
    )
    assert "| NOMAD projects | ok: 1 created, 0 reused, 1 left out |" in text
    assert "| State | ok: 1 converted, 3 failed, 0 set to retry |" in text
    # Table cells stay on one line, and "|" does not break the table.
    assert "| 101 | STM \\| Au(111) scans | d1 | up-1 |" in text
    assert "| 102 | Second |  | project_limit |" in text
    assert "| 101 | d/bad.sxm | reader error |" in text
    assert "dry run" not in text


def test_markdown_of_a_dry_run_without_work():
    steps = {
        "galaxy": {"status": "skipped", "reason": "no_work"},
        "nomad": {"status": "skipped", "reason": "no_galaxy_run"},
    }
    report = _report(
        run_config=RUN_CONFIG | {"dry_run": True},
        candidates={},
        work_list=None,
        skipped_files=None,
        manifest=None,
        results=None,
        steps=steps,
    )

    text = render_markdown(report)

    assert "**dry run**" in text
    assert "| Zenodo search | 0 candidates; rejected: none |" in text
    assert "| Galaxy | skipped (no_work) |" in text
    assert "| NOMAD projects | skipped (no_galaxy_run) |" in text
    assert "| State | not run |" in text
    # Batches, records, skipped files and failed files are empty.
    assert text.count("None.") == 4  # noqa: PLR2004


def test_failed_run():
    error = "Galaxy invocation inv-1 ended in state 'error'"
    failure = {"step": "galaxy", "error": error}
    steps = {"failure": failure}
    report = _report(manifest=None, results=None, steps=steps)

    assert report["failure"] == failure
    text = render_markdown(report)
    assert "**Run failed** in step `galaxy`: Galaxy invocation inv-1" in text
    assert "| Galaxy | **failed** |" in text
    assert "| NOMAD projects | not run |" in text
    assert "| Zenodo search | 2 candidates;" in text  # steps before it keep results


def test_failure_in_the_first_step_hides_empty_counts():
    report = _report(
        candidates={},
        work_list=None,
        skipped_files=None,
        manifest=None,
        results=None,
        steps={"failure": {"step": "zenodo_search", "error": "HTTP 503"}},
    )

    text = render_markdown(report)

    assert "| Zenodo search | **failed** |" in text
    assert "0 candidates" not in text


def test_duplicates_are_skipped_files_with_their_project():
    duplicates = {
        "duplicates": [
            {
                "record_id": "101",
                "key": "d/a.sxm",
                "sha256": "sha-a",
                "upload_id": "up-7",
                "reason": "duplicate",
            }
        ]
    }

    report = _report(duplicates=duplicates)

    assert report["skipped_files"] == SKIPPED["skipped"] + duplicates["duplicates"]
    text = render_markdown(report)
    assert "| 101 | d/a.sxm | duplicate (in project up-7) |" in text
    assert "| 101 | big.zip | too_large |" in text


def test_email_subject():
    assert email_subject(_report()) == "Zenodo harvest report 2026-10-21"
    failed = _report(steps={"failure": {"step": "merge", "error": "x"}})
    assert email_subject(failed) == "Zenodo harvest report 2026-10-21: run failed"
    assert email_subject({"started_at": None}) == "Zenodo harvest report "
