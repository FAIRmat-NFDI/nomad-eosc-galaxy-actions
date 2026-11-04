# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import pytest

from nomad_eosc_galaxy_actions.manifest import Manifest, Results
from nomad_eosc_galaxy_actions.nomad_entries import (
    LOOKUP_CHUNK,
    LOOKUP_FIELDS,
    LOOKUP_PAGE_SIZE,
    FoundFile,
    RestLookup,
    entry_metadata,
    external_id,
    file_reference,
    found_files,
    parse_external_id,
    record_reference,
)

ZENODO = "https://zenodo.org"
SHA_A, SHA_B = "a" * 64, "b" * 64
MD5 = "0123456789abcdef0123456789abcdef"
CONCEPT_REF = "https://doi.org/10.5281/zenodo.100"
RECORD = {
    "record_id": "101",
    "concept_record_id": "100",
    "version_doi": "10.5281/zenodo.101",
    "concept_doi": "10.5281/zenodo.100",
}


# external_id and references


def test_external_id_round_trip():
    value = external_id("100", SHA_A)

    assert value == f"zenodo:100:sha256:{SHA_A}"
    assert parse_external_id(value) == ("100", SHA_A)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        f"zenodo-test:{SHA_A}",  # another tool's ID
        f"zenodo:100:sha256:{SHA_A[:-1]}",  # hash too short
        f"zenodo:100:sha256:{SHA_A.upper()}",
        f"zenodo:abc:sha256:{SHA_A}",
        f"zenodo:100:sha256:{SHA_A}:extra",
    ],
)
def test_other_external_ids_are_not_parsed(value):
    assert parse_external_id(value) is None


def test_record_reference_prefers_the_doi():
    assert record_reference(ZENODO, "100", "10.5281/zenodo.100") == CONCEPT_REF
    assert record_reference(ZENODO + "/", "100", None) == f"{ZENODO}/records/100"


def test_file_reference_encodes_the_key_and_adds_the_md5():
    assert file_reference(ZENODO, "101", "d/a b(1).sxm", MD5) == (
        f"{ZENODO}/records/101/files/d/a%20b%281%29.sxm#md5={MD5}"
    )
    assert file_reference(ZENODO, "101", "a.sxm") == f"{ZENODO}/records/101/files/a.sxm"


@pytest.mark.parametrize("key", ["a.sxm", "d/a b(1).sxm", "ü ñ.sxm", "a#b?.sxm"])
def test_file_reference_passes_nomads_url_check(key):
    """NOMAD drops all references of an entry if one is not a URL."""
    validators = pytest.importorskip("validators")

    assert validators.url(file_reference(ZENODO, "101", key, MD5))


# entry_metadata


def _file(path, sha256, **values):
    return {
        "concept_record_id": "100",
        "record_id": "101",
        "source": "archive",
        "archive_key": "data.zip",
        "path": path,
        "size": 1,
        "sha256": sha256,
        "role": "raw",
        "action": "process",
    } | values


def _outputs(files, results):
    manifest = Manifest(schema_version=1, run_id="r", tool_version="1", files=files)
    return manifest, Results(schema_version=1, run_id="r", files=results)


def _success(sha256, nexus_path):
    return {"sha256": sha256, "status": "success", "nexus_path": nexus_path}


def test_entry_metadata_for_loose_and_archive_files():
    manifest, results = _outputs(
        [
            _file("scan.sxm", SHA_A, source="loose", archive_key=None, md5=MD5),
            _file("d/x.sxm", SHA_B),
        ],
        [_success(SHA_A, "scan/scan.nxs"), _success(SHA_B, "x/x.nxs")],
    )

    entries = entry_metadata(RECORD, manifest, results, ZENODO)

    version_ref = "https://doi.org/10.5281/zenodo.101"
    assert entries == {
        "scan/scan.nxs": {
            "external_id": external_id("100", SHA_A),
            "references": [
                CONCEPT_REF,
                version_ref,
                f"{ZENODO}/records/101/files/scan.sxm#md5={MD5}",
            ],
        },
        "x/x.nxs": {
            "external_id": external_id("100", SHA_B),
            # an archive member points to its archive, without an MD5
            "references": [
                CONCEPT_REF,
                version_ref,
                f"{ZENODO}/records/101/files/data.zip",
            ],
        },
    }


def test_entry_metadata_without_dois_uses_zenodo_urls():
    manifest, results = _outputs([_file("x.sxm", SHA_A)], [_success(SHA_A, "x.nxs")])
    record = RECORD | {"version_doi": None, "concept_doi": None}

    entries = entry_metadata(record, manifest, results, ZENODO)

    assert entries["x.nxs"]["references"][:2] == [
        f"{ZENODO}/records/100",
        f"{ZENODO}/records/101",
    ]


