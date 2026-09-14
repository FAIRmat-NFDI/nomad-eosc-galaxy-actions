# Basic standalone demo script for NOMAD-Galaxy interaction

Standalone demo of the **NOMAD ↔ Galaxy XPS peak-finding workflow** using `pynxtools-xps`.

The script:

1. Reads the example XPS spectrum from `example_data/vms_regular_ref.nxs`.
2. Runs the XPS peak-finding algorithm.
3. Writes the detected peaks to `example_data/vms_regular_with_peaks.nxs` as an `NXfit` group containing `NXpeak` entries.
4. Verifies the resulting NeXus structure.

## Run

Requires [`uv`](https://docs.astral.sh/uv/).

```bash
uv run run_peak_finding_demo.py
```

The script uses an isolated, cached environment defined by its inline dependency block. It does not install anything into the system Python environment.

The `pynxtools-xps` dependency currently points to the `eosc-galaxy-peak-finding` branch. Once the changes are merged and released, it can be replaced with the corresponding PyPI version.

## Dependencies

* `scipy`
* `pynxtools-xps`
* `h5py` (provided through the `pynxtools-xps` environment)

No NOMAD or Galaxy installation is required. The demo represents the environment and processing step that can subsequently be wrapped by Galaxy.
