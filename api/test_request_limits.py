import contextlib
import urllib.parse

import pytest

import api.routes as routes
import api.routes_dispatcher as dispatcher
from api.routes_handlers import chat as chat_handlers
from api.routes_helpers import request_limits


class _FakeHandler:
    headers = {}
    close_connection = False


def setup_function(_function=None):
    request_limits.reset_request_limits_for_tests()


def teardown_function(_function=None):
    request_limits.reset_request_limits_for_tests()


def test_chat_start_limit_allows_two_streams_per_user_then_rejects_third():
    assert request_limits.try_acquire_chat_start_slot("user-1", "stream-1") is None
    assert request_limits.try_acquire_chat_start_slot("user-1", "stream-2") is None

    rejection = request_limits.try_acquire_chat_start_slot("user-1", "stream-3")

    assert rejection is not None
    assert rejection.kind == "chat_start_user"
    assert rejection.limit == request_limits.CHAT_START_PER_USER_LIMIT
    assert rejection.active == request_limits.CHAT_START_PER_USER_LIMIT

    request_limits.release_chat_start_slot("stream-1")
    assert request_limits.try_acquire_chat_start_slot("user-1", "stream-3") is None


def test_chat_start_global_limit_rejects_after_global_limit():
    for index in range(request_limits.CHAT_START_GLOBAL_LIMIT):
        assert (
            request_limits.try_acquire_chat_start_slot(f"user-{index}", f"stream-{index}")
            is None
        )

    rejection = request_limits.try_acquire_chat_start_slot("user-extra", "stream-extra")

    assert rejection is not None
    assert rejection.kind == "chat_start_global"
    assert rejection.limit == request_limits.CHAT_START_GLOBAL_LIMIT
    assert rejection.active == request_limits.CHAT_START_GLOBAL_LIMIT


def test_session_create_limit_rejects_after_session_create_limit():
    for _index in range(request_limits.SESSION_CREATE_LIMIT):
        assert request_limits.try_acquire_session_create_slot() is None

    rejection = request_limits.try_acquire_session_create_slot()

    assert rejection is not None
    assert rejection.kind == "session_create"
    assert rejection.limit == request_limits.SESSION_CREATE_LIMIT
    assert rejection.active == request_limits.SESSION_CREATE_LIMIT


def test_upload_limit_rejects_after_upload_limit():
    for _index in range(request_limits.UPLOAD_LIMIT):
        assert request_limits.try_acquire_upload_slot() is None

    rejection = request_limits.try_acquire_upload_slot()

    assert rejection is not None
    assert rejection.kind == "upload"
    assert rejection.limit == request_limits.UPLOAD_LIMIT
    assert rejection.active == request_limits.UPLOAD_LIMIT


def test_upload_limit_short_circuits_before_reading_body(monkeypatch):
    captured = {}

    for _index in range(request_limits.UPLOAD_LIMIT):
        assert request_limits.try_acquire_upload_slot() is None

    def capture_j(_handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status
        captured["extra_headers"] = extra_headers
        captured["handler_close_connection"] = _handler.close_connection
        return True

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "j", capture_j)
    monkeypatch.setattr(
        routes,
        "read_body",
        lambda _handler: (_ for _ in ()).throw(AssertionError("read_body must not run")),
    )
    monkeypatch.setattr(
        routes,
        "handle_upload",
        lambda _handler: (_ for _ in ()).throw(AssertionError("upload must not run")),
    )

    handled = dispatcher.dispatch_post(_FakeHandler(), urllib.parse.urlparse("/api/upload"))

    assert handled is True
    assert captured == {
        "payload": {
            "error": request_limits.REQUEST_LIMIT_MESSAGE,
            "code": request_limits.REQUEST_LIMIT_CODE,
            "kind": "upload",
            "limit": request_limits.UPLOAD_LIMIT,
            "active": request_limits.UPLOAD_LIMIT,
        },
        "status": 429,
        "extra_headers": {
            "Retry-After": request_limits.REQUEST_LIMIT_RETRY_AFTER,
            "Connection": "close",
        },
        "handler_close_connection": True,
    }


