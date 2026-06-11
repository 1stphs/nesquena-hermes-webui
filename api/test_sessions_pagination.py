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
