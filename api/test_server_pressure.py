import urllib.parse

import pytest

import api.routes as routes
import api.routes_dispatcher as dispatcher
from api.routes_helpers import server_pressure


@pytest.fixture
def memory_pressure_enabled(monkeypatch):
    monkeypatch.setenv(server_pressure.SERVER_MEMORY_PRESSURE_ENABLED_ENV, "1")


class _FakeHandler:
    headers = {}
    close_connection = False


class _FakeSession:
    messages = []

    def compact(self):
        return {"session_id": "session-1"}


def _write_meminfo(path, *, total: int, available: int) -> None:
    path.write_text(
        f"MemTotal: {total} kB\n"
        f"MemAvailable: {available} kB\n",
        encoding="utf-8",
    )


def _missing_cgroup_paths(tmp_path):
    return tmp_path / "memory.current", tmp_path / "memory.max"


def test_meminfo_below_threshold_does_not_block(tmp_path):
    meminfo = tmp_path / "meminfo"
    cgroup_current, cgroup_max = _missing_cgroup_paths(tmp_path)
    _write_meminfo(meminfo, total=1000, available=251)

    blocked = server_pressure._is_server_memory_pressure_exceeded(
        proc_meminfo_path=meminfo,
        cgroup_current_path=cgroup_current,
        cgroup_max_path=cgroup_max,
    )

    assert blocked is False


def test_meminfo_equal_threshold_does_not_block(tmp_path):
    meminfo = tmp_path / "meminfo"
    cgroup_current, cgroup_max = _missing_cgroup_paths(tmp_path)
    _write_meminfo(meminfo, total=1000, available=250)

    blocked = server_pressure._is_server_memory_pressure_exceeded(
        proc_meminfo_path=meminfo,
        cgroup_current_path=cgroup_current,
        cgroup_max_path=cgroup_max,
    )

    assert blocked is False


def test_memory_pressure_disabled_by_default_even_when_above_threshold(tmp_path, monkeypatch):
    monkeypatch.delenv(server_pressure.SERVER_MEMORY_PRESSURE_ENABLED_ENV, raising=False)
    meminfo = tmp_path / "meminfo"
    cgroup_current, cgroup_max = _missing_cgroup_paths(tmp_path)
    _write_meminfo(meminfo, total=1000, available=249)

    blocked = server_pressure._is_server_memory_pressure_exceeded(
        proc_meminfo_path=meminfo,
        cgroup_current_path=cgroup_current,
        cgroup_max_path=cgroup_max,
    )

    assert blocked is False


def test_meminfo_above_threshold_blocks(tmp_path, memory_pressure_enabled):
    meminfo = tmp_path / "meminfo"
    cgroup_current, cgroup_max = _missing_cgroup_paths(tmp_path)
    _write_meminfo(meminfo, total=1000, available=249)

    blocked = server_pressure._is_server_memory_pressure_exceeded(
        proc_meminfo_path=meminfo,
        cgroup_current_path=cgroup_current,
        cgroup_max_path=cgroup_max,
    )

    assert blocked is True


def test_meminfo_missing_or_malformed_fails_open(tmp_path):
    cgroup_current, cgroup_max = _missing_cgroup_paths(tmp_path)
    missing_meminfo = tmp_path / "missing-meminfo"

    assert server_pressure._is_server_memory_pressure_exceeded(
        proc_meminfo_path=missing_meminfo,
        cgroup_current_path=cgroup_current,
        cgroup_max_path=cgroup_max,
    ) is False

    malformed_meminfo = tmp_path / "malformed-meminfo"
    malformed_meminfo.write_text("MemTotal: nope\n", encoding="utf-8")

    assert server_pressure._is_server_memory_pressure_exceeded(
        proc_meminfo_path=malformed_meminfo,
        cgroup_current_path=cgroup_current,
        cgroup_max_path=cgroup_max,
    ) is False


def test_cgroup_v2_max_value_is_ignored(tmp_path):
    missing_meminfo = tmp_path / "missing-meminfo"
    cgroup_current = tmp_path / "memory.current"
    cgroup_max = tmp_path / "memory.max"
    cgroup_current.write_text("900\n", encoding="utf-8")
    cgroup_max.write_text("max\n", encoding="utf-8")

    blocked = server_pressure._is_server_memory_pressure_exceeded(
        proc_meminfo_path=missing_meminfo,
        cgroup_current_path=cgroup_current,
        cgroup_max_path=cgroup_max,
    )

    assert blocked is False


def test_cgroup_v2_finite_limit_can_block(tmp_path, memory_pressure_enabled):
    missing_meminfo = tmp_path / "missing-meminfo"
    cgroup_current = tmp_path / "memory.current"
    cgroup_max = tmp_path / "memory.max"
    cgroup_current.write_text("751\n", encoding="utf-8")
    cgroup_max.write_text("1000\n", encoding="utf-8")

    blocked = server_pressure._is_server_memory_pressure_exceeded(
        proc_meminfo_path=missing_meminfo,
        cgroup_current_path=cgroup_current,
        cgroup_max_path=cgroup_max,
    )

    assert blocked is True


def test_default_pressure_threshold_is_75_percent():
    assert server_pressure.SERVER_MEMORY_PRESSURE_THRESHOLD_PERCENT == 75.0


def test_default_pressure_message_matches_user_copy():
    assert server_pressure.SERVER_MEMORY_PRESSURE_MESSAGE == "请求人数超过80，请稍后再试～"


