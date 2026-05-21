"""Regression test for #1386: CLI session import must not crash when the
session is missing from `get_cli_sessions()` metadata at the time of import.

Before the fix, `_handle_session_import_cli` only assigned `model` inside
the `for cs in get_cli_sessions(): if cs["session_id"] == sid` loop. If
the session existed in the messages store but had no metadata row (or had
been pruned after `get_cli_session_messages()` was called), `model` was
unbound and `import_cli_session(sid, title, msgs, model, ...)` raised
`UnboundLocalError`.

The fix initializes `model = "unknown"` before the loop so the import
proceeds with a sensible default rather than crashing.
"""

from __future__ import annotations

from pathlib import Path

from tests.route_source import function_source

REPO = Path(__file__).resolve().parents[1]


def _extract_handler(name: str) -> str:
    """Return the source of a route-layer handler function."""
    return function_source(name)


def test_import_cli_initializes_model_before_metadata_loop():
    """The fallback `model = 'unknown'` must be set BEFORE the
    `for cs in get_cli_sessions()` loop so that a metadata-less session
    cannot leave `model` unbound."""
    handler = _extract_handler("_handle_session_import_cli")
    init_idx = handler.find('model = "unknown"')
    if init_idx == -1:
        # Allow single quotes too.
        init_idx = handler.find("model = 'unknown'")
    assert init_idx != -1, (
        "Expected `model = \"unknown\"` initialization in "
        "_handle_session_import_cli before the metadata loop. Without it, "
        "import crashes when the session has messages but no metadata row."
    )
    loop_idx = handler.find("for cs in get_cli_sessions()")
    assert loop_idx != -1, "Expected `for cs in get_cli_sessions()` loop"
    assert init_idx < loop_idx, (
        "`model` must be initialized BEFORE the `for cs in get_cli_sessions()` "
        "loop, otherwise a session without a metadata row leaves `model` "
        "unbound and `import_cli_session(..., model, ...)` raises "
        "UnboundLocalError."
    )


def test_import_cli_passes_model_to_import_helper():
    """Sanity: the handler still passes the resolved model down to
    `import_cli_session` — the regression test would not catch a refactor
    that drops the argument entirely."""
    handler = _extract_handler("_handle_session_import_cli")
    assert "import_cli_session(" in handler
    # The model variable should appear as a positional or keyword arg in
    # the import_cli_session call.
    call_idx = handler.find("import_cli_session(")
    call_block = handler[call_idx : call_idx + 400]
    assert "model" in call_block, (
        "import_cli_session() call should still receive the `model` argument."
    )


def test_session_import_cli_refresh_matches_messages_despite_timestamp_type_differences(monkeypatch):
    """Refreshing an imported session should still extend when timestamps differ only by type.

    Existing WebUI messages can use integer timestamps while CLI refresh returns
    floating-point timestamps for the same turns. This test verifies the handler
    accepts that as semantic equality and replaces with the longer, fresher tail.
    """
    import api.routes as routes

    session_id = "ts_type_diff_001"

    class FakeSession:
        def __init__(self):
            self.messages = [
                {"role": "user", "content": "hello", "timestamp": 1710000000},
                {"role": "assistant", "content": "working", "timestamp": 1710000001},
            ]
            self.source_tag = "weixin"
            self.raw_source = "weixin"
            self.session_source = "messaging"
            self.source_label = "WeChat"
            self.parent_session_id = None

        def compact(self):
            return {"session_id": session_id, "title": "Imported"}

        def save(self, touch_updated_at=False):
            save_calls.append(touch_updated_at)

    save_calls = []
    existing = FakeSession()
    fresh = [
        {"role": "user", "content": "hello", "timestamp": 1710000000.0},
        {"role": "assistant", "content": "working", "timestamp": 1710000001.0},
        {"role": "assistant", "content": "next", "timestamp": 1710000002.0},
    ]

    monkeypatch.setattr(routes.Session, "load", classmethod(lambda _cls, sid: existing if sid == session_id else None))
    monkeypatch.setattr(routes, "require", lambda body, *keys: None)
    monkeypatch.setattr(routes, "bad", lambda _handler, msg, status=400: {"ok": False, "error": msg, "status": status})
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, extra_headers=None: payload)
    monkeypatch.setattr(routes, "get_cli_session_messages", lambda sid: fresh if sid == session_id else [])
    monkeypatch.setattr(routes, "get_cli_sessions", lambda: [{"session_id": session_id, "source_tag": "weixin", "raw_source": "weixin", "session_source": "messaging", "source_label": "WeChat"}])

    response = routes._handle_session_import_cli(object(), {"session_id": session_id})

    assert response["imported"] is False
    assert response["session"]["messages"] == fresh
    assert existing.messages == fresh
    assert save_calls == [False]


