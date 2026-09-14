# /// script
# dependencies = [
#     "scipy",
#     "pynxtools-xps @ git+https://github.com/FAIRmat-NFDI/pynxtools-xps@eosc-galaxy-peak-finding",
# ]
# ///
"""
Standalone demo of the NOMAD<->Galaxy XPS peak-finding step.

Reads the bundled example XPS spectrum (example_data/vms_regular_ref.nxs -- a
already-converted NeXus/NXxps file that we use elsewhere as a pynxtools-xps test
fixture), finds peaks in it, and writes the result back into a copy of the
file as an NXfit group.

Run with:
    uv run run_peak_finding_demo.py

`uv run` reads the dependency block above and executes this script in an
isolated, cached environment.
The pynxtools-xps dependency points at the feature branch until it merges
into main and gets released, at which point it becomes "pynxtools-xps>=X.Y".

No NOMAD or Galaxy dependency -- this can be wrapped into a Galaxy tool by itself.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUT_FILE = HERE / "example_data" / "vms_regular_ref.nxs"
OUTPUT_FILE = HERE / "example_data" / "vms_regular_with_peaks.nxs"


def main() -> None:
    import logging

    import h5py

    from pynxtools_xps.peak_finding import find_xps_peaks

    # Quiets pynxtools's per-attribute "prevented overwriting" log lines --
    # expected noise from writing in append mode, not relevant to this demo.
    logging.getLogger('pynxtools').setLevel(logging.WARNING)

    peaks = find_xps_peaks(INPUT_FILE, OUTPUT_FILE, prominence=10000, distance=20)

    print(f"Found {len(peaks)} peaks. Wrote result to {OUTPUT_FILE.name}:")
    for i, peak in enumerate(peaks):
        print(f"  peak_{i}: position={peak['position']:.1f} eV, intensity={peak['intensity']:.0f}")

    with h5py.File(OUTPUT_FILE, "r") as f:
        entry_name = next(iter(f.keys()))
        fit_group = f[f"{entry_name}/fit"]
        assert fit_group.attrs.get("NX_class") == "NXfit"
        peak_groups = [
            child
            for child in fit_group.values()
            if child.attrs.get("NX_class") == "NXpeak"
        ]
        assert len(peak_groups) == len(peaks)
    print(f"Confirmed: /{entry_name}/fit is a valid NXfit group with {len(peaks)} NXpeak children.")


if __name__ == "__main__":
    main()
