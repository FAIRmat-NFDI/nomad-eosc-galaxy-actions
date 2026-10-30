<!--
SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors

SPDX-License-Identifier: Apache-2.0
-->

# Development files

Files used only while developing; they are not part of the installed package.
Remove each one once the feature it stands in for is ready.

- `zenodo_harvest/Galaxy-Workflow-Zenodo_harvest_placeholder.ga`: Galaxy
  workflow that returns the work list unchanged as `manifest`, to test the
  Galaxy round trip of the `zenodo_harvest` Action. Replace it with the real
  download and conversion workflow.
