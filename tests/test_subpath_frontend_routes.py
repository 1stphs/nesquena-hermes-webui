"""Regression tests for API-only auth behavior under subpath mounts like /hermes/."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_server_auth_no_longer_redirects_to_login_page():
    src = read("api/auth.py")
    assert "handler.send_header('Location'" not in src
    assert "Authentication required" in src
