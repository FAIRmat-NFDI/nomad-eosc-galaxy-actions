# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""The report of one harvest run: `report.json` and `report.md`.

Built from the files in the run folder (search result, work list, skipped
files, Galaxy's manifest and results) and the workflow's step summaries.
Every record and file that was not handled is listed with its reason.

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

# Version of the report.json layout (keys and their meaning).
REPORT_SCHEMA_VERSION = 1


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
) -> dict[str, Any]:
    """Collect the content of `report.json` from the run's files.

    `steps` holds the workflow's summary of the Galaxy, NOMAD and merge
    steps. Files of steps that did not run are None.
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
        "run_id": run_config.get("action_instance_id"),
        "started_at": run_config.get("started_at"),
        "dry_run": run_config.get("dry_run"),
        "zenodo": {
            "candidates": len(candidates.get("candidates", [])),
            "rejected": dict(sorted(rejected.items())),
        },
        "records": records,
        "skipped_files": (skipped_files or {}).get("skipped", []),
        "failed_files": failed,
        "steps": steps,
    }


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
    rejected = ", ".join(f"{n} {reason}" for reason, n in zenodo["rejected"].items())
    overview = [
        [
            "Zenodo search",
            f"{zenodo['candidates']} candidates; rejected: {rejected or 'none'}",
        ],
        [
            "Work list",
            f"{len(report['records'])} records; "
            f"{len(report['skipped_files'])} files skipped",
        ],
        ["Galaxy", _step(steps.get("galaxy"))],
        ["NOMAD projects", _nomad_line(steps.get("nomad"))],
        ["State", _merge_line(steps.get("merge"))],
    ]
    records = [
        [r["record_id"], r["title"], r["doi"], r["upload_id"] or r["left_out"]]
        for r in report["records"]
    ]
    skipped = [[s["record_id"], s["key"], s["reason"]] for s in report["skipped_files"]]
    failed = [[f["record_id"], f["path"], f["error"]] for f in report["failed_files"]]
    dry_run = ", **dry run** (no NOMAD projects, state unchanged)"
    return (
        "# Zenodo harvest report\n\n"
        f"Run `{report['run_id']}`, started {report['started_at']}"
        f"{dry_run if report['dry_run'] else ''}.\n\n"
        + _table(["Step", "Result"], overview)
        + "\n## Records\n\n"
        + _table(["Record", "Title", "DOI", "NOMAD upload ID or reason"], records)
        + "\n## Skipped files\n\n"
        + _table(["Record", "File", "Reason"], skipped)
        + "\n## Failed files\n\n"
        + _table(["Record", "File", "Error"], failed)
    )
