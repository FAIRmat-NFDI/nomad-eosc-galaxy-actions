# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import copy

import pytest
import requests

from nomad_eosc_galaxy_actions import zenodo_client

RAW = (".sxm",)
LICENSES = ("cc-zero", "cc-by-4.0")
TOTAL_HITS = 6


@pytest.fixture
def recorded_hit(zenodo_hits):
    """Return a copy of the recorded hit with the given record id."""

    def _find(record_id: int) -> dict:
        return copy.deepcopy(next(h for h in zenodo_hits if h["id"] == record_id))

    return _find


def _session(retries: int = 2) -> requests.Session:
    return zenodo_client.make_session(retries=retries, backoff_factor=0)


# parse_record


def test_parse_record_without_concept_doi(recorded_hit):
    record = zenodo_client.parse_record(
        recorded_hit(10843653)
    )  # Dryad DOI, no conceptdoi

    assert record.concept_doi is None
    assert record.concept_record_id == "10843652"
    assert record.doi == "10.5061/dryad.1g1jwsv49"
    assert record.license == "cc-zero"
    assert record.files[0].md5 == "25557eaca33babeff87227cd745d4f7d"


def test_parse_record_with_concept_doi(recorded_hit):
    record = zenodo_client.parse_record(recorded_hit(10443995))

    assert record.concept_doi == "10.5281/zenodo.10443994"
    assert record.creators


def test_parse_record_non_md5_checksum_and_no_files(recorded_hit):
    hit = recorded_hit(22806339)
    hit["files"][0]["checksum"] = "sha256:abc"

    assert zenodo_client.parse_record(hit).files[0].md5 is None
    hit["files"] = []
    assert zenodo_client.parse_record(hit).files == []


# rejection_reason


def _reason(hit: dict) -> str | None:
    record = zenodo_client.parse_record(hit)
    return zenodo_client.rejection_reason(record, raw_extensions=RAW, licenses=LICENSES)


def test_archive_and_loose_raw_file_are_candidates(recorded_hit):
    assert _reason(recorded_hit(22402765)) is None  # one zip
    assert _reason(recorded_hit(22806339)) is None  # loose topo016.sxm


def test_extension_match_ignores_case(recorded_hit):
    hit = recorded_hit(22806339)
    hit["files"] = [{"key": "TOPO.SXM", "size": 1, "checksum": "md5:x"}]
    assert _reason(hit) is None


def test_rejection_reasons(recorded_hit):
    restricted = recorded_hit(22402765)
    restricted["metadata"]["access_right"] = "restricted"
    no_files = recorded_hit(22402765)
    no_files["files"] = []
    wrong_license = recorded_hit(22402765)
    wrong_license["metadata"]["license"] = {"id": "cc-by-nc-4.0"}
    no_license = recorded_hit(22402765)
    del no_license["metadata"]["license"]
    no_raw = recorded_hit(22806339)
    no_raw["files"] = [f for f in no_raw["files"] if not f["key"].endswith(".sxm")]

    assert _reason(restricted) == "not_open_access"
    assert _reason(no_files) == "not_open_access"
    assert _reason(wrong_license) == "license_not_allowed"
    assert _reason(no_license) == "license_not_allowed"
    assert _reason(no_raw) == "no_raw_or_archive"


# collect_candidates


def test_collect_candidates_empty():
    assert zenodo_client.collect_candidates(
        [], raw_extensions=RAW, licenses=LICENSES
    ) == ([], [])


def test_collect_candidates_marks_duplicate_concepts(recorded_hit):
    hit = recorded_hit(22402765)
    candidates, rejections = zenodo_client.collect_candidates(
        [hit, copy.deepcopy(hit)], raw_extensions=RAW, licenses=LICENSES
    )

    assert [c.record_id for c in candidates] == ["22402765"]
    assert [(r.record_id, r.reason) for r in rejections] == [("22402765", "duplicate")]


def test_collect_candidates_reads_all_hits(zenodo_hits):
    hits = iter(zenodo_hits)
    candidates, _ = zenodo_client.collect_candidates(
        hits, raw_extensions=RAW, licenses=LICENSES
    )

    assert len(candidates) == TOTAL_HITS
    assert next(hits, None) is None  # no limit: every hit is read


# iter_search_hits (against a local fake Zenodo server)


def test_search_follows_next_links(fake_zenodo, zenodo_hits):
    hits = list(
        zenodo_client.iter_search_hits(
            _session(), "STM", zenodo_url=fake_zenodo.url, page_size=3
        )
    )

    assert [h["id"] for h in hits] == [h["id"] for h in zenodo_hits]
    assert len(fake_zenodo.requests) == len(fake_zenodo.pages)
    assert "q=STM" in fake_zenodo.requests[0]


def test_search_stops_on_empty_page(fake_zenodo):
    fake_zenodo.pages = {1: {"hits": {"hits": []}, "links": {"next": "ignored"}}}

    assert (
        list(
            zenodo_client.iter_search_hits(
                _session(), "STM", zenodo_url=fake_zenodo.url
            )
        )
        == []
    )
    assert len(fake_zenodo.requests) == 1


def test_search_retries_after_rate_limit(fake_zenodo):
    fake_zenodo.failures = [429]

    hits = list(
        zenodo_client.iter_search_hits(_session(), "STM", zenodo_url=fake_zenodo.url)
    )

    assert len(hits) == TOTAL_HITS
    assert len(fake_zenodo.requests) == len(fake_zenodo.pages) + 1


def test_search_raises_after_retries_exhausted(fake_zenodo):
    fake_zenodo.failures = [500, 500, 500]

    with pytest.raises(requests.RequestException):
        list(
            zenodo_client.iter_search_hits(
                _session(retries=2), "STM", zenodo_url=fake_zenodo.url
            )
        )


def test_search_raises_on_client_error(fake_zenodo):
    fake_zenodo.failures = [400]  # not retried: a bad query stays bad

    with pytest.raises(requests.HTTPError):
        list(
            zenodo_client.iter_search_hits(
                _session(), "STM", zenodo_url=fake_zenodo.url
            )
        )


def test_make_session_sets_token_header():
    assert _session().headers.get("Authorization") is None
    session = zenodo_client.make_session("secret")
    assert session.headers["Authorization"] == "Bearer secret"