def test_token_login_pressure_short_circuits_before_body_read(monkeypatch):
    captured = {}

    def capture_j(_handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status
        captured["extra_headers"] = extra_headers
        captured["handler_close_connection"] = _handler.close_connection
        return True

    monkeypatch.setattr(routes, "_is_server_memory_pressure_exceeded", lambda: True)
    monkeypatch.setattr(routes, "j", capture_j)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: (_ for _ in ()).throw(AssertionError("read_body must not run")),
    )

    handled = dispatcher.dispatch_post(
        _FakeHandler(),
        urllib.parse.urlparse("/api/auth/token-login"),
    )

    assert handled is True
    assert captured == {
        "payload": {
            "error": server_pressure.SERVER_MEMORY_PRESSURE_MESSAGE,
            "code": server_pressure.SERVER_MEMORY_PRESSURE_CODE,
        },
        "status": 503,
        "extra_headers": {
            "Retry-After": server_pressure.SERVER_MEMORY_PRESSURE_RETRY_AFTER,
            "Connection": "close",
        },
        "handler_close_connection": True,
    }


def test_password_login_pressure_short_circuits_before_body_read(monkeypatch):
    captured = {}

    def capture_j(_handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status
        captured["extra_headers"] = extra_headers
        captured["handler_close_connection"] = _handler.close_connection
        return True

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_is_server_memory_pressure_exceeded", lambda: True)
    monkeypatch.setattr(routes, "j", capture_j)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: (_ for _ in ()).throw(AssertionError("read_body must not run")),
    )

    handled = dispatcher.dispatch_post(
        _FakeHandler(),
        urllib.parse.urlparse("/api/auth/login"),
    )

    assert handled is True
    assert captured == {
        "payload": {
            "error": server_pressure.SERVER_MEMORY_PRESSURE_MESSAGE,
            "code": server_pressure.SERVER_MEMORY_PRESSURE_CODE,
        },
        "status": 503,
        "extra_headers": {
            "Retry-After": server_pressure.SERVER_MEMORY_PRESSURE_RETRY_AFTER,
            "Connection": "close",
        },
        "handler_close_connection": True,
    }


def test_session_new_pressure_short_circuits_before_body_read(monkeypatch):
    captured = {}

    def capture_j(_handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status
        captured["extra_headers"] = extra_headers
        captured["handler_close_connection"] = _handler.close_connection
        return True

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_is_server_memory_pressure_exceeded", lambda: True)
    monkeypatch.setattr(routes, "j", capture_j)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: (_ for _ in ()).throw(AssertionError("read_body must not run")),
    )
    monkeypatch.setattr(
        routes,
        "new_session",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("new_session must not run")),
    )

    handled = dispatcher.dispatch_post(
        _FakeHandler(),
        urllib.parse.urlparse("/api/session/new"),
    )

    assert handled is True
    assert captured == {
        "payload": {
            "error": server_pressure.SERVER_MEMORY_PRESSURE_MESSAGE,
            "code": server_pressure.SERVER_MEMORY_PRESSURE_CODE,
        },
        "status": 503,
        "extra_headers": {
            "Retry-After": server_pressure.SERVER_MEMORY_PRESSURE_RETRY_AFTER,
            "Connection": "close",
        },
        "handler_close_connection": True,
    }


def test_chat_start_pressure_short_circuits_before_handler(monkeypatch):
    captured = {}

    def capture_j(_handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status
        captured["extra_headers"] = extra_headers
        captured["handler_close_connection"] = _handler.close_connection
        return True

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_is_server_memory_pressure_exceeded", lambda: True)
    monkeypatch.setattr(routes, "j", capture_j)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: (_ for _ in ()).throw(AssertionError("read_body must not run")),
    )
    monkeypatch.setattr(
        routes,
        "_handle_chat_start",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("_handle_chat_start must not run")),
    )

    handled = dispatcher.dispatch_post(
        _FakeHandler(),
        urllib.parse.urlparse("/api/chat/start"),
    )

    assert handled is True
    assert captured["status"] == 503
    assert captured["payload"]["error"] == server_pressure.SERVER_MEMORY_PRESSURE_MESSAGE
    assert captured["payload"]["code"] == server_pressure.SERVER_MEMORY_PRESSURE_CODE
    assert captured["extra_headers"] == {
        "Retry-After": server_pressure.SERVER_MEMORY_PRESSURE_RETRY_AFTER,
        "Connection": "close",
    }
    assert captured["handler_close_connection"] is True


def test_session_new_without_pressure_keeps_existing_flow(monkeypatch):
    captured = {}
    calls = {}

    def capture_j(_handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status
        captured["extra_headers"] = extra_headers
        return True

    def fake_new_session(**kwargs):
        calls["new_session"] = kwargs
        return _FakeSession()

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_is_server_memory_pressure_exceeded", lambda: False)
    monkeypatch.setattr(routes, "j", capture_j)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {"profile": "default", "model": "m1"})
    monkeypatch.setattr(routes, "new_session", fake_new_session)
    monkeypatch.setattr(routes, "_session_model_state_from_request", lambda model, provider: (model, provider))

    handled = dispatcher.dispatch_post(
        _FakeHandler(),
        urllib.parse.urlparse("/api/session/new"),
    )

    assert handled is True
    assert captured["status"] == 200
    assert captured["payload"]["session"]["session_id"] == "session-1"
    assert calls["new_session"]["profile"] == "default"
    assert calls["new_session"]["model"] == "m1"


def test_chat_start_without_pressure_keeps_existing_flow(monkeypatch):
    captured = {}

    def fake_chat_start(_handler, body):
        captured["body"] = body
        return True

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_is_server_memory_pressure_exceeded", lambda: False)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {"session_id": "session-1", "message": "hi"})
    monkeypatch.setattr(routes, "_handle_chat_start", fake_chat_start)

    handled = dispatcher.dispatch_post(
        _FakeHandler(),
        urllib.parse.urlparse("/api/chat/start"),
    )

    assert handled is True
    assert captured["body"] == {"session_id": "session-1", "message": "hi"}
