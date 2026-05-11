"""Tests for long-lived API token login."""
import hashlib
import http.cookies
import io
import json
from urllib.parse import urlparse

import pytest

import server
from api import auth, routes


class _Headers(dict):
    def get(self, key, default=None):
        wanted = str(key).lower()
        for name, value in self.items():
            if str(name).lower() == wanted:
                return value
        return default


class _FakeHandler:
    command = "POST"
    path = "/api/auth/token-login"
    client_address = ("127.0.0.1", 12345)
    request = object()

    def __init__(self, body=None, headers=None):
        raw = json.dumps(body or {}).encode()
        self.headers = _Headers(headers or {})
        self.headers.setdefault("Content-Length", str(len(raw)))
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = []
        self.ended = False

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers.append((key, value))

    def end_headers(self):
        self.ended = True

    def header_values(self, key):
        wanted = key.lower()
        return [value for name, value in self.sent_headers if name.lower() == wanted]

    def header_value(self, key):
        values = self.header_values(key)
        return values[-1] if values else None

    def json_body(self):
        return json.loads(self.wfile.getvalue().decode() or "{}")


@pytest.fixture(autouse=True)
def _isolate_token_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_API_TOKENS_FILE", str(tmp_path / "api_tokens.json"))
    monkeypatch.delenv("HERMES_WEBUI_CORS_ALLOW_ALL", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_COOKIE_SAMESITE", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_COOKIE_SECURE", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_PASSWORD", raising=False)
    auth._sessions.clear()
    yield
    auth._sessions.clear()


def _write_tokens(path, records):
    path.write_text(json.dumps({"tokens": records}), encoding="utf-8")


def _token_hash(token):
    return "sha256:" + hashlib.sha256(token.encode()).hexdigest()


def _configured_token(tmp_path, token="test-token", **overrides):
    record = {
        "id": "digital-employee-local-test",
        "name": "digital_employee local test",
        "token_hash": _token_hash(token),
        "enabled": True,
        "expires_at": None,
        "allowed_origins": ["*"],
    }
    record.update(overrides)
    _write_tokens(tmp_path / "api_tokens.json", [record])
    return token


def _post_token_login(token, origin="http://localhost:5173"):
    handler = _FakeHandler(
        {"token": token} if token is not None else {},
        headers={"Origin": origin},
    )
    result = routes.handle_post(handler, urlparse("/api/auth/token-login"))
    return result, handler


def test_token_login_success_sets_session_cookie(tmp_path):
    token = _configured_token(tmp_path)

    result, handler = _post_token_login(token)

    assert result is True
    assert handler.status == 200
    assert handler.json_body() == {"ok": True, "token_id": "digital-employee-local-test"}
    assert handler.header_value("Content-Length") == str(len(handler.wfile.getvalue()))

    set_cookie = handler.header_value("Set-Cookie")
    assert set_cookie and "hermes_session=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=Lax" in set_cookie

    cookie = http.cookies.SimpleCookie()
    cookie.load(set_cookie)
    assert auth.verify_session(cookie[auth.COOKIE_NAME].value)


def test_token_login_cookie_supports_cross_site_env_overrides(tmp_path, monkeypatch):
    token = _configured_token(tmp_path)
    monkeypatch.setenv("HERMES_WEBUI_COOKIE_SAMESITE", "None")

    _, handler = _post_token_login(token)

    set_cookie = handler.header_value("Set-Cookie")
    assert "SameSite=None" in set_cookie
    assert "Secure" in set_cookie


def test_token_login_cookie_secure_env_keeps_default_samesite(tmp_path, monkeypatch):
    token = _configured_token(tmp_path)
    monkeypatch.setenv("HERMES_WEBUI_COOKIE_SECURE", "1")

    _, handler = _post_token_login(token)

    set_cookie = handler.header_value("Set-Cookie")
    assert "SameSite=Lax" in set_cookie
    assert "Secure" in set_cookie


@pytest.mark.parametrize(
    "body_token,record_overrides",
    [
        (None, {}),
        ("wrong-token", {}),
        ("test-token", {"enabled": False}),
        ("test-token", {"expires_at": "2000-01-01T00:00:00+00:00"}),
        ("test-token", {"allowed_origins": ["https://allowed.example.com"]}),
    ],
)
def test_token_login_rejects_invalid_disabled_expired_or_wrong_origin(
    tmp_path, body_token, record_overrides
):
    _configured_token(tmp_path, **record_overrides)

    result, handler = _post_token_login(body_token)

    assert result is not False
    assert handler.status == 401
    assert handler.json_body() == {"error": "Invalid token"}
    assert not handler.header_values("Set-Cookie")


def test_token_login_cookie_authorizes_protected_api_when_password_auth_enabled(tmp_path, monkeypatch):
    token = _configured_token(tmp_path)
    monkeypatch.setenv("HERMES_WEBUI_PASSWORD", "secret")

    public_handler = _FakeHandler(headers={"Origin": "http://localhost:5173"})
    assert auth.check_auth(public_handler, urlparse("/api/auth/token-login")) is True

    _, login_handler = _post_token_login(token)
    cookie_header = login_handler.header_value("Set-Cookie").split(";", 1)[0]

    protected_handler = _FakeHandler(headers={"Cookie": cookie_header})
    assert auth.check_auth(protected_handler, urlparse("/api/profiles")) is True

    denied_handler = _FakeHandler()
    assert auth.check_auth(denied_handler, urlparse("/api/profiles")) is False
    assert denied_handler.status == 401


def test_options_returns_credentialed_cors_headers_when_allow_all_enabled(monkeypatch):
    monkeypatch.setenv("HERMES_WEBUI_CORS_ALLOW_ALL", "1")
    handler = _FakeHandler(headers={"Origin": "http://localhost:5173"})
    handler.command = "OPTIONS"
    handler.path = "/api/auth/token-login"

    server.Handler.do_OPTIONS(handler)

    assert handler.status == 204
    assert handler.header_value("Access-Control-Allow-Origin") == "http://localhost:5173"
    assert handler.header_value("Access-Control-Allow-Credentials") == "true"
    assert handler.header_value("Access-Control-Allow-Methods") == "GET, POST, PATCH, DELETE, OPTIONS"
    assert handler.header_value("Access-Control-Allow-Headers") == "Content-Type, Authorization"
    assert handler.header_value("Access-Control-Allow-Origin") != "*"


def test_options_does_not_emit_cors_headers_unless_allow_all_enabled():
    handler = _FakeHandler(headers={"Origin": "http://localhost:5173"})
    handler.command = "OPTIONS"
    handler.path = "/api/auth/token-login"

    server.Handler.do_OPTIONS(handler)

    assert handler.status == 204
    assert handler.header_value("Access-Control-Allow-Origin") is None


def test_csrf_still_rejects_cross_origin_post_without_cors_allow_all():
    handler = _FakeHandler(headers={
        "Origin": "http://localhost:5173",
        "Host": "127.0.0.1:8787",
    })

    assert routes._check_csrf(handler) is False