def test_entry_metadata_only_for_converted_files_of_the_record():
    manifest, results = _outputs(
        [
            _file("failed.sxm", "1" * 64),
            _file("no_nexus.sxm", "2" * 64),
            _file("no_result.sxm", "3" * 64),
            _file("known.sxm", "4" * 64, action="already_known"),
            _file("other.sxm", "5" * 64, record_id="102"),
            _file("readme.txt", "6" * 64, role="context"),
        ],
        [
            {"sha256": "1" * 64, "status": "failed", "nexus_path": "f.nxs"},
            {"sha256": "2" * 64, "status": "success"},
            _success("4" * 64, "k.nxs"),
            _success("5" * 64, "o.nxs"),
            _success("6" * 64, "r.nxs"),
        ],
    )

    assert entry_metadata(RECORD, manifest, results, ZENODO) == {}


# found_files


def _entry(sha256=SHA_A, cid="100", upload_id="u1", published=False, refs=()):
    return {
        "entry_id": f"e-{upload_id}-{sha256[:2]}",
        "upload_id": upload_id,
        "external_id": external_id(cid, sha256),
        "references": [CONCEPT_REF, *refs],
        "published": published,
    }


def test_found_files_empty():
    assert found_files([]) == []


def test_found_files_ignore_entries_without_a_harvest_id():
    entries = [
        _entry() | {"external_id": None},
        _entry() | {"external_id": "zenodo-test:x"},
        {"entry_id": "e"},  # no fields at all
    ]

    assert found_files(entries) == []


def test_found_files_one_per_raw_file_published_wins():
    """NeXus files give a root and a child entry; another user may have published it."""
    md5_ref = f"{ZENODO}/records/101/files/a.sxm#md5={MD5}"
    entries = [
        _entry(upload_id="mine", refs=[md5_ref]),
        _entry(upload_id="mine"),
        _entry(upload_id="theirs", published=True),
        _entry(SHA_B, cid="200", upload_id="mine"),
    ]

    assert found_files(entries) == [
        FoundFile(
            concept_record_id="100",
            sha256=SHA_A,
            upload_id="theirs",
            published=True,
            md5=MD5,
        ),
        FoundFile(concept_record_id="200", sha256=SHA_B, upload_id="mine"),
    ]


# RestLookup (against the local fake NOMAD server)


def test_rest_lookup_without_references_sends_nothing(fake_nomad):
    assert list(RestLookup(fake_nomad.url, "pat-1").entries([])) == []
    assert fake_nomad.requests == []


def test_rest_lookup_searches_visible_entries_by_reference(fake_nomad):
    fake_nomad.entries = [
        _entry(upload_id="u1") | {"mainfile": "not requested"},
        _entry(SHA_B) | {"references": ["https://doi.org/other"]},
    ]

    found = list(
        RestLookup(fake_nomad.url, "pat-1").entries([CONCEPT_REF, CONCEPT_REF])
    )

    assert found == [_entry(upload_id="u1")]
    (body,) = fake_nomad.entry_queries
    assert body == {
        "owner": "visible",  # own entries and published ones
        "query": {"references:any": [CONCEPT_REF]},  # no repeats
        "pagination": {"page_size": LOOKUP_PAGE_SIZE},
        "required": {"include": LOOKUP_FIELDS},
    }
    assert fake_nomad.auth_headers == ["Bearer pat-1"]


def test_rest_lookup_follows_the_pages(fake_nomad):
    fake_nomad.entries = [
        _entry(f"{i:064x}", upload_id=f"u{i}") for i in range(LOOKUP_PAGE_SIZE + 1)
    ]

    found = list(RestLookup(fake_nomad.url, "pat-1").entries([CONCEPT_REF]))

    assert len(found) == LOOKUP_PAGE_SIZE + 1
    pages = [body["pagination"] for body in fake_nomad.entry_queries]
    assert pages == [
        {"page_size": LOOKUP_PAGE_SIZE},
        {"page_size": LOOKUP_PAGE_SIZE, "page_after_value": str(LOOKUP_PAGE_SIZE)},
    ]


def test_rest_lookup_splits_long_reference_lists(fake_nomad):
    references = [
        f"https://doi.org/10.5281/zenodo.{i}" for i in range(LOOKUP_CHUNK + 1)
    ]

    assert list(RestLookup(fake_nomad.url, "pat-1").entries(references)) == []

    sizes = [len(body["query"]["references:any"]) for body in fake_nomad.entry_queries]
    assert sizes == [LOOKUP_CHUNK, 1]
