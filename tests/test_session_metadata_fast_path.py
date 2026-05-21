from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_messages_zero_skips_effective_model_resolution(monkeypatch):
    import api.routes as routes
    from tests.route_test_utils import invoke_route

    class FakeSession:
        session_id = "fast-path"
        title = "Fast Path"
        model = "stored-model"
        model_provider = None
        messages = [{"role": "user", "content": "full history"}]
        tool_calls = [{"name": "tool"}]
        active_stream_id = None
        pending_user_message = None
        pending_attachments = ["attachment"]
        pending_started_at = 123
        context_length = 0
        threshold_tokens = 0
        last_prompt_tokens = 0

        def compact(self):
            return {
                "session_id": self.session_id,
                "title": self.title,
                "model": self.model,
                "message_count": len(self.messages),
            }

    resolved = []

    def resolve_model(_session):
        resolved.append(True)
        return "resolved-model"

    monkeypatch.setattr(routes, "get_session", lambda sid, metadata_only=False: FakeSession())
    monkeypatch.setattr(routes, "_clear_stale_stream_state", lambda session: False)
    monkeypatch.setattr(routes, "_resolve_effective_session_model_for_display", resolve_model)
    monkeypatch.setattr(routes, "_resolve_effective_session_model_provider_for_display", lambda session: None)
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda sid: {})

    response = invoke_route("get", "/api/session?session_id=fast-path&messages=0")

    assert response.status == 200
    assert resolved == []
    session = response.body["session"]
    assert session["model"] == "stored-model"
    assert session["messages"] == []
    assert session["tool_calls"] == []
    assert session["pending_attachments"] == []


def test_full_message_load_updates_viewed_count_after_metadata_fast_path():
    src = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")

    assert "_setSessionViewedCount(S.session.session_id, Number(data.session.message_count || 0));" in src
    assert "_setSessionViewedCount(sid, Number(S.session.message_count || msgs.length));" in src


def test_lazy_message_load_skips_model_resolution():
    src = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")

    assert "messages=1&resolve_model=0" in src


def test_session_switch_defers_model_resolution_without_blocking():
    src = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")

    assert "messages=0&resolve_model=0" in src
    assert "function _resolveSessionModelForDisplaySoon" in src
    assert "messages=0&resolve_model=1" in src
    assert "_modelResolutionDeferred=true" in src
    assert "deferModelCorrection" in ui
    assert "if(fallback&&!deferModelCorrection)" in ui


def test_boot_does_not_block_session_restore_on_model_catalog():
    src = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")

    assert "if(s.default_model) window._defaultModel=s.default_model;" in src
    assert "const _modelDropdownReady=populateModelDropdown().then" in src
    assert "window._modelDropdownReady=_modelDropdownReady" in src
    assert "await populateModelDropdown()" not in src
