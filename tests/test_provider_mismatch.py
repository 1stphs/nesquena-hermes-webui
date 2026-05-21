"""
Tests for issue #266 — provider/model mismatch warning.

Covers:
  1. streaming.py: auth errors detected and classified as 'auth_mismatch'
  2. static/ui.js: _checkProviderMismatch() helper exists and logic is correct
  3. static/messages.js: apperror handler has auth_mismatch branch
  4. static/i18n.js: provider_mismatch_warning and provider_mismatch_label keys
     present in all locales (en, es, de, ru, zh, zh-Hant)
  5. static/boot.js: modelSelect.onchange calls _checkProviderMismatch
  6. /api/models: response includes active_provider field
"""
import json
import pathlib
import re
import urllib.request
from tests.conftest import TEST_STATE_DIR

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
from tests._pytest_port import BASE


def _read(rel_path: str) -> str:
    return (REPO_ROOT / rel_path).read_text(encoding="utf-8")


def _post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()), r.status


# ── 1. streaming.py: auth error detection ───────────────────────────────────

class TestStreamingAuthErrorDetection:
    """streaming.py must classify auth/401 errors as auth_mismatch."""

    def test_auth_mismatch_type_defined_in_streaming(self):
        """'auth_mismatch' type must be emitted for auth errors."""
        src = _read("api/streaming.py")
        assert "auth_mismatch" in src, (
            "auth_mismatch type not found in streaming.py — "
            "401/auth errors will not be surfaced with a helpful message"
        )

    def test_is_auth_error_flag_defined(self):
        """auth error variable must exist in the error handler (exception path and silent-failure path)."""
        src = _read("api/streaming.py")
        # Variable renamed to _exc_is_auth in exception path, _is_auth in silent-failure path
        assert "_exc_is_auth" in src or "_is_auth" in src, (
            "auth error flag not found in streaming.py"
        )

    def test_auth_error_detects_401(self):
        """'401' must be part of the auth error detection logic."""
        src = _read("api/streaming.py")
        # Find the is_auth_error block
        # Variable renamed to _exc_is_auth in exception path, _is_auth in silent-failure path
        idx = src.find("_exc_is_auth")
        assert idx != -1
        block = src[idx:idx + 500]
        assert "'401'" in block or '"401"' in block, (
            "'401' not in auth error detection block"
        )

    def test_auth_error_detects_unauthorized(self):
        """'unauthorized' must be part of the auth error detection logic."""
        src = _read("api/streaming.py")
        # Variable renamed to _exc_is_auth in exception path
        idx = src.find("_exc_is_auth")
        block = src[idx:idx + 500]
        assert "unauthorized" in block.lower(), (
            "'unauthorized' not in auth error detection block"
        )

    def test_auth_error_hint_mentions_hermes_model(self):
        """The auth_mismatch hint must mention 'hermes model' command."""
        src = _read("api/streaming.py")
        # Find the auth_mismatch apperror block
        idx = src.find("auth_mismatch")
        block = src[idx:idx + 500]
        assert "hermes model" in block, (
            "auth_mismatch hint must mention 'hermes model' command "
            "so users know how to fix provider mismatch"
        )

    def test_auth_error_does_not_catch_rate_limit(self):
        """Rate limit errors must not be reclassified as auth_mismatch."""
        src = _read("api/streaming.py")
        # Variables renamed: _exc_is_rate_limit / _exc_is_auth in exception path
        # Quota check comes first (before rate limit), then rate limit, then auth
        rl_idx = src.find("_exc_is_rate_limit")
        ae_idx = src.find("_exc_is_auth")
        assert rl_idx != -1, "_exc_is_rate_limit not found in streaming.py exception path"
        assert ae_idx != -1, "_exc_is_auth not found in streaming.py exception path"
        assert rl_idx < ae_idx, (
            "_exc_is_rate_limit check should precede _exc_is_auth — "
            "rate limit errors must not be mistaken for auth errors"
        )


# ── 2. static/ui.js: _checkProviderMismatch() ───────────────────────────────

# ── 3. static/messages.js: apperror handler ─────────────────────────────────

# ── 4. static/i18n.js: all locales ───────────────────────────────────────────

# ── 5. static/boot.js: dropdown change handler ──────────────────────────────

# ── 6. /api/models: active_provider in response ──────────────────────────────

