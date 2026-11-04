# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
"""Zenodo metadata on NOMAD entries, and the lookup of entries already in NOMAD.

Each converted NeXus entry gets, in the project's `nomad.json`:

- `external_id`: `zenodo:<concept record ID>:sha256:<raw file SHA-256>`;
- `references`: the concept DOI, the version DOI and the Zenodo file URL.
  A loose raw file's URL ends with `#md5=<Zenodo MD5>`.

NOMAD indexes both fields, so one search by concept DOI tells which raw files
of the Zenodo search results are already in NOMAD, published or not
(https://docs.nomad-lab.eu/reference/glossary.html).

Framework-agnostic: no NOMAD or Temporal imports.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Any, Protocol
from urllib.parse import quote

import requests
from pydantic import BaseModel

from nomad_eosc_galaxy_actions.manifest import Manifest, Results

EXTERNAL_ID = re.compile(r"zenodo:(?P<cid>\d+):sha256:(?P<sha256>[0-9a-f]{64})")
MD5_FRAGMENT = re.compile(r"#md5=(?P<md5>[0-9a-f]{32})$")
# Entry fields the lookup needs; NOMAD sends only these.
LOOKUP_FIELDS = ["entry_id", "upload_id", "external_id", "references", "published"]
# Concept references per search request, and entries per result page.
LOOKUP_CHUNK = 500
LOOKUP_PAGE_SIZE = 1000


def external_id(concept_record_id: str, sha256: str) -> str:
    return f"zenodo:{concept_record_id}:sha256:{sha256}"


def parse_external_id(value: str | None) -> tuple[str, str] | None:
    """(concept record ID, SHA-256) of a harvest `external_id`, else None."""
    match = EXTERNAL_ID.fullmatch(value or "")
    return (match["cid"], match["sha256"]) if match else None


def record_reference(zenodo_url: str, record_id: str, doi: str | None) -> str:
    """The DOI URL of a record (concept or version); its Zenodo URL without DOI."""
    if doi:
        return f"https://doi.org/{doi}"
    return f"{zenodo_url.rstrip('/')}/records/{record_id}"


def file_reference(
    zenodo_url: str, record_id: str, key: str, md5: str | None = None
) -> str:
    """The Zenodo URL of a file; the key is encoded, as NOMAD accepts only URLs."""
    url = f"{zenodo_url.rstrip('/')}/records/{record_id}/files/{quote(key)}"
    return f"{url}#md5={md5}" if md5 else url


def entry_metadata(
    record: dict[str, Any], manifest: Manifest, results: Results, zenodo_url: str
) -> dict[str, dict[str, Any]]:
    """`nomad.json` `entries` for the record's converted raw files, by NeXus path."""
    by_sha256 = {result.sha256: result for result in results.files}
    cid = record["concept_record_id"]
    record_id = record["record_id"]
    record_refs = [
        record_reference(zenodo_url, cid, record.get("concept_doi")),
        record_reference(zenodo_url, record_id, record.get("version_doi")),
    ]
    entries = {}
    for file in manifest.files:
        result = by_sha256.get(file.sha256)
        if (
            (file.record_id, file.role, file.action) != (record_id, "raw", "process")
            or result is None
            or result.status != "success"
            or not result.nexus_path
        ):
            continue
        if file.source == "loose":
            source = file_reference(zenodo_url, record_id, file.path, file.md5)
        else:  # the archive is the file on Zenodo
            source = file_reference(zenodo_url, record_id, file.archive_key or "")
        entries[result.nexus_path] = {
            "external_id": external_id(cid, file.sha256),
            "references": list(dict.fromkeys([*record_refs, source])),
        }
    return entries


class FoundFile(BaseModel):
    """A raw file whose NeXus entry is in NOMAD."""

    concept_record_id: str
    sha256: str
    upload_id: str | None = None
    published: bool = False
    md5: str | None = None  # Zenodo MD5, only for a loose raw file


def found_files(entries: Iterable[dict[str, Any]]) -> list[FoundFile]:
    """One `FoundFile` per raw file; entries without a harvest `external_id` are
    ignored. A file in several entries counts as published if any entry is."""
    found: dict[tuple[str, str], FoundFile] = {}
    for entry in entries:
        parsed = parse_external_id(entry.get("external_id"))
        if parsed is None:
            continue
        md5 = next(
            (
                match["md5"]
                for ref in entry.get("references") or []
                if (match := MD5_FRAGMENT.search(ref))
            ),
            None,
        )
        file = found.setdefault(
            parsed,
            FoundFile(
                concept_record_id=parsed[0],
                sha256=parsed[1],
                upload_id=entry.get("upload_id"),
            ),
        )
        if entry.get("published") and not file.published:
            file.published = True
            file.upload_id = entry.get("upload_id")
        file.md5 = file.md5 or md5
    return sorted(found.values(), key=lambda f: (f.concept_record_id, f.sha256))


class EntryLookup(Protocol):
    """Where entries are searched: a NOMAD over its API, or the local one."""

    def entries(self, references: list[str]) -> Iterator[dict[str, Any]]:
        """Entries the user can see (own or published) with any of `references`."""


def chunks(values: list[str], size: int = LOOKUP_CHUNK) -> Iterator[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


class RestLookup:
    """Entry search of the PAT's user in a NOMAD, through `POST /entries/query`."""

    def __init__(self, nomad_url: str, pat: str, timeout: float = 300.0):
        self._url = nomad_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers["Authorization"] = f"Bearer {pat}"

    def entries(self, references: list[str]) -> Iterator[dict[str, Any]]:
        for chunk in chunks(sorted(set(references))):
            after = None
            while True:
                pagination: dict[str, Any] = {"page_size": LOOKUP_PAGE_SIZE}
                if after:
                    pagination["page_after_value"] = after
                response = self._session.post(
                    f"{self._url}/entries/query",
                    json={
                        "owner": "visible",
                        "query": {"references:any": chunk},
                        "pagination": pagination,
                        "required": {"include": LOOKUP_FIELDS},
                    },
                    timeout=self._timeout,
                )
                response.raise_for_status()
                body = response.json()
                yield from body.get("data", [])
                after = body.get("pagination", {}).get("next_page_after_value")
                if not after:
                    break
