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
from pathlib import Path
from typing import Any

from temporalio import activity

from nomad_eosc_galaxy_actions.actions.zenodo_harvest.models import (
    PrepareRunInput,
    PrepareRunResult,
)

ZENODO_HARVEST_ACTION = (
    "nomad_eosc_galaxy_actions.actions.zenodo_harvest:zenodo_harvest_action"
)
RUN_CONFIG_FILE = "run_config.json"
# Version of the run_config.json layout. Bump it when a key is renamed,
# removed or changes meaning, so readers can recognise older files.
RUN_CONFIG_SCHEMA_VERSION = 1


# Entry point fields that describe what a run harvests. Snapshotting them per
# run makes every run reproducible and its report self-explanatory, even if
# nomad.yaml changes later.
HARVEST_SETTINGS = (
    "zenodo_url",
    "zenodo_query",
    "raw_extensions",
    "context_extensions",
    "ignore_patterns",
    "licenses",
    "max_archive_bytes",
    "max_uncompressed_bytes",
    "galaxy_url",
    "galaxy_workflow_id",
)


def _harvest_entry_point():
    from nomad.config import config  # noqa: PLC0415

    return config.get_plugin_entry_point(ZENODO_HARVEST_ACTION)


def write_run_config(
    run_dir: str | Path, settings: dict[str, Any], data: PrepareRunInput
) -> Path:
    """Write the effective configuration of one run to `run_dir/run_config.json`.

    The file holds:

    - `schema_version`: version of this file's layout (keys and their meaning),
      not of the data. A reader that finds an older version knows the layout
      differs and can convert it. See RUN_CONFIG_SCHEMA_VERSION.
    - `action_instance_id`, `started_at`, `dry_run`, `max_records`: the run's inputs.
    - `settings`: snapshot of the harvest settings used by this run.

    Args:
        run_dir: the run's artifacts directory (created if missing).
        settings: harvest settings from the entry point (see HARVEST_SETTINGS).
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
        "settings": settings,
    }
    path = run_dir / RUN_CONFIG_FILE
    path.write_text(json.dumps(run_config, indent=2), encoding="utf-8")
    return path


@activity.defn
def prepare_run(data: PrepareRunInput) -> PrepareRunResult:
    """Create this run's artifacts folder and record its effective configuration."""
    from nomad.actions.manager import action_instance_artifacts_dir  # noqa: PLC0415

    entry_point = _harvest_entry_point()
    settings = entry_point.model_dump(mode="json", include=set(HARVEST_SETTINGS))
    run_dir = action_instance_artifacts_dir(data.action_instance_id)
    path = write_run_config(run_dir, settings, data)
    activity.logger.info("Prepared harvest run in %s", run_dir)
    return PrepareRunResult(run_dir=str(run_dir), run_config_path=str(path))
