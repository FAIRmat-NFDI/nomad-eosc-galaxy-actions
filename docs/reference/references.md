# References

## Environment variables and config options

Two separate mechanisms carry Galaxy settings, depending on which code reads them:

| Name | Read by | Where it's set | Purpose |
|---|---|---|---|
| `GALAXY_API_KEY` | `scripts/import_workflow.py`, `scripts/test_galaxy_roundtrip.py` | `.env` in this repo, loaded via `uv run --env-file .env` | Your `usegalaxy.eu` API key, for the standalone scripts only. |
| `GALAXY_WORKFLOW_ID` | `scripts/test_galaxy_roundtrip.py` | `.env`, printed by `import_workflow.py` | The imported workflow's ID, for the standalone scripts only. |
| `GALAXY_API_KEY` | `resolve_api_key` (`actions/find_peaks/resolve.py`) | An environment variable on the NOMAD **worker** process | Fallback used when the `/actions` trigger form's `galaxy_api_key` field is left empty. Same variable name as above, different process reading it — the form field always wins if set. See [Explanation > Handling secrets](../explanation/explanation.md#handling-secrets) for why. |
| `galaxy_workflow_id` | `FindPeaksActionEntryPoint` (`__init__.py`) | `nomad.yaml`'s `plugins.entry_points.options` for `nomad_eosc_galaxy_actions.actions.find_peaks:find_peaks_action` | The Action's own copy of the workflow ID. No default — must be set for the Action to run at all. See [Install This Plugin](../how_to/install_this_plugin.md). |
| `galaxy_url` | `FindPeaksActionEntryPoint` (`__init__.py`) | Same `nomad.yaml` options block as above | Base URL of the Galaxy instance. Defaults to `https://usegalaxy.eu`; override for a different Galaxy instance. |

The two `GALAXY_API_KEY`s are unrelated in practice: one is read by short-lived standalone scripts you run yourself, the other by the long-running NOMAD worker. Set both if you use both paths — setting one doesn't set the other.
