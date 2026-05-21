"""
Tests for feat #450: MEDIA: token inline rendering in web UI chat.

Covers:
1. /api/media endpoint: serves local image files by absolute path
2. /api/media endpoint: rejects paths outside allowed roots (path traversal)
3. /api/media endpoint: 404 for non-existent files
4. /api/media endpoint: auth gate when auth is enabled
5. renderMd() MEDIA: stash/restore logic (static JS analysis)
6. /api/media endpoint: integration test via live server (requires 8788)
"""
from __future__ import annotations

import json
import os
import pathlib
import tempfile
import unittest
import urllib.error
import urllib.request

from tests._pytest_port import BASE, TEST_STATE_DIR

REPO_ROOT = pathlib.Path(__file__).parent.parent
# ── Static analysis: renderMd MEDIA stash ────────────────────────────────────

# ── Static analysis: CSS ──────────────────────────────────────────────────────

# ── Backend: /api/media endpoint (unit-level, no server needed) ─────────────

class TestMediaEndpointUnit(unittest.TestCase):
    """Test route registration and handler logic via imports."""

    def test_handle_media_function_exists(self):
        from api import routes
        self.assertTrue(
            hasattr(routes, "_handle_media"),
            "_handle_media must be defined in api/routes.py",
        )

    def test_api_media_route_registered(self):
        """The GET dispatch must include the /api/media path."""
        from tests.route_source import read_route_sources
        routes_src = read_route_sources()
        self.assertIn('"/api/media"', routes_src,
                      '/api/media must be registered in the GET route dispatch')

    def test_allowed_roots_include_tmp(self):
        """Handler must allow /tmp so screenshot paths work."""
        from tests.route_source import read_route_sources
        routes_src = read_route_sources()
        self.assertIn('/tmp', routes_src,
                      '/tmp must be in the allowed roots list for /api/media')

    def test_svg_forces_download(self):
        """.svg must not be served inline (XSS risk)."""
        from tests.route_source import read_route_sources
        routes_src = read_route_sources()
        # SVG should be in _DOWNLOAD_TYPES or explicitly excluded from inline
        self.assertIn("image/svg+xml", routes_src,
                      "SVG MIME type must be handled (forced download) in _handle_media")

    def test_non_image_forces_download(self):
        """Non-image files should be forced to download, not served inline."""
        from tests.route_source import read_route_sources
        routes_src = read_route_sources()
        self.assertIn("_INLINE_IMAGE_TYPES", routes_src,
                      "_INLINE_IMAGE_TYPES whitelist must exist in _handle_media")

    def test_media_endpoints_advertise_byte_range_support(self):
        from tests.route_source import read_route_sources
        routes_src = read_route_sources()
        self.assertIn("Accept-Ranges", routes_src)
        self.assertIn("Content-Range", routes_src)
        self.assertIn("206", routes_src)


# ── Integration tests: live server on TEST_PORT ───────────────────────────────
# No collection-time skip guard — conftest.py starts the server via its
# autouse session fixture BEFORE tests run.  A collection-time check always
# sees no server and turns every test into a skip.  Instead we assert
# reachability inside setUp() so failures are loud errors, not silent skips.


