# XPS Peak Finding via Galaxy

A live demonstration of a NOMAD Action: an XPS spectrum stored in NOMAD is sent to [Galaxy](https://usegalaxy.eu) for peak finding, and the annotated result comes back as a new NOMAD entry, automatically.

```
xps_spectrum.nxs  -- "Find Peaks" action-->  Galaxy peak-finding workflow
   (this upload)                                     |
                                                     v
                          new entry: same spectrum + detected peaks
```

## What's in this upload

- **`xps_spectrum.nxs`**: an example XPS spectrum, already converted to the   [`NXxps`](https://manual.nexusformat.org/classes/applications/NXxps.html#nxxps) NeXus application definition. Open it and select the **FILES** tab, then the file itself, to view it with the built-in H5Web viewer.
- **`nomad.json`**: upload-level metadata (a short description and links back to this plugin) that NOMAD applies automatically when this upload is created — not something you need to open or edit.

## Try it: four steps to a finished round trip

1. **Get the spectrum's `entry_id`.** Open the `xps_spectrum.nxs` entry (click it in  the entries list above) and copy `entry_id` from its overview page.
2. **Open Actions.** In the main menu, go to **Actions**, and start a new run of **find_peaks_action** (or use the **Run action** button on this upload itself).
3. **Fill in the form.** Paste the spectrum's `entry_id` into `spectrum_entry_id`. Add a Galaxy API key — create a free account at [usegalaxy.eu](https://usegalaxy.eu) if you don't have one, then go to User &rarr; Preferences &rarr; Manage API Key — or leave it empty if your deployment already has a shared key configured. (Optional: tune `prominence`/`distance`/`height`; the defaults work fine for this example spectrum.) Run it.
4. **Watch it finish, then find the result.** The Actions view shows the run's status moving to `COMPLETED`. A new entry then appears in this upload: the same spectrum, now with an `NXfit` group holding one `NXpeak` per detected peak.

## Where to go from here

- **See the round trip without touching NOMAD at all**: the plugin's [tutorial](https://fairmat-nfdi.github.io/nomad-eosc-galaxy-actions/tutorial/tutorial.html) runs the exact same Galaxy call as a standalone script against `usegalaxy.eu`.
- **Understand what's happening underneath**: the [explanation](https://fairmat-nfdi.github.io/nomad-eosc-galaxy-actions/explanation/explanation.html)  page covers NOMAD Actions, the Galaxy concepts involved, and the exact contract between this plugin and the Galaxy tool it calls.
- **Build your own Action on this pattern**: `find_peaks` is the first Action that implements this NOMAD <-> Galaxy interaction. The same read-from-NOMAD / run-on-Galaxy / write-back-to-NOMAD idea can be built for a different Galaxy workflow entirely. Explore the associated plugin `nomad-eosc-galaxy-actions`(https://github.com/FAIRmat-NFDI/nomad-eosc-galaxy-actions) to get started.