def test_session_import_cli_refresh_rejects_prefix_if_non_timing_content_diverges(monkeypatch):
    """Only true prefixes should be treated as unchanged history during refresh.

    If the refreshed message body diverges, we should keep the existing in-memory
    transcript instead of replacing it with potentially older content.
    """
    import api.routes as routes

    session_id = "ts_type_diverge_001"

    class FakeSession:
        def __init__(self):
            self.messages = [
                {"role": "user", "content": "old-prefix", "timestamp": 1710000000},
                {"role": "assistant", "content": "from local", "timestamp": 1710000001},
            ]
            self.source_tag = "telegram"
            self.raw_source = "telegram"
            self.session_source = "messaging"
            self.source_label = "Telegram"
            self.is_cli_session = True
            self.parent_session_id = None

        def compact(self):
            return {"session_id": session_id, "title": "Imported"}

        def save(self, touch_updated_at=False):
            save_calls.append(touch_updated_at)

    save_calls = []
    existing = FakeSession()
    fresh = [
        {"role": "user", "content": "different-prefix", "timestamp": 1710000000.0},
        {"role": "assistant", "content": "from cli", "timestamp": 1710000001.0},
        {"role": "assistant", "content": "next", "timestamp": 1710000002.0},
    ]

    monkeypatch.setattr(routes.Session, "load", classmethod(lambda _cls, sid: existing if sid == session_id else None))
    monkeypatch.setattr(routes, "require", lambda body, *keys: None)
    monkeypatch.setattr(routes, "bad", lambda _handler, msg, status=400: {"ok": False, "error": msg, "status": status})
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, extra_headers=None: payload)
    monkeypatch.setattr(routes, "get_cli_session_messages", lambda sid: fresh if sid == session_id else [])
    monkeypatch.setattr(routes, "get_cli_sessions", lambda: [{"session_id": session_id, "source_tag": "telegram", "raw_source": "telegram", "session_source": "messaging", "source_label": "Telegram"}])

    response = routes._handle_session_import_cli(object(), {"session_id": session_id})

    assert response["imported"] is False
    assert response["session"]["messages"] == existing.messages
    assert existing.messages[0]["content"] == "old-prefix"
    assert save_calls == []


def test_session_import_cli_preserves_parent_metadata_on_existing_import(monkeypatch):
    """Refreshing an already-imported CLI session must persist lineage metadata."""
    import api.routes as routes

    session_id = "existing_parent_lineage_001"
    parent_id = "root_parent_lineage_001"

    class FakeSession:
        def __init__(self):
            self.messages = [{"role": "user", "content": "hello", "timestamp": 1.0}]
            self.source_tag = "telegram"
            self.raw_source = "telegram"
            self.session_source = "messaging"
            self.source_label = "Telegram"
            self.parent_session_id = None
            self.is_cli_session = True

        def compact(self):
            return {"session_id": session_id, "title": "Imported", "parent_session_id": self.parent_session_id}

        def save(self, touch_updated_at=False):
            save_calls.append(touch_updated_at)

    save_calls = []
    existing = FakeSession()

    monkeypatch.setattr(routes.Session, "load", classmethod(lambda _cls, sid: existing if sid == session_id else None))
    monkeypatch.setattr(routes, "require", lambda body, *keys: None)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, extra_headers=None: payload)
    monkeypatch.setattr(routes, "get_cli_session_messages", lambda sid: existing.messages if sid == session_id else [])
    monkeypatch.setattr(
        routes,
        "get_cli_sessions",
        lambda: [{
            "session_id": session_id,
            "source_tag": "telegram",
            "raw_source": "telegram",
            "session_source": "messaging",
            "source_label": "Telegram",
            "parent_session_id": parent_id,
        }],
    )

    response = routes._handle_session_import_cli(object(), {"session_id": session_id})

    assert response["imported"] is False
    assert existing.parent_session_id == parent_id
    assert response["session"]["parent_session_id"] == parent_id
    assert save_calls == [False]