def test_api_models_includes_active_provider():
    """/api/models must include 'active_provider' key in response."""
    with urllib.request.urlopen(BASE + "/api/models", timeout=10) as r:
        data = json.loads(r.read())
    # active_provider can be None/null but the key must exist
    assert "active_provider" in data, (
        "/api/models response missing 'active_provider' field — "
        "frontend needs this to detect provider mismatches"
    )


def test_codex_provider_qualified_model_routes_to_codex_not_openrouter():
    """@openai-codex:gpt-5.5 must route through OpenAI Codex, not OpenRouter."""
    import api.config as config

    old_cfg = dict(config.cfg)
    config.cfg["model"] = {
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
    }
    try:
        model, provider, base_url = config.resolve_model_provider(
            "@openai-codex:gpt-5.5"
        )
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)

    assert model == "gpt-5.5"
    assert provider == "openai-codex"
    assert provider != "openrouter"
    assert base_url is None


def test_default_model_save_persists_codex_provider_for_qualified_model(tmp_path, monkeypatch):
    """Saving @openai-codex:gpt-5.5 must persist model.provider=openai-codex."""
    import yaml
    import api.config as config

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "model:\n"
        "  provider: openrouter\n"
        "  default: openai/gpt-5.4\n"
        "  base_url: https://openrouter.ai/api/v1\n",
        encoding="utf-8",
    )
    old_cfg = dict(config.cfg)
    old_mtime = config._cfg_mtime
    monkeypatch.setattr(config, "_get_config_path", lambda: config_file)
    config.cfg["model"] = {
        "provider": "openrouter",
        "default": "openai/gpt-5.4",
        "base_url": "https://openrouter.ai/api/v1",
    }
    config._cfg_mtime = config_file.stat().st_mtime
    try:
        result = config.set_hermes_default_model("@openai-codex:gpt-5.5")
        saved = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)
        config._cfg_mtime = old_mtime
        config.invalidate_models_cache()

    assert result["ok"] is True
    assert result["model"] == "gpt-5.5"
    assert saved["model"]["default"] == "gpt-5.5"
    assert saved["model"]["provider"] == "openai-codex"
    assert saved["model"].get("base_url") != "https://openrouter.ai/api/v1"


def test_active_codex_at_provider_session_model_preserved(monkeypatch):
    """@openai-codex:gpt-5.5 session selections must keep their provider hint."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
                },
                {
                    "provider": "OpenRouter",
                    "provider_id": "openrouter",
                    "models": [{"id": "openai/gpt-5.5", "label": "GPT-5.5"}],
                },
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "@openai-codex:gpt-5.5"
    )

    assert changed is False
    assert effective == "@openai-codex:gpt-5.5"


def test_bare_codex_gpt_session_model_gets_separate_provider_context(monkeypatch):
    """A bare GPT model under active Codex stays bare and carries model_provider."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
                },
                {
                    "provider": "OpenRouter",
                    "provider_id": "openrouter",
                    "models": [{"id": "openai/gpt-5.5", "label": "GPT-5.5"}],
                },
            ],
        },
    )

    effective, provider, changed = routes._resolve_compatible_session_model_state("gpt-5.5")

    assert changed is False
    assert effective == "gpt-5.5"
    assert provider == "openai-codex"


