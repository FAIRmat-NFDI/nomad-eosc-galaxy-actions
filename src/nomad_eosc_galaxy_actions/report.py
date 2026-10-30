# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""The report of one harvest run: `report.json` and `report.md`.

Built from the files in the run folder (search result, work list, skipped
files, Galaxy's manifest and results) and the workflow's step summaries.
Every record and file that was not handled is listed with its reason. A
failed run is reported too, with the failed step and its error.

Framework-agnostic: no NOMAD or Temporal imports.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from nomad_eosc_galaxy_actions.manifest import (
    FileResult,
    Manifest,
    Results,
    file_status,
)

# Version of the report.json layout (keys and their meaning); 2 adds `failure`,
# 3 adds duplicate raw files (with their `upload_id`) to `skipped_files`,
# 4 adds `batches`: one entry per work list sent to Galaxy.
REPORT_SCHEMA_VERSION = 4


def failed_files(manifest: Manifest, results: Results) -> list[dict[str, str]]:
    """Raw files that Galaxy could not convert, with the error."""
    by_sha256: dict[str, FileResult] = {r.sha256: r for r in results.files}
    failed = []
    for file in manifest.files:
        if file.role != "raw" or file.action == "already_known":
            continue
        status, error = file_status(file, by_sha256.get(file.sha256))
        if status == "failed":
            failed.append(
                {"record_id": file.record_id, "path": file.path, "error": error}
            )
    return failed


def build_report(  # noqa: PLR0913
    *,
    run_config: dict[str, Any],
    candidates: dict[str, Any],
    work_list: dict[str, Any] | None,
    skipped_files: dict[str, Any] | None,
    manifest: Manifest | None,
    results: Results | None,
    steps: dict[str, Any],
    duplicates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Collect the content of `report.json` from the run's files.

    `steps` holds the workflow's summary of the Galaxy, NOMAD and merge
    steps, and `failure` ({step, error}) when the run failed. Files of steps
    that did not run are None.
    """
    nomad = steps.get("nomad", {})
    upload_ids = nomad.get("upload_ids", {})
    left_out = nomad.get("left_out", {})
    records = [
        {
            "record_id": record["record_id"],
            "title": record.get("title", ""),
            "doi": record.get("version_doi"),
            "upload_id": upload_ids.get(record["record_id"]),
            "left_out": left_out.get(record["record_id"]),
        }
        for record in (work_list or {}).get("records", [])
    ]
    placeholder = steps.get("galaxy", {}).get("placeholder")
    failed = (
        failed_files(manifest, results)
        if manifest and results and not placeholder
        else []
    )
    rejected = Counter(r["reason"] for r in candidates.get("rejected", []))
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "run_id": run_config.get("run_id"),
        "started_at": run_config.get("started_at"),
        "dry_run": run_config.get("dry_run"),
        "failure": steps.get("failure"),
        "zenodo": {
            "candidates": len(candidates.get("candidates", [])),
            "rejected": dict(sorted(rejected.items())),
        },
        "records": records,
        "skipped_files": (skipped_files or {}).get("skipped", [])
        + (duplicates or {}).get("duplicates", []),
        "failed_files": failed,
        "batches": steps.get("batches", []),
        "steps": steps,
    }


def _reason(skipped: dict[str, Any]) -> str:
    """The reason, naming the project that already holds a duplicate."""
    if skipped.get("upload_id"):
        return f"{skipped['reason']} (in project {skipped['upload_id']})"
    return skipped["reason"]


def email_subject(report: dict[str, Any]) -> str:
    """For example "Zenodo harvest report 2026-10-21: run failed"."""
    subject = f"Zenodo harvest report {(report.get('started_at') or '')[:10]}"
    return f"{subject}: run failed" if report.get("failure") else subject


def _cell(value: Any) -> str:
    """A Markdown table cell: no line breaks, `|` escaped."""
    text = "" if value is None else str(value)
    return " ".join(text.split()).replace("|", "\\|")


def _table(header: list[str], rows: list[list[Any]]) -> str:
    if not rows:
        return "None.\n"
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
        *("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows),
    ]
    return "\n".join(lines) + "\n"


def _step(summary: dict[str, Any] | None) -> str:
    """One line per step: `ok: <details>`, `skipped (<reason>)` or `not run`."""
    if not summary:
        return "not run"
    if summary.get("status") == "skipped":
        return f"skipped ({summary.get('reason')})"
    return str(summary.get("status"))


def _galaxy_line(galaxy: dict[str, Any] | None) -> str:
    if not galaxy or "batches" not in galaxy:
        return _step(galaxy)
    if galaxy["failed"]:
        return f"{galaxy['failed']} of {galaxy['batches']} batches failed"
    return f"ok: {galaxy['batches']} batches"


def _batch_result(batch: dict[str, Any]) -> str:
    """What happened to one batch, in a few words."""
    if batch.get("skipped"):
        return f"skipped ({batch['skipped']})"
    if batch.get("failure"):
        failure = batch["failure"]
        return f"failed in {failure['step']}: {failure['error']}"
    return _nomad_line(batch.get("nomad"))


def _nomad_line(nomad: dict[str, Any] | None) -> str:
    if not nomad or nomad.get("status") != "ok":
        return _step(nomad)
    created = len(nomad["created"])
    reused = len(nomad["upload_ids"]) - created
    return f"ok: {created} created, {reused} reused, {len(nomad['left_out'])} left out"


def _merge_line(merge: dict[str, Any] | None) -> str:
    if not merge or merge.get("status") != "ok":
        return _step(merge)
    return (
        f"ok: {merge['success']} converted, {merge['failed']} failed, "
        f"{merge['retried']} set to retry"
    )


def render_markdown(report: dict[str, Any]) -> str:
    """The report for people; also the email body."""
    steps = report["steps"]
    zenodo = report["zenodo"]
    failure = report.get("failure")
    rejected = ", ".join(f"{n} {reason}" for reason, n in zenodo["rejected"].items())
    overview = [
        [
            "zenodo_search",
            "Zenodo search",
            f"{zenodo['candidates']} candidates; rejected: {rejected or 'none'}",
        ],
        [
            "work_list",
            "Work list",
            f"{len(report['records'])} records; "
            f"{len(report['skipped_files'])} files skipped",
        ],
        ["galaxy", "Galaxy", _galaxy_line(steps.get("galaxy"))],
        ["nomad", "NOMAD projects", _nomad_line(steps.get("nomad"))],
        ["merge", "State", _merge_line(steps.get("merge"))],
    ]
    # The failed step shows "failed"; the steps after it show "not run".
    overview = [
        [name, "**failed**" if failure and failure["step"] == step else line]
        for step, name, line in overview
    ]
    records = [
        [r["record_id"], r["title"], r["doi"], r["upload_id"] or r["left_out"]]
        for r in report["records"]
    ]
    batches = [
        [
            b["folder"],
            ", ".join(b["records"]),
            (b.get("galaxy") or {}).get("history_id"),
            _batch_result(b),
        ]
        for b in report.get("batches", [])
    ]
    skipped = [[s["record_id"], s["key"], _reason(s)] for s in report["skipped_files"]]
    failed = [[f["record_id"], f["path"], f["error"]] for f in report["failed_files"]]
    dry_run = ", **dry run** (no NOMAD projects, state unchanged)"
    return (
        "# Zenodo harvest report\n\n"
        f"Run `{report['run_id']}`, started {report['started_at']}"
        f"{dry_run if report['dry_run'] else ''}.\n\n"
        + (
            f"**Run failed** in step `{failure['step']}`: {_cell(failure['error'])}\n\n"
            if failure
            else ""
        )
        + _table(["Step", "Result"], overview)
        + "\n## Batches\n\n"
        + _table(["Batch", "Records", "Galaxy history", "Result"], batches)
        + "\n## Records\n\n"
        + _table(["Record", "Title", "DOI", "NOMAD upload ID or reason"], records)
        + "\n## Skipped files\n\n"
        + _table(["Record", "File", "Reason"], skipped)
        + "\n## Failed files\n\n"
        + _table(["Record", "File", "Error"], failed)
    )
