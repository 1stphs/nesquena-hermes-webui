from urllib.parse import urlparse

import api.profiles as profiles
import api.routes as routes
from api import routes_dispatcher


def _make_session(index, *, profile="alpha"):
    return {
        "session_id": f"{profile}-{index:02d}",
        "title": f"{profile} title {index:02d}",
        "profile": profile,
        "last_message_at": 1_800_000_000 - index,
    }


def _dispatch_sessions(monkeypatch, url, sessions, *, active_profile="alpha"):
    responses = []

    routes_dispatcher._clear_sessions_list_cache_for_tests()
    monkeypatch.setattr(routes, "all_sessions", lambda: list(sessions))
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": False})
    monkeypatch.setattr(routes, "get_cli_sessions", lambda: [])
    monkeypatch.setattr(routes, "_is_cli_session_for_settings", lambda _session: False)
    monkeypatch.setattr(
        routes,
        "_keep_latest_messaging_session_per_source",
        lambda scoped: list(scoped),
    )
    monkeypatch.setattr(routes, "_redact_text", lambda text: text)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: active_profile)
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: responses.append((status, payload))
        or True,
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: responses.append((status, {"error": message}))
        or True,
    )

    assert routes_dispatcher.dispatch_get(object(), urlparse(url)) is True
    assert responses
    return responses[-1]


def test_sessions_pagination_returns_first_page_after_profile_scope(monkeypatch):
    sessions = [
        *[_make_session(index, profile="beta") for index in range(1, 6)],
        *[_make_session(index) for index in range(1, 26)],
    ]

    status, payload = _dispatch_sessions(
        monkeypatch,
        "/api/sessions?hermes_profile=alpha&page=1&page_size=10",
        sessions,
    )

    assert status == 200
    assert [session["session_id"] for session in payload["sessions"]] == [
        f"alpha-{index:02d}" for index in range(1, 11)
    ]
    assert payload["other_profile_count"] == 5
    assert payload["pagination"] == {
        "page": 1,
        "page_size": 10,
        "total": 25,
        "total_pages": 3,
        "has_more": True,
        "next_page": 2,
    }


def test_sessions_pagination_returns_next_page(monkeypatch):
    sessions = [_make_session(index) for index in range(1, 26)]

    status, payload = _dispatch_sessions(
        monkeypatch,
        "/api/sessions?hermes_profile=alpha&page=2&page_size=10",
        sessions,
    )

    assert status == 200
    assert [session["session_id"] for session in payload["sessions"]] == [
        f"alpha-{index:02d}" for index in range(11, 21)
    ]
    assert payload["pagination"]["has_more"] is True
    assert payload["pagination"]["next_page"] == 3


def test_sessions_pagination_returns_empty_out_of_range_page(monkeypatch):
    sessions = [_make_session(index) for index in range(1, 26)]

    status, payload = _dispatch_sessions(
        monkeypatch,
        "/api/sessions?hermes_profile=alpha&page=4&page_size=10",
        sessions,
    )

    assert status == 200
    assert payload["sessions"] == []
    assert payload["pagination"] == {
        "page": 4,
        "page_size": 10,
        "total": 25,
        "total_pages": 3,
        "has_more": False,
        "next_page": None,
    }


def test_sessions_request_without_pagination_keeps_full_list_contract(monkeypatch):
    sessions = [_make_session(index) for index in range(1, 26)]

    status, payload = _dispatch_sessions(
        monkeypatch,
        "/api/sessions?hermes_profile=alpha",
        sessions,
    )

    assert status == 200
    assert [session["session_id"] for session in payload["sessions"]] == [
        f"alpha-{index:02d}" for index in range(1, 26)
    ]
    assert "pagination" not in payload


def test_sessions_pagination_uses_default_page_size(monkeypatch):
    sessions = [_make_session(index) for index in range(1, 26)]

    status, payload = _dispatch_sessions(
        monkeypatch,
        "/api/sessions?hermes_profile=alpha&page=1",
        sessions,
    )

    assert status == 200
    assert len(payload["sessions"]) == 20
    assert payload["pagination"]["page_size"] == 20
    assert payload["pagination"]["total_pages"] == 2


def test_sessions_pagination_rejects_invalid_page(monkeypatch):
    status, payload = _dispatch_sessions(
        monkeypatch,
        "/api/sessions?hermes_profile=alpha&page=0&page_size=10",
        [],
    )

    assert status == 400
    assert payload == {"error": "page must be a positive integer"}


def test_sessions_pagination_rejects_too_large_page_size(monkeypatch):
    status, payload = _dispatch_sessions(
        monkeypatch,
        "/api/sessions?hermes_profile=alpha&page=1&page_size=101",
        [],
    )

    assert status == 400
    assert payload == {"error": "page_size must be between 1 and 100"}


def test_sessions_list_uses_short_cache_for_repeated_source(monkeypatch):
    routes_dispatcher._clear_sessions_list_cache_for_tests()
    responses = []
    calls = []
    sessions = [_make_session(index) for index in range(1, 4)]

    def fake_all_sessions():
        calls.append("all_sessions")
        return list(sessions)

    monkeypatch.setattr(routes, "all_sessions", fake_all_sessions)
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": False})
    monkeypatch.setattr(routes, "get_cli_sessions", lambda: [])
    monkeypatch.setattr(routes, "_is_cli_session_for_settings", lambda _session: False)
    monkeypatch.setattr(
        routes,
        "_keep_latest_messaging_session_per_source",
        lambda scoped: list(scoped),
    )
    monkeypatch.setattr(routes, "_redact_text", lambda text: text)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "alpha")
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: responses.append((status, payload))
        or True,
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: responses.append((status, {"error": message}))
        or True,
    )

    assert routes_dispatcher.dispatch_get(object(), urlparse("/api/sessions?hermes_profile=alpha"))
    assert routes_dispatcher.dispatch_get(object(), urlparse("/api/sessions?hermes_profile=alpha"))

    assert calls == ["all_sessions"]
    assert len(responses) == 2
    assert [session["session_id"] for session in responses[-1][1]["sessions"]] == [
        "alpha-01",
        "alpha-02",
        "alpha-03",
    ]