def test_session_model_normalizer_keeps_bare_codex_model_and_saves_provider(monkeypatch):
    """Write-path normalization must persist model_provider without adding @."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
                },
            ],
        },
    )

    save_calls = []

    class DummySession:
        def __init__(self):
            self.model = "gpt-5.5"
            self.model_provider = None

        def save(self, touch_updated_at=True):
            save_calls.append(touch_updated_at)

    session = DummySession()
    effective = routes._normalize_session_model_in_place(session)

    assert effective == "gpt-5.5"
    assert session.model == "gpt-5.5"
    assert session.model_provider == "openai-codex"
    assert save_calls == [False]


def test_bare_codex_gpt_runtime_bridge_routes_to_codex(monkeypatch):
    """Bare model + model_provider=openai-codex must route Codex at runtime."""
    import api.config as config

    old_cfg = dict(config.cfg)
    config.cfg["model"] = {
        "provider": "openrouter",
        "default": "openai/gpt-5.4",
        "base_url": "https://openrouter.ai/api/v1",
    }
    try:
        runtime_model = config.model_with_provider_context(
            "gpt-5.5",
            "openai-codex",
        )
        model, provider, base_url = config.resolve_model_provider(runtime_model)
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)

    assert runtime_model == "@openai-codex:gpt-5.5"
    assert model == "gpt-5.5"
    assert provider == "openai-codex"
    assert base_url is None


def test_non_openrouter_slash_model_provider_context_stays_unqualified():
    """Portal/custom slash IDs must not be blindly wrapped as @provider:model."""
    import api.config as config

    runtime_model = config.model_with_provider_context(
        "anthropic/claude-sonnet-4.6",
        "nous",
    )

    assert runtime_model == "anthropic/claude-sonnet-4.6"


def test_api_session_new_persists_model_provider_context():
    """POST /api/session/new returns compact session model_provider metadata."""
    created, status = _post(
        "/api/session/new",
        {"model": "gpt-5.5", "model_provider": "openai-codex"},
    )

    assert status == 200
    assert created["session"]["model"] == "gpt-5.5"
    assert created["session"]["model_provider"] == "openai-codex"


def test_explicit_openrouter_selection_supported_with_codex_base_url():
    """OpenRouter slash and @openrouter selections must remain routable."""
    import api.config as config

    old_cfg = dict(config.cfg)
    config.cfg["model"] = {
        "provider": "openai-codex",
        "default": "gpt-5.5",
        "base_url": "https://chatgpt.com/backend-api/codex",
    }
    try:
        slash_model, slash_provider, slash_base_url = config.resolve_model_provider(
            "openai/gpt-5.5"
        )
        at_model, at_provider, at_base_url = config.resolve_model_provider(
            "@openrouter:openai/gpt-5.5"
        )
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)

    assert slash_model == "openai/gpt-5.5"
    assert slash_provider == "openrouter"
    assert slash_base_url is None
    assert at_model == "openai/gpt-5.5"
    assert at_provider == "openrouter"
    assert at_base_url is None


def test_real_provider_custom_base_url_slash_model_stays_on_configured_endpoint():
    """A real-provider proxy base_url must not be silently rerouted to OpenRouter."""
    import api.config as config

    old_cfg = dict(config.cfg)
    config.cfg["model"] = {
        "provider": "openai",
        "default": "google/gemma-4-26b-a4b",
        "base_url": "http://proxy.local/v1",
    }
    try:
        model, provider, base_url = config.resolve_model_provider(
            "google/gemma-4-26b-a4b"
        )
    finally:
        config.cfg.clear()
        config.cfg.update(old_cfg)

    assert model == "gemma-4-26b-a4b"
    assert provider == "openai"
    assert provider != "openrouter"
    assert base_url == "http://proxy.local/v1"


def test_bare_gemini_session_model_normalizes_to_active_provider_default(monkeypatch):
    """Persisted bare Gemini IDs must not survive a provider switch."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "gemini-3.1-pro-preview"
    )

    assert changed is True
    assert effective == "gpt-5.4-mini"


def test_prefixed_google_session_model_normalizes_to_active_provider_default(monkeypatch):
    """Persisted provider-prefixed Gemini IDs must normalize too."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "google/gemini-3.1-pro-preview"
    )

    assert changed is True
    assert effective == "gpt-5.4-mini"


def test_legacy_at_provider_session_model_normalizes_when_provider_hidden(monkeypatch):
    """Old @provider:model session values must not bypass stale-model recovery."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
                },
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "@copilot:gpt-5.5"
    )

    assert changed is True
    assert effective == "gpt-5.5"


def test_active_at_provider_session_model_preserved_with_hint(monkeypatch):
    """@active-provider:model must be preserved — stripping the prefix breaks duplicate-ID routing.

    Before #1253 was fixed, this path stripped the @provider: prefix and returned
    the bare model ID. That caused the picker to snap to the first matching provider
    (not the explicitly selected one) on the next send, and the agent to run on the
    wrong provider. The fix returns the full @provider:model unchanged so
    resolve_model_provider() can route through the correct provider.
    """
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.4-mini", "label": "GPT-5.4 Mini"}],
                },
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "@openai-codex:gpt-5.4-mini"
    )

    # Must preserve the full @provider:model so resolve_model_provider() routes
    # through openai-codex, not through whatever provider happens to be first.
    assert changed is False
    assert effective == "@openai-codex:gpt-5.4-mini"


