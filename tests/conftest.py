# SPDX-FileCopyrightText: The nomad-eosc-galaxy-actions Authors
#
# This file is part of nomad-eosc-galaxy-actions.
#
# SPDX-License-Identifier: Apache-2.0
import copy
import json
import socketserver
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
        self.auth_headers: list[str | None] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class _FakeZenodoHandler(BaseHTTPRequestHandler):
    server: FakeZenodo

    def do_GET(self):
        self.server.requests.append(self.path)
        self.server.auth_headers.append(self.headers.get("Authorization"))
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
    uploaded file, unless `output` is set. Add a `results` entry to
    `invocation_outputs` to serve `results`. `collections` maps collection IDs
    to {element identifier: content}, served as datasets `<collection>-<identifier>`;
    label them in `invocation_collections`. `job_states` lists the job state
    for each poll; the last one repeats.
    """

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _FakeGalaxyHandler)
        self.uploaded = b""
        self.output: bytes | None = None
        self.results = b""
        self.collections: dict[str, dict[str, bytes]] = {}
        self.invocation_collections: dict[str, dict] = {}
        self.job_states = ["ok"]
        self.invocation_outputs = {"manifest": {"id": "out-1", "src": "hda"}}
        self.requests: list[tuple[str, str]] = []
        self.api_keys: set[str | None] = set()  # x-api-key headers received
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
            self._send(
                200,
                {
                    "id": "inv-1",
                    "steps": steps,
                    "outputs": server.invocation_outputs,
                    "output_collections": server.invocation_collections,
                },
            )
        elif path.startswith("/api/dataset_collections/"):
            collection_id = path.rsplit("/", 1)[1]
            elements = [
                {
                    "element_identifier": name,
                    "object": {"id": f"{collection_id}-{name}"},
                }
                for name in server.collections.get(collection_id, {})
            ]
            self._send(200, {"id": collection_id, "elements": elements})
        elif path == "/api/jobs/job-1":
            state = (
                server.job_states.pop(0)
                if len(server.job_states) > 1
                else server.job_states[0]
            )
            self._send(200, {"state": state, "outputs": {"out_file1": {"id": "out-1"}}})
        elif path in ("/api/datasets/out-1", "/api/datasets/out-2"):
            self._send(
                200,
                {
                    "state": "ok",
                    "file_ext": "json",
                    "download_url": f"{path}/display",
                },
            )
        elif path == "/api/datasets/out-1/display":
            content = server.output if server.output is not None else server.uploaded
            self._send_bytes(200, content)
        elif path == "/api/datasets/out-2/display":
            self._send_bytes(200, server.results)
        elif path.startswith("/api/datasets/") and self._element(path):
            if path.endswith("/display"):
                self._send_bytes(200, self._element(path))
            else:
                self._send(200, {"state": "ok", "download_url": f"{path}/display"})
        else:
            self._send(404, {"err_msg": f"unknown {path}"})

    def _element(self, path: str) -> bytes | None:
        """Content of a collection element dataset `<collection>-<identifier>`."""
        dataset_id = path.removeprefix("/api/datasets/").removesuffix("/display")
        for collection_id, elements in self.server.collections.items():
            for name, content in elements.items():
                if dataset_id == f"{collection_id}-{name}":
                    return content
        return None

    def _path(self) -> str:
        path = urlparse(self.path).path.rstrip("/")
        self.server.requests.append((self.command, path))
        self.server.api_keys.add(self.headers.get("x-api-key"))
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


class FakeNomad(ThreadingHTTPServer):
    """Local HTTP server answering NOMAD's `GET` and `POST /uploads` (API v1).

    `projects` maps upload IDs to {"upload_name", "published", "content"};
    `post_status` answers every POST with that HTTP status instead.
    """

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _FakeNomadHandler)
        self.projects: dict[str, dict] = {}
        self.post_status: int | None = None
        self.requests: list[tuple[str, str, dict]] = []  # method, path, query
        self.auth_headers: list[str | None] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/api/v1"


class _FakeNomadHandler(BaseHTTPRequestHandler):
    server: FakeNomad

    def _request(self) -> dict:
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.server.requests.append((self.command, parsed.path, query))
        self.server.auth_headers.append(self.headers.get("Authorization"))
        return query

    def do_GET(self):
        query = self._request()
        unpublished = query.get("is_published") == "false"
        data = [
            {"upload_id": upload_id, "upload_name": p["upload_name"]}
            for upload_id, p in self.server.projects.items()
            if not (unpublished and p["published"])
        ]
        self._send(200, {"data": data})

    def do_POST(self):
        query = self._request()
        content = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.server.post_status:
            self._send(self.server.post_status, {"detail": "refused"})
            return
        upload_id = f"up-{len(self.server.projects) + 1}"
        self.server.projects[upload_id] = {
            "upload_name": query.get("upload_name"),
            "published": False,
            "content": content,
        }
        self._send(200, {"upload_id": upload_id, "data": {}})

    def _send(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture
def fake_nomad():
    server = FakeNomad()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


class FakeSmtp(socketserver.ThreadingTCPServer):
    """Local SMTP server (RFC 5321 basics, no TLS) keeping what it receives.

    `messages` holds (sender, recipients, raw message) per accepted mail.
    """

    daemon_threads = True

    def __init__(self):
        super().__init__(("127.0.0.1", 0), _FakeSmtpHandler)
        self.messages: list[tuple[str, list[str], bytes]] = []

    @property
    def port(self) -> int:
        return self.server_address[1]


class _FakeSmtpHandler(socketserver.StreamRequestHandler):
    server: FakeSmtp

    def _reply(self, line: str) -> None:
        self.wfile.write(f"{line}\r\n".encode())

    def handle(self):
        sender, recipients = "", []
        self._reply("220 fake SMTP")
        while line := self.rfile.readline():
            command = line.decode().strip()
            verb = command[:4].upper()
            if verb == "EHLO":
                self._reply("250-fake")
                self._reply("250 HELP")  # no STARTTLS, no AUTH
            elif verb == "MAIL":
                sender, recipients = command.split(":", 1)[1].strip("<> "), []
                self._reply("250 OK")
            elif verb == "RCPT":
                recipients.append(command.split(":", 1)[1].strip("<> "))
                self._reply("250 OK")
            elif verb == "DATA":
                self._reply("354 end with .")
                data = b""
                while (chunk := self.rfile.readline()) not in (b".\r\n", b""):
                    data += chunk
                self.server.messages.append((sender, recipients, data))
                self._reply("250 OK")
            elif verb == "QUIT":
                self._reply("221 bye")
                return
            else:
                self._reply("250 OK")


@pytest.fixture
def fake_smtp():
    server = FakeSmtp()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
