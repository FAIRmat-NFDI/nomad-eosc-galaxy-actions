# Install This Plugin

Installing a NOMAD plugin has two parts: getting the Python package into the environment, and turning its entry points on in `nomad.yaml`. The general mechanics are covered by the [NOMAD plugin documentation](https://nomad-lab.eu/prod/v1/staging/docs/plugins/plugins.html#add-a-plugin-to-your-nomad){:target="_blank" rel="noopener"}; below is where each part concretely lives for FAIRmat's two workspaces.

## In `nomad-distro-dev` (local development)

1. Add this repo as a git submodule under `packages/`:

   ```bash
   git submodule add git@github.com:FAIRmat-NFDI/nomad-eosc-galaxy-actions.git packages/nomad-eosc-galaxy-actions
   ```

2. Register it as a workspace package in the root `pyproject.toml`: add `"nomad-eosc-galaxy-actions"` to the plugin `dependencies` list, and `nomad-eosc-galaxy-actions = { workspace = true }` under `[tool.uv.sources]`. Then run `uv sync`.

3. Add all three entry points to the root `nomad.yaml`:

   ```yaml
   plugins:
     entry_points:
       include:
         - "nomad_eosc_galaxy_actions.actions.find_peaks:find_peaks_action"
         - "nomad_eosc_galaxy_actions.schema_packages.find_peaks:find_peaks_schema_package"
         - "nomad_eosc_galaxy_actions.example_uploads:find_peaks_example_upload"
       options:
         nomad_eosc_galaxy_actions.actions.find_peaks:find_peaks_action:
           galaxy_workflow_id: "..."   # see below
           # galaxy_url: "https://usegalaxy.eu"  # default, override if needed
   ```

   The example upload isn't strictly required. Leave it out if you don't want the
   **XPS Peak Finding via Galaxy** entry showing up under *Uploads &rarr; Add example
   uploads*; but it's the fastest way for anyone else to try this plugin, see
   [Use This Plugin](use_this_plugin.md).

## In a (production / Oasis deployment)

- Once this plugin is published to PyPI: add it, with a pinned version, to `pyproject.toml`'s `[project.optional-dependencies].plugins` list, alongside the other plugin packages there.
- Until then: install it as a git dependency in that same list instead, e.g `"nomad-eosc-galaxy-actions @ git+https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions.git@<tag>"`.
- Ensure that all three entry points are activated in your NOMAD configuration (e.g., your `nomad.yaml`).

## Import the workflow

The Action calls an already-imported Galaxy workflow by ID; it never imports or looks one up itself. Import it once, into whichever Galaxy account the Action will run as:

```bash
echo "GALAXY_API_KEY=..." > .env
uv run --env-file .env scripts/import_workflow.py
```

Put the printed `workflow_id` into `nomad.yaml`'s `galaxy_workflow_id` option above.

## Temporal

This plugin needs the CPU action queue running. For local development, run `docker compose up -d` (brings up Temporal) and `uv run poe cpuworker` (the worker process), from the `nomad-distro-dev` repo root.