def test_routable_non_active_at_provider_session_model_is_preserved(monkeypatch):
    """Visible cross-provider dropdown selections must keep their provider hint."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
                },
                {
                    "provider": "GitHub Copilot",
                    "provider_id": "copilot",
                    "models": [{"id": "@copilot:gpt-5.4", "label": "GPT-5.4"}],
                },
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "@copilot:gpt-5.4"
    )

    assert changed is False
    assert effective == "@copilot:gpt-5.4"


def test_issue1253_duplicate_model_id_active_provider_hint_preserved(monkeypatch):
    """@provider:model where hint matches active provider must survive _resolve_compatible_session_model.

    Regression test for #1253: when two providers both expose the same bare model ID
    (e.g. both custom:edith and openai both expose 'gpt-5.4'), the picker stores the
    selection as @custom:gpt-5.4. On chat/start that value must be returned unchanged
    so resolve_model_provider() routes to 'custom', not to the default provider.

    Before the fix, hint_matches_active=True caused the prefix to be stripped:
      '@custom:gpt-5.4' → ('gpt-5.4', True)
    which then got written back to disk and sent as effective_model, snapping the
    picker to the first (wrong) provider.
    """
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "custom",
            "default_model": "gpt-5.4",
            "groups": [
                {
                    "provider": "Custom",
                    "provider_id": "custom",
                    "models": [{"id": "@custom:edith", "label": "Edith"}],
                },
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.4", "label": "GPT-5.4"}],
                },
            ],
        },
    )

    # User selected the custom:edith model — explicit @provider:model form.
    effective, changed = routes._resolve_compatible_session_model("@custom:edith")

    # Must NOT be stripped to 'edith' — that would route to the default provider.
    assert changed is False, (
        f"_resolve_compatible_session_model must not strip @custom:edith "
        f"(got effective='{effective}', changed={changed})"
    )
    assert effective == "@custom:edith", (
        f"expected '@custom:edith', got '{effective}'"
    )


def test_named_custom_provider_hint_with_colon_is_preserved(monkeypatch):
    """@custom:name:model must survive chat/start normalization for WebUI routing."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "deepseek",
            "default_model": "deepseek-v4-pro",
            "groups": [
                {
                    "provider": "sub2api",
                    "provider_id": "custom:sub2api",
                    "models": [
                        {
                            "id": "@custom:sub2api:gpt-5.4-mini",
                            "label": "GPT 5.4 Mini",
                        }
                    ],
                },
                {
                    "provider": "DeepSeek",
                    "provider_id": "deepseek",
                    "models": [
                        {
                            "id": "deepseek-v4-pro",
                            "label": "DeepSeek V4 Pro",
                        }
                    ],
                },
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "@custom:sub2api:gpt-5.4-mini"
    )

    assert changed is False
    assert effective == "@custom:sub2api:gpt-5.4-mini"


