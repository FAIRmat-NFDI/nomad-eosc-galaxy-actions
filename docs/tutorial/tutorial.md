# Tutorial

## Who is this tutorial for?

- People on the Galaxy team who want to see the NOMAD ↔ Galaxy round trip work for themselves, without needing a NOMAD deployment — Part 1 covers this.
- NOMAD plugin developers or users who want to try the full round trip through a real NOMAD deployment — Part 2 covers this.

## What should you should know before this tutorial?

You should be familiar with the general idea of the NOMAD ↔ Galaxy demonstrator.
General knowledge of both NOMAD and Galaxy is also helpful

## What you will know at the end of this tutorial?

You will know

- how to invoke the Galaxy peak finding workflow with and without NOMAD

Throughout this tutorial you'll be running `find_peaks`, the one NOMAD Action this plugin currently implements: it sends an XPS spectrum to Galaxy for peak finding and links the annotated result back into NOMAD. It's an *example* Action, not the only kind possible — the underlying NOMAD Actions interface is generic, and `find_peaks` is here to demonstrate the NOMAD ↔ Galaxy round trip end to end, concretely. See [What this plugin does](../index.md#what-this-plugin-does) for the broader picture.

## Part 1: check the Galaxy round trip, no NOMAD required

This is the fastest way to see the whole NOMAD → Galaxy → NOMAD interaction actually work, and the part most relevant if you're on the Galaxy team and don't have a NOMAD deployment handy — everything here runs against real `usegalaxy.eu`, using the same code (`galaxy_client.py`) the real NOMAD Action uses, with no NOMAD or Temporal involved at all.

You'll need [`uv`](https://docs.astral.sh/uv/){:target="_blank" rel="noopener"} and a `usegalaxy.eu` API key (Galaxy GUI: User → Preferences → Manage API Key).

1. Clone this repo and `cd` into it.

2. Store your API key where the standalone scripts can find it, without it ending up anywhere it shouldn't (shell history, chat logs, etc.):

   ```bash
   echo "GALAXY_API_KEY=your-key-here" > .env
   ```

   `.env` is already gitignored.

3. Import the `XPS peak finding` workflow into your account. This only needs to be done once — it checks for an existing copy by name first, so re-running it is safe:

   ```bash
   uv run --env-file .env scripts/import_workflow.py
   ```

   This prints a `workflow_id`. Add it to `.env`:

   ```bash
   echo "GALAXY_WORKFLOW_ID=the-id-just-printed" >> .env
   ```

4. Run the round trip against the bundled example spectrum (`examples/galaxy_demo/example_data/vms_regular_ref.nxs`):

   ```bash
   uv run --env-file .env scripts/test_galaxy_roundtrip.py
   ```

   Expect output like:

   ```
   Uploading vms_regular_ref.nxs and invoking workflow ...
     history_id=... invocation_id=...
   Polling...
     [0s] state=running done=False
     ...
     [65s] state=ok done=True
   Outputs: {'output_nxs': '...', 'peaks_tabular': '...', 'peaks_json': '...'}
   Downloaded annotated NeXus to .../scripts/roundtrip_result.nxs
   ```

   You can pass a different spectrum file as an argument if you want to try your own:

   ```bash
   uv run --env-file .env scripts/test_galaxy_roundtrip.py path/to/your_spectrum.nxs
   ```

   Note: an occasional run may fail with a Galaxy compute-cluster error unrelated to this plugin (a transient container-cache issue, e.g. `image format not recognized` from Singularity/CVMFS) — this has been observed to be transient; retrying the same command usually succeeds.

5. Inspect the result. `roundtrip_result.nxs` is a copy of the input with peaks added under `/<entry>/fit` as an `NXfit` group (`h5py`, `pynx read`, or any NeXus browser works):

   ```bash
   uv run --with h5py python -c "
   import h5py
   with h5py.File('scripts/roundtrip_result.nxs') as f:
       entry = next(iter(f.keys()))
       fit = f[f'{entry}/fit']
       print(fit.attrs['NX_class'], list(fit.keys()))
   "
   ```

## Part 2: the full round trip through NOMAD

This part needs a running NOMAD deployment with this plugin installed (see [Install This Plugin](../how_to/install_this_plugin.md)) and an XPS entry already in it (for example, converted by `pynxtools-xps`).

Don't have an NXxps file handy? On the *Uploads* page, **Add example uploads** includes
**XPS Peak Finding via Galaxy** (a spectrum and an almost-ready trigger entry
bundled together) so you can skip straight to step 3 below.

1. Bring up Temporal and a CPU action worker (from the `nomad-distro-dev` repo root):

   ```bash
   docker compose up -d
   uv run poe cpuworker
   ```

2. In the NOMAD GUI, create a new **Find Peaks Trigger** entry in the same upload as your XPS spectrum (or a different, unpublished one — see the limitation in
   [Use This Plugin](../how_to/use_this_plugin.md)).

3. Reference the spectrum entry, enter your Galaxy API key, optionally set `prominence`/`distance`/`height`, and click **Find Peaks**.

4. Click **Get Action Status** (or wait — it's checked automatically right after triggering) until `workflow_status` reads `COMPLETED`. Behind the scenes this is running exactly the same steps as Part 1, plus reading the spectrum from NOMAD's upload storage first and writing the result back into NOMAD's upload storage last.

5. A new entry now exists in the upload: the original spectrum's data, with peaks added as an `NXfit`/`NXpeak` group, fully searchable like any other NOMAD quantity.
