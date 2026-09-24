# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Entry point for the find-peaks example upload."""

from nomad.config.models.plugins import ExampleUploadEntryPoint

find_peaks_example_upload = ExampleUploadEntryPoint(
    title="XPS Peak Finding via Galaxy (EOSC Demonstrator)",
    category="EOSC Demonstrators",
    description="""
        See a full NOMAD &harr; Galaxy round trip in action: an XPS spectrum stored in
        NOMAD is sent to [Galaxy](https://usegalaxy.eu) for peak finding, and the
        annotated result comes back as a new NOMAD entry.
    """,
    resources=["example_uploads/find_peaks/*"],
)