def test_issue1734_stale_openai_slash_session_model_repairs_to_codex(monkeypatch):
    """Legacy openai/... session IDs must not route to OpenRouter when Codex is active."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
                },
                {
                    "provider": "OpenRouter",
                    "provider_id": "openrouter",
                    "models": [{"id": "openai/gpt-5.4-mini", "label": "GPT-5.4 Mini"}],
                },
            ],
        },
    )

    effective, provider, changed = routes._resolve_compatible_session_model_state(
        "openai/gpt-5.4-mini",
        None,
    )

    assert changed is True
    assert effective == "gpt-5.5"
    assert provider == "openai-codex"


def test_issue1734_chat_start_persists_repaired_codex_provider(monkeypatch):
    """/api/chat/start should save repaired Codex model state before spawning."""
    import contextlib
    import io
    import json
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
                },
            ],
        },
    )

    save_calls = []

    class DummySession:
        session_id = "issue1734_session"
        workspace = "/tmp/hermes-webui-test"
        model = "openai/gpt-5.4-mini"
        model_provider = None
        active_stream_id = None
        pending_user_message = None
        pending_attachments = []
        pending_started_at = None
        messages = [{"role": "user", "content": "old"}]
        context_messages = []

        def save(self, touch_updated_at=True):
            save_calls.append(
                {
                    "touch_updated_at": touch_updated_at,
                    "model": self.model,
                    "model_provider": self.model_provider,
                    "pending_user_message": self.pending_user_message,
                }
            )

    captured_thread = {}

    class FakeThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None):
            captured_thread.update(
                {"target": target, "args": args, "kwargs": kwargs or {}, "daemon": daemon}
            )

        def start(self):
            captured_thread["started"] = True

    class FakeHandler:
        def __init__(self):
            self.wfile = io.BytesIO()
            self.status = None
            self.sent_headers = {}

        def send_response(self, status):
            self.status = status

        def send_header(self, key, value):
            self.sent_headers[key] = value

        def end_headers(self):
            pass

    session = DummySession()
    monkeypatch.setattr(routes, "get_session", lambda sid: session)
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value: value)
    monkeypatch.setattr(routes, "_get_session_agent_lock", lambda sid: contextlib.nullcontext())
    monkeypatch.setattr(routes, "set_last_workspace", lambda workspace: None)
    monkeypatch.setattr(routes, "create_stream_channel", lambda: object())
    monkeypatch.setattr(routes.threading, "Thread", FakeThread)

    handler = FakeHandler()
    routes._handle_chat_start(
        handler,
        {"session_id": session.session_id, "message": "new turn"},
    )
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))

    assert handler.status == 200
    assert payload["effective_model"] == "gpt-5.5"
    assert payload["effective_model_provider"] == "openai-codex"
    assert session.model == "gpt-5.5"
    assert session.model_provider == "openai-codex"
    assert captured_thread["args"][2] == "gpt-5.5"
    assert captured_thread["kwargs"]["model_provider"] == "openai-codex"
    assert save_calls[-1]["model_provider"] == "openai-codex"


def test_stale_at_provider_model_falls_back_when_family_mismatches(monkeypatch):
    """Unroutable @provider:model should not invent a bare model for another family."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.5",
            "groups": [
                {
                    "provider": "OpenAI Codex",
                    "provider_id": "openai-codex",
                    "models": [{"id": "gpt-5.5", "label": "GPT-5.5"}],
                },
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "@copilot:claude-opus-4.6"
    )

    assert changed is True
    assert effective == "gpt-5.5"


def test_google_active_provider_keeps_valid_gemini_session_model(monkeypatch):
    """A Google-configured session must keep its Gemini model."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "google",
            "default_model": "gemini-3.1-pro-preview",
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "gemini-3.1-pro-preview"
    )

    assert changed is False
    assert effective == "gemini-3.1-pro-preview"


def test_session_model_normalizer_persists_corrected_model(monkeypatch):
    """Write-path normalization should still persist corrected models."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    save_calls = []

    class DummySession:
        def __init__(self):
            self.model = "gemini-3.1-pro-preview"

        def save(self, touch_updated_at=True):
            save_calls.append(touch_updated_at)

    session = DummySession()
    effective = routes._normalize_session_model_in_place(session)

    assert effective == "gpt-5.4-mini"
    assert session.model == "gpt-5.4-mini"
    assert save_calls == [False]


