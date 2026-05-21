"""Regression coverage for issue #1800 file-picker and HTML-open interactions."""

from __future__ import annotations

import re
from pathlib import Path

from tests.route_source import function_source


REPO = Path(__file__).resolve().parents[1]
def _slice_after(source: str, needle: str, chars: int = 900) -> str:
    idx = source.find(needle)
    assert idx >= 0, f"{needle!r} not found"
    return source[idx : idx + chars]


def test_media_html_inline_keeps_csp_sandbox():
    """api/media may serve HTML inline only behind a CSP sandbox."""
    body = function_source("_handle_media")
    assert 'html_inline_ok = inline_preview and mime == "text/html"' in body
    assert 'csp = "sandbox allow-scripts" if html_inline_ok else None' in body
    assert "csp=csp" in body
    assert "allow-same-origin" not in body


def test_sandboxed_file_responses_do_not_send_x_frame_options():
    """X-Frame-Options: DENY would block the sandbox iframe preview."""
    body = function_source("_serve_file_bytes")
    csp_branch = body[body.find("if csp:") : body.find("else:", body.find("if csp:"))]
    assert "Content-Security-Policy" in csp_branch
    assert 'send_header("X-Frame-Options"' not in csp_branch
