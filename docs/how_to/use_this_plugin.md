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

## Harvest SPM datasets from Zenodo

The **ZenodoHarvestAction** finds openly licensed SPM (scanning probe microscopy, e.g. STM, STS and AFM)
datasets on Zenodo, converts their raw files to NeXus on Galaxy and stores each Zenodo dataset as a
private NOMAD project, with one folder per record version. It remembers what it has processed, so the
next run only handles new files.

The form lists the raw file extensions to convert; by default, all that
[pynxtools-spm](https://github.com/FAIRmat-NFDI/pynxtools-spm){:target="_blank" rel="noopener"} reads.
For more SPM techniques, also widen `zenodo_query` in the harvest config.

Start it from **Actions** in the main menu. No project needs to be selected.

### Form fields

| Field | Meaning |
|---|---|
| `dry_run` | Default on. Search, plan and convert, but create no NOMAD projects and keep the record of processed files unchanged. |
| `galaxy_workflow_id` | ID of the harvest workflow in your Galaxy account. Empty uses the harvest config's `galaxy_workflow_id`. |
| `zenodo_query` | Zenodo search query ([search syntax](https://help.zenodo.org/guides/search/){:target="_blank" rel="noopener"}). Empty uses the harvest config's `zenodo_query`. |
| `zenodo_record_ids` | Only these Zenodo records, in place of the query. A concept record ID means its latest version. Unknown IDs are reported as `not_found`. |
| `raw_extensions` | Raw file extensions converted to NeXus. Default `.sxm`, `.dat`, `.sm4`, `.spm`, `.flt`. Written in any case, with or without the dot. |
| `max_records` | Process at most this many Zenodo records with new work. Empty means no limit. |
| `records_per_work_list` | Default 1. Zenodo records sent to Galaxy together in one work list; each work list is one Galaxy run. Empty means all records in one work list. Example: `max_records` 6 and `records_per_work_list` 2 → 6 records in 3 Galaxy runs of 2 records. |
| `repeat_every_days` | Start a new run this many days after the previous one started (7 = weekly). Empty means one run. |
| `max_runs` | With `repeat_every_days`: stop after this many runs. Empty means until the Action is stopped. |
| `allow_duplicate_projects` | Default off. On: also convert raw files that are already in one of your NOMAD projects. |
| `notify_email` | Optional. One more address that gets the run's report, besides your NOMAD account's address. |
| `galaxy_api_key` | Your Galaxy API key. Empty uses the worker's `GALAXY_API_KEY`, if the administrators set one. |
| `zenodo_token` | Optional Zenodo token for larger search pages. Empty uses the worker's `ZENODO_TOKEN`, if set. |

The two keys are secrets: the form hides them, and they are never written to the run folder, the
report, the logs or Galaxy. See [NOMAD: Individual user secrets](https://docs.nomad-lab.eu/howto/plugins/types/actions.html#individual-user-secrets){:target="_blank" rel="noopener"}.

### One run per user

Each user has their own record of processed files, so users do not block each other. A user can
run one harvest at a time; a second one fails at once with the ID of the active run.

### Duplicate projects

A raw file is recognized by its SHA-256 checksum. If it is already in one of your NOMAD projects,
it is not converted again and the report lists it as `duplicate`, with the project's upload ID.
Other users' private projects do not count. Raw files with an entry in NOMAD, including other users'
published ones, are skipped as already known (see [State project](#state-project)). Each version
folder holds a `zenodo_harvest.json` file
with its Zenodo record and the checksums of its raw and NeXus files.

### Project layout

One project per Zenodo dataset, named `zenodo_<concept record ID>`. A new version of the dataset is
added to the same project:

```
zenodo_22402764/
  nomad.json                  links (DOIs) to all harvested versions; Zenodo IDs per entry
  10.5281_zenodo.22402765/    one folder per version, named after its DOI
    zenodo_harvest.json
    <raw file stem>/          raw file, NeXus file and eln_data.yaml
    Complementaries/          the dataset's other files (README, notes, images, ...)
```

`Complementaries` holds every file of the dataset that is not a raw file and not matched by the
harvest config's `ignore_patterns`, at its path in the dataset. Loose files larger than
`max_archive_bytes` are not downloaded (reported as `too_large`).

### Zenodo IDs on each entry

Each NeXus entry gets two searchable fields, set in `nomad.json`:

| Field | Value |
|---|---|
| `external_id` | `zenodo:<concept record ID>:sha256:<raw file SHA-256>` |
| `references` | concept DOI, version DOI and the raw file's Zenodo URL (`#md5=<MD5>` for a loose file) |

Find an entry in the GUI by typing `external_id = <value>` in an app's search bar.

### State project

Your record of processed files (`state.json`) is kept in your private project
`zenodo_harvest_state`. Each run reads it before planning and saves it after each batch, so the record
survives a new worker or a lost artifacts folder.

Before planning, each run also searches NOMAD for entries of the found Zenodo datasets (your own and
published ones) and updates `state.json`:

- A raw file with an entry in NOMAD is not downloaded or converted again.
- A raw file whose entry was deleted in NOMAD is converted again by the next run.
- Failed files and archives are known only from `state.json`. Without the state project, the next run
  tries them again.

Each real run (not a dry run) also copies its report there: `reports/<start time in UTC>/report.json`
and `report.md`. Open them in the project's file browser.

### Batches

A run sends its records to Galaxy in batches of `records_per_work_list` records, one after the other.
Each batch is one Galaxy run with its own work list and folder in the run folder (`batch-001`,
`batch-002`, ...).

- With 1 record per batch, a failing dataset does not hold up the others.
- A failed batch does not stop the next ones; the run is marked failed at the end (see
  [Failed runs](#failed-runs)).
- Once the limit of 9 unpublished harvest projects is reached, the remaining batches are skipped and
  handled by a later run. NOMAD allows 10 unpublished projects per user; one stays free for the state
  project.

### Repeated runs

- All runs of a series belong to one Action, which shows as `RUNNING` until the series ends.
- Each run has its own folder (`run-001`, `run-002`, ...) in the Action's artifacts.
- **Stop** ends the series, also while it waits for the next run.

### Failed runs

A failed run fails the Action and **ends the series**. A run fails when a step fails, or when a batch
failed after all batches ran. Its report is still written and emailed, with the failed step and the
error; the email subject ends in "run failed". Fix the cause and start the Action again.
Already processed files are not handled twice.

### Report

Each run writes `report.json` and `report.md` into its run folder (and a copy into the
[state project](#state-project)): the Zenodo search, the work list,
each batch, Galaxy, the NOMAD projects (with their upload IDs) and every skipped or failed file with its reason,
duplicates included. It is emailed to the address of your NOMAD account.

### Worker settings

Settings that stay with the installation are in the harvest config file
(`actions/zenodo_harvest/config/harvest_config.json`, or the file in the entry point's
`harvest_config_path`). `state_dir` sets where the records of processed files are kept; empty
means NOMAD's action artifacts folder.

The report email uses NOMAD's own mail server: the
[`mail` section of `nomad.yaml`](https://docs.nomad-lab.eu/reference/config.html#mail){:target="_blank" rel="noopener"}.
Without `mail.enabled`, no email is sent.

Secrets that are not entered in the form are read from the worker's environment:

| Variable | Needed for |
|---|---|
| `GALAXY_API_KEY` | running the Galaxy workflow, when the form has no `galaxy_api_key` |
| `ZENODO_TOKEN` | optional: larger Zenodo result pages, when the form has no `zenodo_token` |
| `NOMAD_PAT` | creating projects through the REST API (only when `nomad_url` is set in the harvest config) |

## Troubleshooting

- **Nothing happens after running the action**: check that a CPU action worker is running (in nomad-distro-dev, `uv run poe cpuworker`) and Temporal is up (`docker compose up -d`).
- **The run reaches `FAILED`**: check the action's logs. If the failure is inside the Galaxy call specifically, `scripts/test_galaxy_roundtrip.py` reproduces just that part standalone, without needing NOMAD or Temporal — useful for telling Galaxy-side issues (including occasional Galaxy compute-cluster flakes) apart from NOMAD-side ones.
- **The result entry didn't appear**: `create_result_entry` requires the original upload to still be unpublished.