def test_session_model_display_resolver_is_read_only(monkeypatch):
    """Read-path model resolution must not mutate or save the session."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    save_calls = []

    class DummySession:
        def __init__(self):
            self.model = "gemini-3.1-pro-preview"

        def save(self, touch_updated_at=True):
            save_calls.append(touch_updated_at)

    session = DummySession()
    effective = routes._resolve_effective_session_model_for_display(session)

    assert effective == "gpt-5.4-mini"
    assert session.model == "gemini-3.1-pro-preview"
    assert save_calls == []


def test_api_session_is_side_effect_free_for_stale_models():
    """GET /api/session must not rewrite the session file on first open (#845)."""
    created, status = _post("/api/session/new", {})
    assert status == 200
    sid = created["session"]["session_id"]

    session_path = TEST_STATE_DIR / "sessions" / f"{sid}.json"
    # POST /api/session/new no longer eagerly writes empty sessions to disk
    # (#1171 follow-up). Materialise the file from the API response so the
    # rest of this test, which checks that GET is side-effect-free against
    # an on-disk session with a stale model, has a file to work with.
    if not session_path.exists():
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            json.dumps(created["session"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    session_data = json.loads(session_path.read_text(encoding="utf-8"))
    stale_model = "google/gemini-3.1-pro-preview"
    session_data["model"] = stale_model
    before = json.dumps(session_data, ensure_ascii=False, indent=2)
    session_path.write_text(before, encoding="utf-8")

    with urllib.request.urlopen(
        BASE + f"/api/session?session_id={sid}", timeout=10
    ) as r:
        payload = json.loads(r.read())

    after = session_path.read_text(encoding="utf-8")
    assert payload["session"]["model"], "response should still expose an effective display model"
    assert payload["session"]["model"] != stale_model, (
        "response model should be compatibility-normalized on the read path"
    )
    assert after == before, (
        "GET /api/session must return an effective model for display without "
        "rewriting the session file on disk"
    )


# ── Model switch toast (#419) ─────────────────────────────────────────────────

def test_unknown_prefix_model_passes_through_unchanged(monkeypatch):
    """Models with unknown/custom prefixes must never be stripped — regression test for #751."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    for custom_model in (
        "custom-provider/test-model-999",
        "test/import-model",
        "my-local-llm/variant-1",
        "lmstudio-community/Qwen2.5-Coder-7B-Instruct-GGUF",
    ):
        effective, changed = routes._resolve_compatible_session_model(custom_model)
        assert changed is False, (
            f"Model '{custom_model}' has an unknown prefix and must pass through unchanged, "
            f"but _resolve_compatible_session_model returned changed=True (effective='{effective}')"
        )
        assert effective == custom_model, (
            f"Expected '{custom_model}', got '{effective}'"
        )


def test_empty_model_session_does_not_trigger_save(monkeypatch):
    """Sessions with no model stored must not trigger session.save() — index rebuild is expensive."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openai-codex",
            "default_model": "gpt-5.4-mini",
        },
    )

    save_calls = []

    class DummySession:
        def __init__(self):
            self.model = None  # no model stored

        def save(self, touch_updated_at=True):
            save_calls.append(touch_updated_at)

    session = DummySession()
    effective = routes._normalize_session_model_in_place(session)

    # Must return the default, but must NOT write to disk
    assert effective == "gpt-5.4-mini"
    assert save_calls == [], (
        "_normalize_session_model_in_place must not call session.save() when "
        "the session has no stored model — no correction needed, just a fallback."
    )


# ── Issue #829: stale cross-provider model on custom_providers-only setup ─────

def test_stale_openai_model_cleared_for_custom_only_provider(monkeypatch):
    """A stale openai/... session model must be cleared when active provider is
    'custom' and no catalog group can route the openai prefix (#829)."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "custom",
            "default_model": "",
            "groups": [
                {"provider": "Agent37", "provider_id": "custom:agent37",
                 "models": [{"id": "agent37/default", "label": "default"}]},
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "openai/gpt-5.4-mini"
    )

    # No routable group for openai/ — should clear to default (empty → model itself
    # only if no default available, which means changed=False when default_model="")
    # When default_model is empty, we can't clear — preserve and return False
    assert changed is False
    assert effective == "openai/gpt-5.4-mini"


def test_stale_openai_model_cleared_for_custom_provider_with_default(monkeypatch):
    """When active_provider='custom', no openrouter group, and default_model is
    configured, stale openai/... model should be cleared to default (#829)."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "custom",
            "default_model": "agent37/default",
            "groups": [
                {"provider": "Agent37", "provider_id": "custom:agent37",
                 "models": [{"id": "agent37/default", "label": "default"}]},
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "openai/gpt-5.4-mini"
    )

    assert changed is True
    assert effective == "agent37/default"


def test_openrouter_model_preserved_when_openrouter_group_present(monkeypatch):
    """When active_provider='openrouter' and openrouter group exists,
    openai/... model IDs must pass through unchanged — they are routable (#829)."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "openrouter",
            "default_model": "openai/gpt-5.4-mini",
            "groups": [
                {"provider": "OpenRouter", "provider_id": "openrouter",
                 "models": [{"id": "openai/gpt-5.4-mini", "label": "GPT-5.4 Mini"}]},
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "openai/gpt-5.4-mini"
    )

    assert changed is False
    assert effective == "openai/gpt-5.4-mini"


def test_custom_namespace_model_always_preserved_on_custom_provider(monkeypatch):
    """Model IDs with 'custom/' prefix must always pass through unchanged even
    when active_provider='custom' (#829)."""
    import api.routes as routes

    monkeypatch.setattr(
        routes,
        "get_available_models",
        lambda: {
            "active_provider": "custom",
            "default_model": "agent37/default",
            "groups": [
                {"provider": "Agent37", "provider_id": "custom:agent37",
                 "models": [{"id": "agent37/default", "label": "default"}]},
            ],
        },
    )

    effective, changed = routes._resolve_compatible_session_model(
        "custom/my-local-llm"
    )

    assert changed is False
    assert effective == "custom/my-local-llm"