def test_sessions_list_overlay_clears_stale_streaming_state_on_cache_hit(monkeypatch):
    import api.config as config

    routes_dispatcher._clear_sessions_list_cache_for_tests()
    responses = []
    calls = []
    sessions = [
        {
            "session_id": "alpha-01",
            "title": "alpha title 01",
            "profile": "alpha",
            "active_stream_id": "stream-1",
            "is_streaming": True,
            "last_message_at": 1_800_000_000,
        }
    ]

    monkeypatch.setattr(routes, "all_sessions", lambda: (calls.append("all_sessions") or list(sessions)))
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": False})
    monkeypatch.setattr(routes, "get_cli_sessions", lambda: [])
    monkeypatch.setattr(routes, "_is_cli_session_for_settings", lambda _session: False)
    monkeypatch.setattr(
        routes,
        "_keep_latest_messaging_session_per_source",
        lambda scoped: list(scoped),
    )
    monkeypatch.setattr(routes, "_redact_text", lambda text: text)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "alpha")
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: responses.append((status, payload))
        or True,
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: responses.append((status, {"error": message}))
        or True,
    )

    with config.STREAMS_LOCK:
        config.STREAMS["stream-1"] = object()

    assert routes_dispatcher.dispatch_get(object(), urlparse("/api/sessions?hermes_profile=alpha"))

    with config.STREAMS_LOCK:
        config.STREAMS.clear()

    assert routes_dispatcher.dispatch_get(object(), urlparse("/api/sessions?hermes_profile=alpha"))

    assert calls == ["all_sessions"]
    assert responses[-1][1]["sessions"][0]["is_streaming"] is False


def test_sessions_list_overlay_applies_live_streaming_state_on_cache_hit(monkeypatch):
    import api.config as config

    routes_dispatcher._clear_sessions_list_cache_for_tests()
    responses = []
    calls = []
    sessions = [
        {
            "session_id": "alpha-01",
            "title": "alpha title 01",
            "profile": "alpha",
            "active_stream_id": "stream-2",
            "is_streaming": False,
            "last_message_at": 1_800_000_000,
        }
    ]

    monkeypatch.setattr(routes, "all_sessions", lambda: (calls.append("all_sessions") or list(sessions)))
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": False})
    monkeypatch.setattr(routes, "get_cli_sessions", lambda: [])
    monkeypatch.setattr(routes, "_is_cli_session_for_settings", lambda _session: False)
    monkeypatch.setattr(
        routes,
        "_keep_latest_messaging_session_per_source",
        lambda scoped: list(scoped),
    )
    monkeypatch.setattr(routes, "_redact_text", lambda text: text)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "alpha")
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: responses.append((status, payload))
        or True,
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: responses.append((status, {"error": message}))
        or True,
    )

    assert routes_dispatcher.dispatch_get(object(), urlparse("/api/sessions?hermes_profile=alpha"))

    with config.STREAMS_LOCK:
        config.STREAMS["stream-2"] = object()

    assert routes_dispatcher.dispatch_get(object(), urlparse("/api/sessions?hermes_profile=alpha"))

    assert calls == ["all_sessions"]
    assert responses[-1][1]["sessions"][0]["active_stream_id"] == "stream-2"
    assert responses[-1][1]["sessions"][0]["is_streaming"] is True


def test_sessions_list_cache_is_invalidated_when_all_sessions_source_changes(monkeypatch):
    routes_dispatcher._clear_sessions_list_cache_for_tests()
    responses = []
    calls = []

    def fake_all_sessions_v1():
        calls.append("v1")
        return [_make_session(1)]

    def fake_all_sessions_v2():
        calls.append("v2")
        return [_make_session(2)]

    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": False})
    monkeypatch.setattr(routes, "get_cli_sessions", lambda: [])
    monkeypatch.setattr(routes, "_is_cli_session_for_settings", lambda _session: False)
    monkeypatch.setattr(
        routes,
        "_keep_latest_messaging_session_per_source",
        lambda scoped: list(scoped),
    )
    monkeypatch.setattr(routes, "_redact_text", lambda text: text)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "alpha")
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kwargs: responses.append((status, payload))
        or True,
    )
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, message, status=400: responses.append((status, {"error": message}))
        or True,
    )

    monkeypatch.setattr(routes, "all_sessions", fake_all_sessions_v1)
    assert routes_dispatcher.dispatch_get(object(), urlparse("/api/sessions?hermes_profile=alpha"))

    monkeypatch.setattr(routes, "all_sessions", fake_all_sessions_v2)
    assert routes_dispatcher.dispatch_get(object(), urlparse("/api/sessions?hermes_profile=alpha"))

    assert calls == ["v1", "v2"]
    assert [session["session_id"] for session in responses[-1][1]["sessions"]] == ["alpha-02"]
