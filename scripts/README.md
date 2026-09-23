# Scripts

Standalone, PEP 723 self-contained scripts (runnable standalone with `uv run scripts/<name>.py`) for setting up and checking the Galaxy side of this plugin without NOMAD or Temporal. Run all of these from the repo root, with a `.env` holding your `usegalaxy.eu` API key (see the [tutorial](../docs/tutorial/tutorial.md) for the full walkthrough).

## `import_workflow.py`

One-time setup: imports the `XPS peak finding` workflow into a Galaxy account. Checks for an existing copy by name first, so re-running it is safe. Prints the imported `workflow_id` — put that into `GALAXY_WORKFLOW_ID` (for `test_galaxy_roundtrip.py` below) and into `galaxy_workflow_id` (for the real Action, see [Install This Plugin](../docs/how_to/install_this_plugin.md)).

```bash
echo "GALAXY_API_KEY=..." > .env
uv run --env-file .env scripts/import_workflow.py
```

## `test_galaxy_roundtrip.py`

Exercises exactly what the Action's activities do against a real Galaxy instance — upload, invoke, poll, download — via the same `galaxy_client` module the Action uses, with no NOMAD or Temporal involved. The fastest way to tell a Galaxy-side problem apart from a NOMAD-side one.

```bash
uv run --env-file .env scripts/test_galaxy_roundtrip.py
uv run --env-file .env scripts/test_galaxy_roundtrip.py path/to/your_spectrum.nxs  # optional
```

## `generate_custom_dict.sh`

Regenerates `.cspell/custom-dictionary.txt` from the current source and docs. Run this if `cspell` (in pre-commit or CI) flags a real, correctly-spelled word as unknown.

```bash
scripts/generate_custom_dict.sh
```
