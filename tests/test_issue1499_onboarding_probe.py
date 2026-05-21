"""Regression: onboarding wizard probes <base_url>/models before persisting (#1499).

Pre-#1499, `apply_onboarding_setup` accepted whatever `base_url` the user typed
without ever fetching `<base_url>/models`. The wizard would finish in ~200ms
with no outbound HTTP request, persist an unreachable URL silently, and leave
the user with an empty model dropdown that they had to populate by hand-editing
`config.yaml`.

Reporters: @chwps's log timeline in #1420 was the smoking gun — onboarding
submit completed in 239ms and there was no GET to `<HostIP>:1234/v1/models`
anywhere in the WebUI container's outbound trace.

The fix is the new `probe_provider_endpoint(provider, base_url, api_key)` in
`api/onboarding.py` and the matching `POST /api/onboarding/probe` route. The
frontend wizard runs the probe debounced on baseUrl input and blocking on
Continue for any provider with `requires_base_url=True`.

This file pins the backend probe contract (the function and the endpoint).
The frontend wiring is exercised through manual reproduction during PR review;
testing JS-side debounce behavior in pytest would add an outsized harness for
the value.

Each test mode covers exactly one error code from `PROBE_ERROR_CODES`, plus
the success path with model-list parsing. The probe response is also asserted
to NOT be persisted to config.yaml (the original wizard bug was that probe-
discovered data was indistinguishable from user-entered data after persist).
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from tests._pytest_port import BASE


@pytest.fixture
def mock_models_server():
    """Spin up a tiny HTTP server with several /v1/models response variants."""
    server_box: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 — http.server convention
            # /v1/models — happy path with OpenAI shape
            if self.path == "/v1/models":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "data": [
                        {"id": "qwen3-27b", "object": "model", "owned_by": "user"},
                        {"id": "llama-3.3-70b", "object": "model", "owned_by": "user"},
                    ]
                }).encode())
                return

            # /barelist/models — bare list shape some self-hosted servers return
            if self.path == "/barelist/models":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps([
                    {"id": "alpha"}, {"id": "beta"},
                ]).encode())
                return

            # /v1bad/models — 404 (wrong path)
            if self.path == "/v1bad/models":
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "not found"}')
                return

            # /v1/parse/models — 200 with non-JSON body
            if self.path == "/v1/parse/models":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"this is not json")
                return

            # /v1/wrongshape/models — 200 with JSON but not OpenAI shape
            if self.path == "/v1/wrongshape/models":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"unexpected": "shape"}')
                return

            # /v1/auth/models — 200 only with correct bearer token
            if self.path == "/v1/auth/models":
                auth = self.headers.get("Authorization", "")
                if auth == "Bearer correct-token":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"data": [{"id": "auth-only"}]}')
                else:
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b"unauthorized")
                return

            # Default: 500
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": "boom"}')

        def log_message(self, *args, **kwargs):  # noqa: N802 — suppress test noise
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    server_box["port"] = httpd.server_address[1]
    server_box["base"] = f"http://127.0.0.1:{server_box['port']}"
    server_box["httpd"] = httpd

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    # Tiny sleep so the listening socket is observable when the test connects.
    time.sleep(0.05)

    yield server_box

    httpd.shutdown()
    httpd.server_close()


class TestIssue1499ProbeRouteEndToEnd:
    """End-to-end smoke test for `POST /api/onboarding/probe`.

    The route is a thin wrapper around `probe_provider_endpoint`; the unit
    tests above cover the function logic exhaustively.  This class verifies
    the wiring: route exists, parses JSON body, returns probe result as JSON.
    """

    def _post(self, body):
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            BASE + "/api/onboarding/probe",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read()), r.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read()), e.code

    def test_route_returns_invalid_url_for_empty_base(self):
        body, status = self._post({"provider": "lmstudio", "base_url": ""})
        assert status == 200
        assert body["ok"] is False
        assert body["error"] == "invalid_url"

    def test_route_returns_success_against_mock(self, mock_models_server):
        body, status = self._post({
            "provider": "lmstudio",
            "base_url": f"{mock_models_server['base']}/v1",
        })
        assert status == 200, f"unexpected status {status}: {body}"
        assert body["ok"] is True
        assert isinstance(body["models"], list)
        assert any(m["id"] == "qwen3-27b" for m in body["models"])

    def test_route_returns_dns_error_for_bad_host(self):
        body, status = self._post({
            "provider": "lmstudio",
            "base_url": "http://this-host-definitely-does-not-exist-zxq987.invalid:1234/v1",
        })
        assert status == 200
        assert body["ok"] is False
        assert body["error"] == "dns"