def test_chat_start_releases_slot_when_prepare_fails(monkeypatch):
    class _FakeSession:
        session_id = "session-1"
        workspace = "/workspace"
        model = "gpt-5.4"
        model_provider = None
        profile = "default"
        messages = []
        active_stream_id = None
        user_id = None

        def save(self):
            raise OSError("simulated session save failure")

    fake_session = _FakeSession()

    monkeypatch.setattr(routes, "get_session", lambda _session_id: fake_session)
    monkeypatch.setattr(routes, "_get_session_agent_lock", lambda _session_id: contextlib.nullcontext())
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda *_args, **_kwargs: ("gpt-5.4", None, "gpt-5.4"),
    )
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda workspace: workspace or "/workspace")
    monkeypatch.setattr(routes, "_normalize_chat_attachments", lambda _attachments: [])
    monkeypatch.setattr(routes, "_clear_stale_stream_state", lambda _session: None)
    monkeypatch.setattr(
        "api.user_provider.optional_user_id_from_handler",
        lambda _handler: "user-1",
    )
    monkeypatch.setattr(
        "api.user_provider.verify_user_profile_access",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(OSError, match="simulated session save failure"):
        chat_handlers._handle_chat_start(
            _FakeHandler(),
            {
                "session_id": "session-1",
                "message": "hello",
                "workspace": "/workspace",
            },
        )

    assert request_limits.try_acquire_chat_start_slot("user-1", "stream-a") is None
    assert request_limits.try_acquire_chat_start_slot("user-1", "stream-b") is None

    rejection = request_limits.try_acquire_chat_start_slot("user-1", "stream-c")

    assert rejection is not None
    assert rejection.kind == "chat_start_user"


@pytest.mark.parametrize(
    ("path", "payload"),
    (
        ("/api/btw", {"session_id": "session-1", "question": "side question"}),
        ("/api/background", {"session_id": "session-1", "prompt": "background task"}),
    ),
)
def test_btw_and_background_return_429_when_chat_stream_limit_reached(
    monkeypatch,
    path,
    payload,
):
    captured = {}

    class _FakeParentSession:
        session_id = "session-1"
        workspace = "/workspace"
        model = "gpt-5.4"
        model_provider = None
        profile = "default"
        messages = []
        active_stream_id = None

    for index in range(request_limits.CHAT_START_GLOBAL_LIMIT):
        assert (
            request_limits.try_acquire_chat_start_slot(f"user-{index}", f"stream-{index}")
            is None
        )

    def capture_j(_handler, response_payload, status=200, extra_headers=None):
        captured["payload"] = response_payload
        captured["status"] = status
        captured["extra_headers"] = extra_headers
        return True

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_is_server_memory_pressure_exceeded", lambda: False)
    monkeypatch.setattr(routes, "j", capture_j)
    monkeypatch.setattr(routes, "read_body", lambda _handler: payload)
    monkeypatch.setattr(routes, "get_session", lambda _session_id: _FakeParentSession())
    monkeypatch.setattr(
        routes,
        "new_session",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("new_session must not run")),
    )

    handled = dispatcher.dispatch_post(_FakeHandler(), urllib.parse.urlparse(path))

    assert handled is True
    assert captured == {
        "payload": {
            "error": request_limits.REQUEST_LIMIT_MESSAGE,
            "code": request_limits.REQUEST_LIMIT_CODE,
            "kind": "chat_start_global",
            "limit": request_limits.CHAT_START_GLOBAL_LIMIT,
            "active": request_limits.CHAT_START_GLOBAL_LIMIT,
        },
        "status": 429,
        "extra_headers": {
            "Retry-After": request_limits.REQUEST_LIMIT_RETRY_AFTER,
        },
    }


def test_session_new_limit_returns_429_without_creating_session(monkeypatch):
    captured = {}

    for _index in range(request_limits.SESSION_CREATE_LIMIT):
        assert request_limits.try_acquire_session_create_slot() is None

    def capture_j(_handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status
        captured["extra_headers"] = extra_headers
        return True

    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_is_server_memory_pressure_exceeded", lambda: False)
    monkeypatch.setattr(routes, "j", capture_j)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {})
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
            "error": request_limits.REQUEST_LIMIT_MESSAGE,
            "code": request_limits.REQUEST_LIMIT_CODE,
            "kind": "session_create",
            "limit": request_limits.SESSION_CREATE_LIMIT,
            "active": request_limits.SESSION_CREATE_LIMIT,
        },
        "status": 429,
        "extra_headers": {
            "Retry-After": request_limits.REQUEST_LIMIT_RETRY_AFTER,
        },
    }
