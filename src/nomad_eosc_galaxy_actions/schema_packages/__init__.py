# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Schema package entry points."""

from nomad.config.models.plugins import SchemaPackageEntryPoint


class FindPeaksSchemaEntryPoint(SchemaPackageEntryPoint):
    def load(self):
        from nomad_eosc_galaxy_actions.schema_packages.schema_package import (  # noqa: PLC0415
            m_package,
        )

        return m_package


schema_package_entry_point = FindPeaksSchemaEntryPoint(
    name="FindPeaksSchema",
    description=(
        "Trigger entry that runs the find-peaks Action on a referenced XPS spectrum."
    ),
)
