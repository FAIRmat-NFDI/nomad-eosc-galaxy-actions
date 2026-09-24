# How to Use This Plugin

## Add This Plugin to Your NOMAD installation

Read the [NOMAD plugin documentation](https://nomad-lab.eu/prod/v1/staging/docs/plugins/plugins.html#add-a-plugin-to-your-nomad){:target="_blank" rel="noopener"} for all details on how to deploy the plugin on your NOMAD instance, and [Install This Plugin](install_this_plugin.md) for this plugin's own setup (importing the Galaxy workflow, configuring `galaxy_workflow_id`).

## Find peaks in a spectrum

The fastest way to try this is the bundled NOMAD example upload: on the *Uploads* page, click **Add example uploads** and choose **XPS Peak Finding via Galaxy** — it comes with a ready-to-use spectrum, and its own README walks through the same steps below. The example upload only appears if it's enabled in your deployment's `nomad.yaml`, see [Install This Plugin](install_this_plugin.md).

To do it with your own data instead:

1. Upload an XPS spectrum as an `NXxps` `.nxs` file, ideally one with several distinct peaks so there's something to find, and let it process into an entry — `examples/galaxy_demo/example_data/vms_regular_ref.nxs` in this repo is a good reference if you don't have one of your own. Copy its `entry_id` from the entry's overview page.
2. In the NOMAD GUI, open **Actions** from the main menu (or use the **Run Action** button on the same project) and start a new run of **find_peaks_action**.
3. Fill in the auto-generated form: `spectrum_entry_id` (from step 1), a Galaxy API key, and optionally `prominence`/`distance`/`height` to tune `scipy.signal.find_peaks` — left empty, the underlying tool returns every local maximum, which for a real spectrum is usually far more peaks than useful; `prominence=10000, distance=20` is a reasonable starting point for a survey scan in counts per second. Leave the API key empty to use the worker's own `GALAXY_API_KEY` environment variable instead, if your deployment has one configured (see [Explanation > Handling secrets](../explanation/explanation.md) for why this field, not a schema quantity, is where a per-user key belongs).
4. Run it, and watch its status in the Actions view move from `RUNNING` to `COMPLETED`.

Once `COMPLETED`, a new entry appears in the same upload: the original spectrum's data, plus an `NXfit` group holding one `NXpeak` per detected peak (`data/position` in the spectrum's own energy units, `data/intensity` in its own intensity units).

## Troubleshooting

- **Nothing happens after running the action**: check that a CPU action worker is running (in nomad-distro-dev, `uv run poe cpuworker`) and Temporal is up (`docker compose up -d`).
- **The run reaches `FAILED`**: check the action's logs. If the failure is inside the Galaxy call specifically, `scripts/test_galaxy_roundtrip.py` reproduces just that part standalone, without needing NOMAD or Temporal — useful for telling Galaxy-side issues (including occasional Galaxy compute-cluster flakes) apart from NOMAD-side ones.
- **The result entry didn't appear**: `create_result_entry` requires the original upload to still be unpublished.
