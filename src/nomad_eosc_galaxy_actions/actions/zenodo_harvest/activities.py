# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Activities for the zenodo-harvest action.

Each activity is a thin wrapper that resolves NOMAD-specific things (config,
storage folders) and then calls a plain function, so that the logic itself
is testable without a NOMAD installation or a Temporal worker.
"""

import json
import os
from pathlib import Path
from typing import Any

from temporalio import activity

from nomad_eosc_galaxy_actions import zenodo_client
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.harvest_config import (
    HarvestConfig,
    load_harvest_config,
)
from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
    PrepareRunInput,
    PrepareRunResult,
    SearchZenodoInput,
    SearchZenodoResult,
)

ZENODO_HARVEST_ACTION = (
    "nomad_eosc_galaxy_actions.actions.zenodo_harvest:zenodo_harvest_action"
)
RUN_CONFIG_FILE = "run_config.json"
# Version of the run_config.json layout. Bump it when a key is renamed,
# removed or changes meaning, so readers can recognise older files.
RUN_CONFIG_SCHEMA_VERSION = 1
CANDIDATES_FILE = "candidates.json"
# Version of the candidates.json layout (same rule as RUN_CONFIG_SCHEMA_VERSION).
CANDIDATES_SCHEMA_VERSION = 1
# Zenodo allows larger pages for authenticated requests.
PAGE_SIZE_ANONYMOUS = 25
PAGE_SIZE_AUTHENTICATED = 100


def _harvest_config() -> HarvestConfig:
    """Load the harvest config file named by the entry point (or the shipped one)."""
    from nomad.config import config  # noqa: PLC0415

    entry_point = config.get_plugin_entry_point(ZENODO_HARVEST_ACTION)
    return load_harvest_config(entry_point.harvest_config_path)


def write_run_config(
    run_dir: str | Path, harvest_config: dict[str, Any], data: PrepareRunInput
) -> Path:
    """Write the effective configuration of one run to `run_dir/run_config.json`.

    The file holds:

    - `schema_version`: version of this file's layout (keys and their meaning),
      not of the data. A reader that finds an older version knows the layout
      differs and can convert it. See RUN_CONFIG_SCHEMA_VERSION.
    - `action_instance_id`, `started_at`, `dry_run`, `max_records`: the run's inputs.
    - `harvest_config`: the harvest config used by this run (already upgraded
      to the current version), so the run can be explained even after the
      config file changes.

    Args:
        run_dir: the run's artifacts directory (created if missing).
        harvest_config: content of the loaded harvest config.
        data: the run's own inputs.

    Returns:
        Path of the written file.
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    run_config = {
        "schema_version": RUN_CONFIG_SCHEMA_VERSION,
        "action_instance_id": data.action_instance_id,
        "started_at": data.started_at,
        "dry_run": data.dry_run,
        "max_records": data.max_records,
        "harvest_config": harvest_config,
    }
    path = run_dir / RUN_CONFIG_FILE
    path.write_text(json.dumps(run_config, indent=2), encoding="utf-8")
    return path


@activity.defn
def prepare_run(data: PrepareRunInput) -> PrepareRunResult:
    """Create this run's artifacts folder and record its effective configuration."""
    from nomad.actions.manager import action_instance_artifacts_dir  # noqa: PLC0415

    harvest_config = _harvest_config().model_dump(mode="json")
    run_dir = action_instance_artifacts_dir(data.action_instance_id)
    path = write_run_config(run_dir, harvest_config, data)
    activity.logger.info("Prepared harvest run in %s", run_dir)
    return PrepareRunResult(run_dir=str(run_dir), run_config_path=str(path))


def write_candidates(
    run_dir: str | Path,
    query: str,
    candidates: list[zenodo_client.ZenodoRecord],
    rejections: list[zenodo_client.Rejection],
) -> Path:
    """Write the search result to `run_dir/candidates.json`.

    The file holds `schema_version` (see CANDIDATES_SCHEMA_VERSION), the
    `query`, the `candidates` (full records) and the `rejected` records with
    their reason, so every skipped record can be explained in the report.
    """
    content = {
        "schema_version": CANDIDATES_SCHEMA_VERSION,
        "query": query,
        "candidates": [record.model_dump(mode="json") for record in candidates],
        "rejected": [rejection.model_dump(mode="json") for rejection in rejections],
    }
    path = Path(run_dir) / CANDIDATES_FILE
    path.write_text(json.dumps(content, indent=2), encoding="utf-8")
    return path


@activity.defn
def search_zenodo(data: SearchZenodoInput) -> SearchZenodoResult:
    """Search Zenodo and write the candidates of this run to candidates.json.

    An optional ZENODO_TOKEN environment variable on the worker (an
    institute-wide secret) allows larger pages.
    """
    harvest_config = _harvest_config()
    token = os.environ.get("ZENODO_TOKEN")
    hits = zenodo_client.iter_search_hits(
        zenodo_client.make_session(token),
        harvest_config.zenodo_query,
        zenodo_url=harvest_config.zenodo_url,
        page_size=PAGE_SIZE_AUTHENTICATED if token else PAGE_SIZE_ANONYMOUS,
        request_delay=harvest_config.zenodo_request_delay,
    )
    candidates, rejections = zenodo_client.collect_candidates(
        hits,
        raw_extensions=harvest_config.raw_extensions,
        licenses=harvest_config.licenses,
        max_records=data.max_records,
    )
    path = write_candidates(
        data.run_dir, harvest_config.zenodo_query, candidates, rejections
    )
    activity.logger.info(
        "Zenodo search: %d candidates, %d rejected", len(candidates), len(rejections)
    )
    return SearchZenodoResult(
        candidates_path=str(path),
        candidates=len(candidates),
        rejected=len(rejections),
    )