def test_read_only_import_payload_includes_parent_session_id(monkeypatch):
    """Read-only CLI/session imports should also expose lineage in the payload."""
    import api.routes as routes

    session_id = "readonly_parent_lineage_001"
    parent_id = "readonly_root_lineage_001"
    messages = [{"role": "user", "content": "hello", "timestamp": 1.0}]

    monkeypatch.setattr(routes.Session, "load", classmethod(lambda _cls, sid: None))
    monkeypatch.setattr(routes, "require", lambda body, *keys: None)
    monkeypatch.setattr(routes, "bad", lambda _handler, msg, status=400: {"ok": False, "error": msg, "status": status})
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, extra_headers=None: payload)
    monkeypatch.setattr(routes, "get_cli_session_messages", lambda sid: messages if sid == session_id else [])
    monkeypatch.setattr(
        routes,
        "get_cli_sessions",
        lambda: [{
            "session_id": session_id,
            "title": "Read-only child",
            "model": "test-model",
            "created_at": 1.0,
            "updated_at": 2.0,
            "source_tag": "discord",
            "raw_source": "discord",
            "session_source": "messaging",
            "source_label": "Discord",
            "parent_session_id": parent_id,
            "read_only": True,
        }],
    )

    response = routes._handle_session_import_cli(object(), {"session_id": session_id})

    assert response["imported"] is False
    assert response["session"]["parent_session_id"] == parent_id
    assert response["session"]["messages"] == messages


def test_merge_cli_sidebar_metadata_keeps_larger_sidecar_message_count():
    """Sidebar metadata merge should not shrink repaired aggregate sidecar counts."""
    import api.routes as routes

    merged = routes._merge_cli_sidebar_metadata(
        {"session_id": "sid", "message_count": 535, "title": "Recovered"},
        {"session_id": "sid", "message_count": 407, "source_tag": "discord"},
    )

    assert merged["message_count"] == 535


def test_messaging_session_loader_prefers_longer_sidecar_transcript():
    """GET /api/session should prefer a longer repaired sidecar over state.db rows."""
    import api.routes as routes
    from tests.route_test_utils import invoke_route

    session_id = "messaging_sidecar_longer_001"

    class FakeSession:
        title = "Recovered"
        workspace = "/tmp"
        model = "test-model"
        model_provider = None
        session_source = "messaging"
        raw_source = "discord"
        source_tag = "discord"
        source_label = "Discord"
        messages = [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
        ]
        tool_calls = []
        active_stream_id = None
        pending_user_message = None
        pending_attachments = []
        pending_started_at = None
        context_length = 0
        threshold_tokens = 0
        last_prompt_tokens = 0

        def __init__(self):
            self.session_id = session_id

        def compact(self):
            return {
                "session_id": self.session_id,
                "title": self.title,
                "model": self.model,
                "message_count": len(self.messages),
                "session_source": self.session_source,
                "raw_source": self.raw_source,
            }

    sidecar = FakeSession()
    cli_messages = [{"role": "user", "content": "one"}]

    original_get_session = routes.get_session
    original_get_cli_messages = routes.get_cli_session_messages
    original_get_cli_sessions = routes.get_cli_sessions
    original_model = routes._resolve_effective_session_model_for_display
    original_provider = routes._resolve_effective_session_model_provider_for_display
    try:
        routes.get_session = lambda sid, metadata_only=False: sidecar
        routes.get_cli_session_messages = lambda sid: cli_messages if sid == session_id else []
        routes.get_cli_sessions = lambda: [{
            "session_id": session_id,
            "session_source": "messaging",
            "raw_source": "discord",
            "source_tag": "discord",
            "source_label": "Discord",
            "message_count": len(cli_messages),
        }]
        routes._resolve_effective_session_model_for_display = lambda session: session.model
        routes._resolve_effective_session_model_provider_for_display = lambda session: None

        response = invoke_route("get", f"/api/session?session_id={session_id}")
    finally:
        routes.get_session = original_get_session
        routes.get_cli_session_messages = original_get_cli_messages
        routes.get_cli_sessions = original_get_cli_sessions
        routes._resolve_effective_session_model_for_display = original_model
        routes._resolve_effective_session_model_provider_for_display = original_provider

    assert response.status == 200
    assert response.body["session"]["messages"] == sidecar.messages
    assert response.body["session"]["messages"] != cli_messages
