"""Small helpers for behavior-level route tests."""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from urllib.parse import urlparse


class CapturingRouteHandler:
    def __init__(
        self,
        *,
        body: dict | bytes | None = None,
        headers: dict[str, str] | None = None,
        client_address: tuple[str, int] = ("127.0.0.1", 50000),
    ) -> None:
        if isinstance(body, bytes):
            raw = body
        elif body is None:
            raw = b""
        else:
            raw = json.dumps(body).encode("utf-8")
        self.headers = {"Content-Length": str(len(raw))}
        if headers:
            self.headers.update(headers)
        self.client_address = client_address
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status: int | None = None
        self.headers_sent: list[tuple[str, str]] = []

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: object) -> None:
        self.headers_sent.append((name, str(value)))

    def end_headers(self) -> None:
        return None

    def json_body(self):
        payload = self.wfile.getvalue()
        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))


@dataclass
class RouteResponse:
    handled: bool
    status: int | None
    body: object
    raw: bytes
    handler: CapturingRouteHandler


def invoke_route(
    method: str,
    path: str,
    *,
    body: dict | bytes | None = None,
    headers: dict[str, str] | None = None,
    client_address: tuple[str, int] = ("127.0.0.1", 50000),
) -> RouteResponse:
    import api.routes as routes

    handler = CapturingRouteHandler(
        body=body,
        headers=headers,
        client_address=client_address,
    )
    route = getattr(routes, f"handle_{method.lower()}")
    handled = route(handler, urlparse(path))
    return RouteResponse(
        handled=handled,
        status=handler.status,
        body=handler.json_body(),
        raw=handler.wfile.getvalue(),
        handler=handler,
    )
