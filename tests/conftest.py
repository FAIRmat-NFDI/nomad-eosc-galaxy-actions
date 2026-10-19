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


class FakeGalaxy(ThreadingHTTPServer):
    """Local HTTP server answering the Galaxy API requests that bioblend sends
    for upload (tus), invoke, poll and download.

    The workflow behaves like the placeholder: its `manifest` output is the
    uploaded file, unless `output` is set. `job_states` lists the job state
    for each poll; the last one repeats.
    """

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _FakeGalaxyHandler)
        self.uploaded = b""
        self.output: bytes | None = None
        self.job_states = ["ok"]
        self.invocation_outputs = {"manifest": {"id": "out-1", "src": "hda"}}
        self.requests: list[tuple[str, str]] = []
        self.invocation_payload: dict = {}
        self.history_payload: dict = {}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _FakeGalaxyHandler(BaseHTTPRequestHandler):
    server: FakeGalaxy
    _TUS = "/api/upload/resumable_upload"

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_POST(self):
        path = self._path()
        body = self._body()
        if path == self._TUS:  # tus: create the upload
            self._send(201, {}, {"Location": f"{self._TUS}/session-1"})
        elif path == "/api/tools/fetch":
            self._send(200, {"outputs": [{"id": "in-1"}]})
        elif path == "/api/histories":
            self.server.history_payload = json.loads(body)
            self._send(200, {"id": "hist-1"})
        elif path.endswith("/invocations"):
            self.server.invocation_payload = json.loads(body)
            self._send(200, {"id": "inv-1"})
        else:
            self._send(404, {"err_msg": f"unknown {path}"})

    def do_PATCH(self):  # tus: upload the file content
        self._path()
        self.server.uploaded += self._body()
        self._send(204, None, {"Upload-Offset": str(len(self.server.uploaded))})

    def do_GET(self):
        path = self._path()
        server = self.server
        if path == "/api/version":
            self._send(200, {"version_major": "24.2", "version_minor": "0"})
        elif path == "/api/invocations/inv-1":
            steps = [{"job_id": None}, {"job_id": "job-1"}]
            outputs = server.invocation_outputs
            self._send(200, {"id": "inv-1", "steps": steps, "outputs": outputs})
        elif path == "/api/jobs/job-1":
            state = (
                server.job_states.pop(0)
                if len(server.job_states) > 1
                else server.job_states[0]
            )
            self._send(200, {"state": state, "outputs": {"out_file1": {"id": "out-1"}}})
        elif path == "/api/datasets/out-1":
            self._send(
                200,
                {
                    "state": "ok",
                    "name": "manifest",
                    "file_ext": "json",
                    "download_url": "/api/datasets/out-1/display",
                },
            )
        elif path == "/api/datasets/out-1/display":
            content = server.output if server.output is not None else server.uploaded
            self._send_bytes(200, content)
        else:
            self._send(404, {"err_msg": f"unknown {path}"})

    def _path(self) -> str:
        path = urlparse(self.path).path.rstrip("/")
        self.server.requests.append((self.command, path))
        return path

    def _send(self, status, body, headers=None):
        self._send_bytes(
            status, b"" if body is None else json.dumps(body).encode(), headers
        )

    def _send_bytes(self, status, payload: bytes, headers=None):
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
def fake_galaxy():
    server = FakeGalaxy()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
