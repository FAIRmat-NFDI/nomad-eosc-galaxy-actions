# How to Use This Plugin

## Add This Plugin to Your NOMAD installation

Read the [NOMAD plugin documentation](https://nomad-lab.eu/prod/v1/staging/docs/plugins/plugins.html#add-a-plugin-to-your-nomad){:target="_blank" rel="noopener"} for all details on how to deploy the plugin on your NOMAD instance, and [Install This Plugin](install_this_plugin.md) for this plugin's own setup (importing the Galaxy workflow, configuring `galaxy_workflow_id`).

## Find peaks in a spectrum

1. Upload an XPS spectrum as an `NXxps` `.nxs` file, ideally one with several distinct peaks so there's something to find, and let it process into an entry — `examples/galaxy_demo/example_data/vms_regular_ref.nxs` in this repo is a good reference if you don't have one of your own.
2. Create a **Find Peaks Trigger** entry.
3. Set `spectrum_entry_id` to the source XPS spectrum entry's `entry_id` (a plain string) — copy it from that entry's URL/overview page.
4. Enter your Galaxy API key, or leave it empty to use the worker's own `GALAXY_API_KEY` environment variable if one is set (convenient while testing; fine for a shared/institute-wide key, not for a personal one). **Whatever you type here is stored as plain text in the entry** — NOMAD has no masked ELN component today. Don't use a personal key in a shared or published upload; ask whoever administers your deployment about a service-account key instead.
5. Optionally set `prominence`/`distance`/`height` to tune `scipy.signal.find_peaks` — left empty, the underlying tool returns every local maximum, which for a real spectrum is usually far more peaks than useful. `prominence=10000, distance=20` is a reasonable starting point for a survey scan in counts per second.
6. Click **Find Peaks**. `workflow_status` moves through `RUNNING` to `COMPLETED` (click **Get Action Status** to refresh it, or wait for the automatic check after triggering).

Once `COMPLETED`, a new entry appears in the same upload: the original spectrum's data, plus an `NXfit` group holding one `NXpeak` per detected peak (`data/position` in the spectrum's own energy units, `data/intensity` in its own intensity units).

## Troubleshooting

- **Nothing happens after clicking Find Peaks**: check that a CPU action worker is running (in nomad-distro-dev, `uv run poe cpuworker`) and Temporal is up (`docker compose up -d`).
- **`workflow_status` reaches `FAILED`**: check the action's logs. If the failure is inside the Galaxy call specifically, `scripts/test_galaxy_roundtrip.py` reproduces just that part standalone, without needing NOMAD or Temporal — useful for telling Galaxy-side issues (including occasional Galaxy compute-cluster flakes) apart from NOMAD-side ones.
- **The result entry didn't appear**: `create_result_entry` requires the original upload to still be unpublished.