class TestMediaEndpointIntegration(unittest.TestCase):

    def setUp(self):
        try:
            urllib.request.urlopen(BASE + "/health", timeout=5)
        except Exception as exc:
            self.fail(f"Test server at {BASE} is not reachable: {exc}")

    def _get(self, path, headers=None):
        req = urllib.request.Request(BASE + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read(), r.status, r.headers
        except urllib.error.HTTPError as e:
            return e.read(), e.code, e.headers

    def test_no_path_returns_400(self):
        _, status, _ = self._get("/api/media")
        self.assertEqual(status, 400)

    def test_nonexistent_file_returns_404(self):
        _, status, _ = self._get("/api/media?path=/tmp/__hermes_nonexistent_12345.png")
        self.assertEqual(status, 404)

    def test_path_outside_allowed_root_rejected(self):
        # /etc/passwd is outside allowed roots
        _, status, _ = self._get("/api/media?path=/etc/passwd")
        self.assertIn(status, {403, 404})

    def test_valid_png_served_with_image_mime(self):
        """Create a 1-pixel PNG in /tmp and verify it's served correctly."""
        # Minimal valid 1x1 transparent PNG (67 bytes)
        png_bytes = (
            b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01'
            b'\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00'
            b'\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        )
        with tempfile.NamedTemporaryFile(
            suffix=".png", prefix="hermes_test_", dir="/tmp", delete=False
        ) as f:
            f.write(png_bytes)
            tmp_path = f.name
        try:
            body, status, headers = self._get(
                f"/api/media?path={urllib.request.quote(tmp_path)}"
            )
            self.assertEqual(status, 200, f"Expected 200, got {status}")
            ct = headers.get("Content-Type", "")
            self.assertIn("image/png", ct, f"Expected image/png, got {ct}")
            self.assertEqual(body, png_bytes)
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

    def test_audio_media_endpoint_inline_and_range(self):
        """MEDIA: audio paths stream inline and support byte ranges for playback."""
        audio_bytes = b"RIFF" + (b"\x00" * 256)
        with tempfile.NamedTemporaryFile(
            suffix=".wav", prefix="hermes_test_", dir="/tmp", delete=False
        ) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        try:
            encoded = urllib.request.quote(tmp_path)
            body, status, headers = self._get(f"/api/media?path={encoded}&inline=1")
            self.assertEqual(status, 200)
            self.assertIn("audio/wav", headers.get("Content-Type", ""))
            self.assertIn("inline", headers.get("Content-Disposition", ""))
            self.assertEqual(headers.get("Accept-Ranges"), "bytes")
            self.assertEqual(body, audio_bytes)

            body, status, headers = self._get(
                f"/api/media?path={encoded}&inline=1",
                headers={"Range": "bytes=0-3"},
            )
            self.assertEqual(status, 206)
            self.assertEqual(body, b"RIFF")
            self.assertEqual(headers.get("Content-Range"), f"bytes 0-3/{len(audio_bytes)}")
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

    def test_html_media_endpoint_inline_requires_csp_sandbox(self):
        """HTML opens inline only when requested and always carries CSP sandbox."""
        html_bytes = b"<!doctype html><title>Hermes</title><script>window.ok=1</script>"
        with tempfile.NamedTemporaryFile(
            suffix=".html", prefix="hermes_test_", dir="/tmp", delete=False
        ) as f:
            f.write(html_bytes)
            tmp_path = f.name
        try:
            encoded = urllib.request.quote(tmp_path)

            body, status, headers = self._get(f"/api/media?path={encoded}")
            self.assertEqual(status, 200)
            self.assertIn("text/html", headers.get("Content-Type", ""))
            self.assertIn("attachment", headers.get("Content-Disposition", ""))
            self.assertIn("DENY", headers.get_all("X-Frame-Options", []))
            self.assertFalse(
                any("sandbox allow-scripts" == h for h in headers.get_all("Content-Security-Policy", []))
            )
            self.assertEqual(body, html_bytes)

            body, status, headers = self._get(f"/api/media?path={encoded}&inline=1")
            self.assertEqual(status, 200)
            self.assertIn("text/html", headers.get("Content-Type", ""))
            self.assertIn("inline", headers.get("Content-Disposition", ""))
            self.assertEqual(headers.get_all("X-Frame-Options", []), [])
            self.assertTrue(
                any("sandbox allow-scripts" == h for h in headers.get_all("Content-Security-Policy", []))
            )
            self.assertEqual(body, html_bytes)
        finally:
            pathlib.Path(tmp_path).unlink(missing_ok=True)

    def test_path_traversal_rejected(self):
        _, status, _ = self._get(
            "/api/media?path=" + urllib.request.quote("/tmp/../../etc/passwd")
        )
        self.assertIn(status, {403, 404})

    def test_health_check_still_works(self):
        """Sanity: server is up and /health works."""
        body, status, _ = self._get("/health")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["status"], "ok")
