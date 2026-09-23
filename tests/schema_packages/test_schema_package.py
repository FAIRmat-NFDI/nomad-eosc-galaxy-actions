# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import os.path

from nomad.client import normalize_all, parse


def test_schema_package():
    test_file = os.path.join("tests", "data", "test.archive.yaml")
    entry_archive = parse(test_file)[0]
    normalize_all(entry_archive)

    assert entry_archive.data.message == "Hello Markus!"
