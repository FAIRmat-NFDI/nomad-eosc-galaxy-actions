# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Search Zenodo and select records worth harvesting.

Framework-agnostic like `galaxy_client.py`: no NOMAD or Temporal imports, so
it can be tested alone and reused by a standalone script.
Zenodo REST API: https://developers.zenodo.org/
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from typing import Any

import requests
from pydantic import BaseModel
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DEFAULT_ZENODO_URL = "https://zenodo.org"
ARCHIVE_EXTENSIONS = (".zip",)
# Zenodo's search cannot page beyond 10,000 results.
MAX_SEARCH_RESULTS = 10_000


class ZenodoFile(BaseModel):
    key: str
    size: int
    md5: str | None = None


class ZenodoRecord(BaseModel):
    """The parts of a Zenodo record the harvest needs.

    `concept_record_id` is always set and groups all versions of a record.
    `concept_doi` can be missing, e.g. for records with an external DOI.
    """

    record_id: str
    concept_record_id: str
    doi: str | None = None
    concept_doi: str | None = None
    title: str = ""
    creators: list[str] = []
    description_html: str = ""
    publication_date: str | None = None
    license: str | None = None
    access_right: str | None = None
    files: list[ZenodoFile] = []


class Rejection(BaseModel):
    record_id: str
    reason: str


def parse_record(hit: dict[str, Any]) -> ZenodoRecord:
    """Convert one hit of the records API into a ZenodoRecord."""
    metadata = hit.get("metadata", {})
    files = []
    for file in hit.get("files", []):
        # Zenodo's checksum is the MD5 of the file content only ("md5:<hex>");
        # the file name or path is not part of it, so a renamed copy matches.
        checksum = file.get("checksum") or ""
        md5 = checksum.removeprefix("md5:") if checksum.startswith("md5:") else None
        files.append(ZenodoFile(key=file["key"], size=file["size"], md5=md5))
    return ZenodoRecord(
        record_id=str(hit["id"]),
        concept_record_id=str(hit["conceptrecid"]),
        doi=hit.get("doi") or metadata.get("doi"),
        concept_doi=hit.get("conceptdoi"),
        title=metadata.get("title", ""),
        creators=[c.get("name", "") for c in metadata.get("creators", [])],
        description_html=metadata.get("description", ""),
        publication_date=metadata.get("publication_date"),
        license=(metadata.get("license") or {}).get("id"),
        access_right=metadata.get("access_right"),
        files=files,
    )


def make_session(
    token: str | None = None, retries: int = 5, backoff_factor: float = 1.0
) -> requests.Session:
    """A session that retries rate-limited (429) and server errors (5xx).

    `Retry` waits as long as Zenodo's `Retry-After` header asks, otherwise
    backs off exponentially. https://urllib3.readthedocs.io/en/stable/reference/urllib3.util.html
    """
    retry = Retry(
        total=retries,
        backoff_factor=backoff_factor,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    if token:
        session.headers["Authorization"] = f"Bearer {token}"
    return session


def iter_search_hits(  # noqa: PLR0913
    session: requests.Session,
    query: str,
    *,
    zenodo_url: str = DEFAULT_ZENODO_URL,
    page_size: int = 25,
    request_delay: float = 0.0,
    timeout: float = 30.0,
) -> Iterator[dict[str, Any]]:
    """Yield raw hits of a Zenodo search, page by page.

    Follows the `links.next` URL of each page. `request_delay` (seconds)
    keeps the request rate below Zenodo's limit.

    Raises:
        requests.RequestException: a page still failed after the session's
            retries (RetryError) or returned another error status (HTTPError).
    """
    url: str | None = f"{zenodo_url.rstrip('/')}/api/records"
    params: dict[str, Any] | None = {"q": query, "size": page_size, "page": 1}
    seen = 0
    while url and seen < MAX_SEARCH_RESULTS:
        response = session.get(url, params=params, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        hits = data.get("hits", {}).get("hits", [])
        yield from hits
        seen += len(hits)
        url = data.get("links", {}).get("next") if hits else None
        params = None  # the next URL already carries all query parameters
        if url and request_delay:
            time.sleep(request_delay)


def has_extension(key: str, extensions: Iterable[str]) -> bool:
    """True if the file name ends with one of the extensions (any case)."""
    return key.lower().endswith(tuple(ext.lower() for ext in extensions))


def rejection_reason(
    record: ZenodoRecord,
    *,
    raw_extensions: Iterable[str],
    licenses: Iterable[str],
    archive_extensions: Iterable[str] = ARCHIVE_EXTENSIONS,
) -> str | None:
    """Why a record is not harvested, or None if it is a candidate.

    Archives count as candidates because their content is unknown until
    they are extracted (in Galaxy).
    """
    if record.access_right != "open" or not record.files:
        return "not_open_access"
    if record.license not in set(licenses):
        return "license_not_allowed"
    wanted = (*raw_extensions, *archive_extensions)
    if not any(has_extension(f.key, wanted) for f in record.files):
        return "no_raw_or_archive"
    return None


def collect_candidates(
    hits: Iterable[dict[str, Any]],
    *,
    raw_extensions: Iterable[str],
    licenses: Iterable[str],
) -> tuple[list[ZenodoRecord], list[Rejection]]:
    """Parse hits and split them into candidates and rejections.

    Reads all hits: whether a candidate still has work is only known from the
    state, so limiting here would return the same records in every run.
    A second hit of the same concept record is rejected as `duplicate`.
    """
    raw_extensions, licenses = tuple(raw_extensions), tuple(licenses)
    candidates: list[ZenodoRecord] = []
    rejections: list[Rejection] = []
    seen_concepts: set[str] = set()
    for hit in hits:
        record = parse_record(hit)
        if record.concept_record_id in seen_concepts:
            reason = "duplicate"
        else:
            reason = rejection_reason(
                record, raw_extensions=raw_extensions, licenses=licenses
            )
        seen_concepts.add(record.concept_record_id)
        if reason:
            rejections.append(Rejection(record_id=record.record_id, reason=reason))
        else:
            candidates.append(record)
    return candidates, rejections
