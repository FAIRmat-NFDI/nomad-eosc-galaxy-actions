from nomad.config.models.plugins import SchemaPackageEntryPoint


class FindPeaksSchemaEntryPoint(SchemaPackageEntryPoint):
    def load(self):
        from nomad_eosc_galaxy_actions.schema_packages.schema_package import m_package

        return m_package


schema_package_entry_point = FindPeaksSchemaEntryPoint(
    name='FindPeaksSchema',
    description='Trigger entry that runs the find-peaks Action on a referenced XPS spectrum.',
)
