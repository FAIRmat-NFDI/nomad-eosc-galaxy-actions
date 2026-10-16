# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ZENODO_DATA = Path(__file__).parent / "data" / "zenodo"


def load_zenodo_page(page: int) -> dict:
    """A real Zenodo search response (2026-10-16), recorded with page size 3."""
    return json.loads((ZENODO_DATA / f"search_page{page}.json").read_text())


@pytest.fixture
def zenodo_hits() -> list[dict]:
    """All 6 recorded hits, in search order; a fresh copy per test."""
    return copy.deepcopy(
        load_zenodo_page(1)["hits"]["hits"] + load_zenodo_page(2)["hits"]["hits"]
    )


class FakeZenodo(ThreadingHTTPServer):
    """Local HTTP server serving recorded Zenodo search pages.

    `pages` maps page number to response body; `failures` lists HTTP status
    codes to answer with (one per request) before serving normally.
    """

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _FakeZenodoHandler)
        self.pages = {1: load_zenodo_page(1), 2: load_zenodo_page(2)}
        self.failures: list[int] = []
        self.requests: list[str] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _FakeZenodoHandler(BaseHTTPRequestHandler):
    server: FakeZenodo

    def do_GET(self):
        self.server.requests.append(self.path)
        if self.server.failures:
            self._send(self.server.failures.pop(0), {}, {"Retry-After": "0"})
            return
        page = int(parse_qs(urlparse(self.path).query).get("page", ["1"])[0])
        body = json.loads(json.dumps(self.server.pages.get(page, {"hits": {}})))
        # Recorded "next" links point to zenodo.org; send them back to this server.
        links = body.get("links", {})
        if "next" in links:
            links["next"] = links["next"].replace("https://zenodo.org", self.server.url)
        self._send(200, body)

    def _send(self, status, body, headers=None):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture
def fake_zenodo():
    server = FakeZenodo()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
