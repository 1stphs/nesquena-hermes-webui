"""Script-level validation for user provider resolver behavior."""

from __future__ import annotations

import sys
import os
import json
import queue
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.user_provider as user_provider


X_USER_ID_ENV = user_provider.X_USER_ID_CONTEXT_ENABLE_ENV
LEGACY_X_USER_ID_ENV = user_provider.UNTRUSTED_CONTEXT_ENABLE_ENV
LEGACY_NOCOBASE_AUTH_ENV = user_provider.LEGACY_NOCOBASE_AUTH_ENABLE_ENV
REPO_ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = REPO_ROOT / "hermes-agent-src"
FAKE_PROVIDER_KEY = "sk-user-provider-local-e2e-secret"


class Handler:
    def __init__(self, headers=None):
        self.headers = headers or {}


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def _active_resolution(user_id, provider_id, *, model, api_key):
    return user_provider.UserProviderResolution(
        status="active",
        reason="active_provider",
        user_id=user_id,
        provider={
            "id": provider_id,
            "user_id": user_id,
            "name": f"Provider {provider_id}",
            "provider_slug": f"user-provider-{provider_id}",
            "base_url": "https://example.invalid/v1",
            "model_name": model,
            "api_mode": "codex_responses",
            "thinking_level": "low",
            "api_key": api_key,
            "status": "enabled",
            "updatedAt": "2026-06-01T00:00:00Z",
        },
    )


def validate_default_disabled_models_payload_does_not_read_provider() -> None:
    original_resolve = user_provider.resolve_user_provider
    original_fetch = user_provider.fetch_provider_models
    previous_env = os.environ.pop(X_USER_ID_ENV, None)
    previous_legacy_env = os.environ.pop(LEGACY_X_USER_ID_ENV, None)
    previous_nocobase_auth_env = os.environ.pop(LEGACY_NOCOBASE_AUTH_ENV, None)
    user_provider.clear_user_provider_models_cache()

    def fail_lookup(_user_id):
        raise AssertionError("resolve_user_provider must not run while disabled")

    def fail_fetch(_provider):
        raise AssertionError("fetch_provider_models must not run while disabled")

    try:
        user_provider.resolve_user_provider = fail_lookup
        user_provider.fetch_provider_models = fail_fetch
        payload = user_provider.build_user_provider_models_payload(
            None,
            lambda: {
                "active_provider": "default",
                "default_model": "default-model",
                "groups": [{"provider": "Default", "provider_id": "default", "models": []}],
            },
        )
    finally:
        user_provider.resolve_user_provider = original_resolve
        user_provider.fetch_provider_models = original_fetch
        _restore_env(X_USER_ID_ENV, previous_env)
        _restore_env(LEGACY_X_USER_ID_ENV, previous_legacy_env)
        _restore_env(LEGACY_NOCOBASE_AUTH_ENV, previous_nocobase_auth_env)

    assert payload["active_provider"] == "default"
    assert payload["provider_resolution"]["status"] == "disabled"
    assert payload["provider_resolution"]["reason"] == "x_user_id_context_disabled"
    assert payload["provider_resolution"]["fallback"] is True


def validate_x_user_id_context_enables_runtime_lookup() -> None:
    original_resolve = user_provider.resolve_user_provider
    previous_env = os.environ.get(X_USER_ID_ENV)
    previous_legacy_env = os.environ.pop(LEGACY_X_USER_ID_ENV, None)
    previous_nocobase_auth_env = os.environ.pop(LEGACY_NOCOBASE_AUTH_ENV, None)
    os.environ[X_USER_ID_ENV] = "1"
    user_provider.clear_user_provider_models_cache()
    captured = {}

    def resolve_none(user_id):
        captured["user_id"] = user_id
        return user_provider.UserProviderResolution(
            status="none",
            reason="no_provider",
            user_id=user_id,
        )

    try:
        user_provider.resolve_user_provider = resolve_none
        payload = user_provider.build_user_provider_models_payload(
            "u1",
            lambda: {
                "active_provider": "default",
                "default_model": "default-model",
                "groups": [{"provider": "Default", "provider_id": "default", "models": []}],
            },
        )
    finally:
        user_provider.resolve_user_provider = original_resolve
        _restore_env(X_USER_ID_ENV, previous_env)
        _restore_env(LEGACY_X_USER_ID_ENV, previous_legacy_env)
        _restore_env(LEGACY_NOCOBASE_AUTH_ENV, previous_nocobase_auth_env)

    assert payload["active_provider"] == "default"
    assert payload["provider_resolution"]["status"] == "none"
    assert captured["user_id"] == "u1"


def validate_lookup_fallback_and_redaction_when_x_user_id_enabled() -> None:
    original_resolve = user_provider.resolve_user_provider
    previous_env = os.environ.get(X_USER_ID_ENV)
    previous_legacy_env = os.environ.pop(LEGACY_X_USER_ID_ENV, None)
    previous_nocobase_auth_env = os.environ.pop(LEGACY_NOCOBASE_AUTH_ENV, None)
    os.environ[X_USER_ID_ENV] = "1"
    user_provider.clear_user_provider_models_cache()

    def fail_lookup(user_id):
        return user_provider.UserProviderResolution(
            status="lookup_failed",
            reason="nocobase_lookup_failed",
            user_id=user_id,
            error="401 INVALID_TOKEN sk-test-secret-1234567890",
        )

    try:
        user_provider.resolve_user_provider = fail_lookup
        payload = user_provider.build_user_provider_models_payload(
            "u1",
            lambda: {
                "active_provider": "default",
                "default_model": "default-model",
                "groups": [{"provider": "Default", "provider_id": "default", "models": []}],
            },
        )
    finally:
        user_provider.resolve_user_provider = original_resolve
        _restore_env(X_USER_ID_ENV, previous_env)
        _restore_env(LEGACY_X_USER_ID_ENV, previous_legacy_env)
        _restore_env(LEGACY_NOCOBASE_AUTH_ENV, previous_nocobase_auth_env)

    assert payload["active_provider"] == "default"
    assert payload["provider_resolution"]["status"] == "lookup_failed"
    assert payload["provider_resolution"]["fallback"] is True
    assert "sk-test-secret-1234567890" not in payload["provider_resolution"]["detail"]


def validate_models_cache_isolated_by_user_and_provider() -> None:
    original_resolve = user_provider.resolve_user_provider
    original_fetch = user_provider.fetch_provider_models
    previous_env = os.environ.get(X_USER_ID_ENV)
    previous_legacy_env = os.environ.pop(LEGACY_X_USER_ID_ENV, None)
    previous_nocobase_auth_env = os.environ.pop(LEGACY_NOCOBASE_AUTH_ENV, None)
    os.environ[X_USER_ID_ENV] = "1"
    user_provider.clear_user_provider_models_cache()
    resolutions = {
        "u1": _active_resolution("u1", "p1", model="model-a", api_key="sk-user-one-1234567890"),
        "u2": _active_resolution("u2", "p2", model="model-b", api_key="sk-user-two-1234567890"),
    }

    try:
        user_provider.resolve_user_provider = lambda user_id: resolutions[user_id]
        user_provider.fetch_provider_models = lambda provider: (
            [{"id": provider["model_name"], "label": provider["model_name"]}],
            "",
        )
        payload_one = user_provider.build_user_provider_models_payload("u1", lambda: {})
        payload_two = user_provider.build_user_provider_models_payload("u2", lambda: {})
    finally:
        user_provider.resolve_user_provider = original_resolve
        user_provider.fetch_provider_models = original_fetch
        _restore_env(X_USER_ID_ENV, previous_env)
        _restore_env(LEGACY_X_USER_ID_ENV, previous_legacy_env)
        _restore_env(LEGACY_NOCOBASE_AUTH_ENV, previous_nocobase_auth_env)

    assert payload_one["active_provider"] == "user-provider-p1"
    assert payload_one["default_model"] == "model-a"
    assert payload_two["active_provider"] == "user-provider-p2"
    assert payload_two["default_model"] == "model-b"


def validate_runtime_signature_omits_api_key() -> None:
    resolution = _active_resolution(
        "u1",
        "p1",
        model="model-a",
        api_key="sk-user-one-1234567890",
    )
    signature = user_provider.provider_runtime_signature(resolution)
    assert signature["status"] == "active"
    assert signature["provider_id"] == "p1"
    assert "api_key" not in signature
    assert "sk-user-one-1234567890" not in str(signature)


def validate_enabled_status_selects_latest_provider_without_is_default() -> None:
    original_candidates = user_provider._provider_candidates_for_user
    original_validate_base_url = user_provider._validate_base_url
    records = [
        {
            "id": "old-enabled",
            "user_id": "u1",
            "name": "Old Enabled",
            "provider_slug": "old-enabled",
            "base_url": "https://old.example.invalid/v1",
            "model_name": "old-model",
            "api_mode": "codex_responses",
            "thinking_level": "low",
            "api_key": "sk-old-provider-1234567890",
            "status": "enabled",
            "updatedAt": "2026-06-01T00:00:00Z",
        },
        {
            "id": "new-enabled",
            "user_id": "u1",
            "name": "New Enabled",
            "provider_slug": "new-enabled",
            "base_url": "https://new.example.invalid/v1",
            "model_name": "new-model",
            "api_mode": "codex_responses",
            "thinking_level": "low",
            "api_key": "sk-new-provider-1234567890",
            "status": "enabled",
            "updatedAt": "2026-06-02T00:00:00Z",
        },
        {
            "id": "latest-disabled",
            "user_id": "u1",
            "name": "Latest Disabled",
            "provider_slug": "latest-disabled",
            "base_url": "https://disabled.example.invalid/v1",
            "model_name": "disabled-model",
            "api_mode": "codex_responses",
            "thinking_level": "low",
            "api_key": "sk-disabled-provider-1234567890",
            "status": "disabled",
            "updatedAt": "2026-06-03T00:00:00Z",
        },
    ]

    try:
        user_provider._provider_candidates_for_user = lambda user_id: records
        user_provider._validate_base_url = lambda base_url: str(base_url or "").strip().rstrip("/")
        resolution = user_provider.resolve_user_provider("u1")
    finally:
        user_provider._provider_candidates_for_user = original_candidates
        user_provider._validate_base_url = original_validate_base_url

    assert resolution.status == "active"
    assert resolution.reason == "active_provider"
    assert resolution.provider["id"] == "new-enabled"
    assert "is_default" not in resolution.provider


def validate_legacy_nocobase_auth_env_enables_x_user_id_runtime() -> None:
    original_resolve = user_provider.resolve_user_provider
    previous_env = os.environ.pop(X_USER_ID_ENV, None)
    previous_legacy_env = os.environ.pop(LEGACY_X_USER_ID_ENV, None)
    previous_nocobase_auth_env = os.environ.get(LEGACY_NOCOBASE_AUTH_ENV)
    os.environ[LEGACY_NOCOBASE_AUTH_ENV] = "1"
    user_provider.clear_user_provider_models_cache()
    captured = {}

    def resolve_none(user_id):
        captured["user_id"] = user_id
        return user_provider.UserProviderResolution(
            status="none",
            reason="no_provider",
            user_id=user_id,
        )

    try:
        user_provider.resolve_user_provider = resolve_none
        payload = user_provider.build_user_provider_models_payload(
            "legacy-user",
            lambda: {
                "active_provider": "default",
                "default_model": "default-model",
                "groups": [{"provider": "Default", "provider_id": "default", "models": []}],
            },
        )
    finally:
        user_provider.resolve_user_provider = original_resolve
        _restore_env(X_USER_ID_ENV, previous_env)
        _restore_env(LEGACY_X_USER_ID_ENV, previous_legacy_env)
        _restore_env(LEGACY_NOCOBASE_AUTH_ENV, previous_nocobase_auth_env)

    assert payload["provider_resolution"]["status"] == "none"
    assert captured["user_id"] == "legacy-user"


def validate_private_and_local_base_urls_are_rejected() -> None:
    blocked = [
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
        "http://10.0.0.1/v1",
        "http://172.16.0.1/v1",
        "http://192.168.1.10/v1",
        "http://169.254.169.254/latest",
        "http://0.0.0.0/v1",
        "http://224.0.0.1/v1",
    ]
    for url in blocked:
        try:
            user_provider._validate_base_url(url)
        except ValueError:
            continue
        raise AssertionError(f"blocked base_url was accepted: {url}")


def validate_dns_resolution_to_private_ip_is_rejected() -> None:
    original_getaddrinfo = user_provider.socket.getaddrinfo

    def fake_getaddrinfo(*_args, **_kwargs):
        return [(user_provider.socket.AF_INET, user_provider.socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))]

    try:
        user_provider.socket.getaddrinfo = fake_getaddrinfo
        try:
            user_provider._validate_base_url("https://provider.example/v1")
        except ValueError:
            pass
        else:
            raise AssertionError("DNS results resolving to private IPs must be rejected")
    finally:
        user_provider.socket.getaddrinfo = original_getaddrinfo


def validate_provider_key_redaction_is_forced_when_global_redaction_disabled() -> None:
    original_redact = user_provider._redact_text
    secret = "sk-current-provider-secret-1234567890"

    try:
        user_provider._redact_text = lambda text: str(text)
        redacted = user_provider._redact_error(f"provider failed with Bearer {secret}", secret)
    finally:
        user_provider._redact_text = original_redact

    assert secret not in redacted
    assert "***" in redacted


def validate_route_models_does_not_require_user_context() -> None:
    import urllib.parse
    import api.routes as routes
    import api.routes_dispatcher as dispatcher

    original_j = routes.j
    original_models = routes.get_available_models
    original_resolve = user_provider.resolve_user_provider
    previous_env = os.environ.pop(X_USER_ID_ENV, None)
    previous_legacy_env = os.environ.pop(LEGACY_X_USER_ID_ENV, None)
    previous_nocobase_auth_env = os.environ.pop(LEGACY_NOCOBASE_AUTH_ENV, None)
    captured = {}

    def capture_j(_handler, payload, status=200, extra_headers=None):
        captured["payload"] = payload
        captured["status"] = status
        return True

    def fail_lookup(_user_id):
        raise AssertionError("route must not resolve user provider while disabled")

    try:
        routes.j = capture_j
        routes.get_available_models = lambda: {
            "active_provider": "default",
            "default_model": "default-model",
            "groups": [{"provider": "Default", "provider_id": "default", "models": []}],
        }
        user_provider.resolve_user_provider = fail_lookup
        assert dispatcher.dispatch_get(Handler(), urllib.parse.urlparse("/api/models")) is True
    finally:
        routes.j = original_j
        routes.get_available_models = original_models
        user_provider.resolve_user_provider = original_resolve
        _restore_env(X_USER_ID_ENV, previous_env)
        _restore_env(LEGACY_X_USER_ID_ENV, previous_legacy_env)
        _restore_env(LEGACY_NOCOBASE_AUTH_ENV, previous_nocobase_auth_env)

    assert captured["status"] == 200
    payload = captured["payload"]
    assert payload["active_provider"] == "default"
    assert payload["provider_resolution"]["status"] == "disabled"


def validate_x_user_id_header_sets_current_user() -> None:
    user_id = user_provider.current_user_id_from_handler(Handler({"X-User-Id": "user-a"}))
    assert user_id == "user-a"


def validate_x_user_id_cookie_sets_current_user() -> None:
    user_id = user_provider.current_user_id_from_handler(
        Handler({"Cookie": "theme=light; X-User-Id=user-cookie"})
    )
    assert user_id == "user-cookie"


def validate_x_user_id_header_cookie_mismatch_rejected() -> None:
    try:
        user_provider.current_user_id_from_handler(
            Handler({"X-User-Id": "user-a", "Cookie": "X-User-Id=user-b"})
        )
    except user_provider.UserProviderAuthError as exc:
        assert exc.status == 400
        assert exc.code == "user_context_mismatch"
    else:
        raise AssertionError("mismatched X-User-Id header/cookie must be rejected")


def validate_x_user_id_missing_rejected() -> None:
    try:
        user_provider.current_user_id_from_handler(Handler())
    except user_provider.UserProviderAuthError as exc:
        assert exc.status == 400
        assert exc.code == "missing_user_context"
    else:
        raise AssertionError("missing X-User-Id context must be rejected")


def _assert_agent_src_available() -> None:
    if not (AGENT_SRC / "run_agent.py").exists():
        raise RuntimeError(
            "hermes-agent-src/run_agent.py is required for the real agent e2e smoke"
        )


class _MockResponsesHandler(BaseHTTPRequestHandler):
    server_version = "HermesUserProviderSmoke/1.0"

    def do_POST(self) -> None:
        if self.path != "/v1/responses":
            self.send_error(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or "0")
        body = self.rfile.read(length) if length else b""
        self.server.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization", ""),
                "body": body.decode("utf-8", errors="replace"),
            }
        )
        message_id = "msg_user_provider_smoke"
        response_id = "resp_user_provider_smoke"
        text = "local provider smoke response"
        completed = {
            "id": response_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "model": "smoke-model",
            "output": [
                {
                    "id": message_id,
                    "type": "message",
                    "status": "completed",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
            "usage": {"input_tokens": 1, "output_tokens": 4, "total_tokens": 5},
        }
        events = [
            (
                "response.created",
                {
                    "type": "response.created",
                    "response": {**completed, "status": "in_progress", "output": []},
                },
            ),
            (
                "response.output_item.added",
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": {
                        "id": message_id,
                        "type": "message",
                        "status": "in_progress",
                        "role": "assistant",
                        "content": [],
                    },
                },
            ),
            (
                "response.content_part.added",
                {
                    "type": "response.content_part.added",
                    "item_id": message_id,
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": ""},
                },
            ),
            (
                "response.output_text.delta",
                {
                    "type": "response.output_text.delta",
                    "item_id": message_id,
                    "output_index": 0,
                    "content_index": 0,
                    "delta": text,
                },
            ),
            (
                "response.output_text.done",
                {
                    "type": "response.output_text.done",
                    "item_id": message_id,
                    "output_index": 0,
                    "content_index": 0,
                    "text": text,
                },
            ),
            (
                "response.content_part.done",
                {
                    "type": "response.content_part.done",
                    "item_id": message_id,
                    "output_index": 0,
                    "content_index": 0,
                    "part": {"type": "output_text", "text": text},
                },
            ),
            (
                "response.output_item.done",
                {
                    "type": "response.output_item.done",
                    "output_index": 0,
                    "item": completed["output"][0],
                },
            ),
            ("response.completed", {"type": "response.completed", "response": completed}),
        ]
        raw = "".join(
            f"event: {event}\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
            for event, payload in events
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, _format, *_args) -> None:
        return


def _start_mock_responses_server() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockResponsesHandler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _collect_stream_events(stream, *, timeout_seconds: float = 20.0) -> list[tuple[str, object]]:
    events = []
    deadline = time.monotonic() + timeout_seconds
    terminal_events = {"done", "stream_end", "apperror", "error", "cancel"}
    queue_obj = stream.subscribe() if hasattr(stream, "subscribe") else stream
    while time.monotonic() < deadline:
        try:
            event = queue_obj.get(timeout=0.25)
        except queue.Empty:
            continue
        events.append(event)
        if event and event[0] in terminal_events:
            if event[0] in {"done", "apperror", "error", "cancel"}:
                break
    if hasattr(stream, "unsubscribe"):
        stream.unsubscribe(queue_obj)
    return events


def validate_real_agent_local_provider_e2e() -> None:
    _assert_agent_src_available()
    previous_env = {name: os.environ.get(name) for name in (
        "HERMES_WEBUI_AGENT_DIR",
        "HERMES_WEBUI_STATE_DIR",
        "HERMES_HOME",
        X_USER_ID_ENV,
        LEGACY_X_USER_ID_ENV,
        LEGACY_NOCOBASE_AUTH_ENV,
    )}
    server = _start_mock_responses_server()
    original_resolve = user_provider.resolve_user_provider
    original_validate_base_url = user_provider._validate_base_url
    try:
        with tempfile.TemporaryDirectory(prefix="hermes-webui-provider-state-") as state_dir, tempfile.TemporaryDirectory(prefix="hermes-home-provider-") as home_dir:
            state_path = Path(state_dir)
            home_path = Path(home_dir)
            workspace_path = state_path / "workspace"
            workspace_path.mkdir(parents=True, exist_ok=True)
            (home_path / "config.yaml").write_text(
                "default_model: smoke-model\nplatform_toolsets:\n  cli: []\n",
                encoding="utf-8",
            )
            os.environ["HERMES_WEBUI_AGENT_DIR"] = str(AGENT_SRC)
            os.environ["HERMES_WEBUI_STATE_DIR"] = str(state_path)
            os.environ["HERMES_HOME"] = str(home_path)
            os.environ[X_USER_ID_ENV] = "1"
            os.environ.pop(LEGACY_X_USER_ID_ENV, None)
            os.environ.pop(LEGACY_NOCOBASE_AUTH_ENV, None)
            if str(AGENT_SRC) not in sys.path:
                sys.path.append(str(AGENT_SRC))
            try:
                from run_agent import AIAgent  # noqa: F401
            except Exception as exc:
                raise RuntimeError(
                    "real run_agent.AIAgent is not importable; run with "
                    "`uv run --with-editable ./hermes-agent-src python scripts/validate_user_provider.py`"
                ) from exc

            import api.config as config
            import api.models as models
            import api.streaming as streaming

            config.SESSION_DIR.mkdir(parents=True, exist_ok=True)
            config.STREAMS.clear()
            config.CANCEL_FLAGS.clear()
            config.AGENT_INSTANCES.clear()

            mock_base_url = f"http://127.0.0.1:{server.server_port}/v1"
            provider_record = {
                "id": "provider-local-e2e",
                "user_id": "user-local-e2e",
                "name": "Local E2E Provider",
                "provider_slug": "local-e2e",
                "base_url": mock_base_url,
                "model_name": "smoke-model",
                "api_mode": "codex_responses",
                "thinking_level": "low",
                "api_key": FAKE_PROVIDER_KEY,
                "status": "enabled",
                "updatedAt": "2026-06-02T00:00:00Z",
            }
            user_provider.resolve_user_provider = lambda user_id: user_provider.UserProviderResolution(
                status="active",
                reason="active_provider",
                user_id=user_id,
                provider=provider_record,
            )
            user_provider._validate_base_url = lambda base_url: str(base_url or "").strip().rstrip("/")

            session = models.new_session(
                workspace=str(workspace_path),
                model="smoke-model",
                profile="default",
                model_provider="local-e2e",
            )
            stream_id = "stream_user_provider_local_e2e"
            stream = config.create_stream_channel()
            with config.STREAMS_LOCK:
                config.STREAMS[stream_id] = stream

            streaming._run_agent_streaming(
                session.session_id,
                "Reply with the local smoke response.",
                "smoke-model",
                str(workspace_path),
                stream_id,
                [],
                model_provider="local-e2e",
                user_id="user-local-e2e",
            )

            events = _collect_stream_events(stream)
            serialized_events = json.dumps(events, ensure_ascii=False, default=str)
            session_payload = json.loads(session.path.read_text(encoding="utf-8"))
            serialized_session = json.dumps(session_payload, ensure_ascii=False, default=str)
            error_payloads = [
                data for event_name, data in events if event_name in {"apperror", "error"}
            ]
            serialized_errors = json.dumps(error_payloads, ensure_ascii=False, default=str)

            assert server.requests, "mock /v1/responses was not called"
            assert server.requests[0]["authorization"] == f"Bearer {FAKE_PROVIDER_KEY}"
            assert "local provider smoke response" in serialized_events
            assert "local provider smoke response" in serialized_session
            assert FAKE_PROVIDER_KEY not in serialized_events
            assert FAKE_PROVIDER_KEY not in serialized_session
            assert FAKE_PROVIDER_KEY not in serialized_errors
            assert not error_payloads, f"unexpected streaming error payloads: {error_payloads}"
    finally:
        user_provider.resolve_user_provider = original_resolve
        user_provider._validate_base_url = original_validate_base_url
        server.shutdown()
        server.server_close()
        for name, value in previous_env.items():
            _restore_env(name, value)


def main() -> None:
    validate_default_disabled_models_payload_does_not_read_provider()
    validate_x_user_id_context_enables_runtime_lookup()
    validate_lookup_fallback_and_redaction_when_x_user_id_enabled()
    validate_models_cache_isolated_by_user_and_provider()
    validate_runtime_signature_omits_api_key()
    validate_enabled_status_selects_latest_provider_without_is_default()
    validate_legacy_nocobase_auth_env_enables_x_user_id_runtime()
    validate_private_and_local_base_urls_are_rejected()
    validate_dns_resolution_to_private_ip_is_rejected()
    validate_provider_key_redaction_is_forced_when_global_redaction_disabled()
    validate_real_agent_local_provider_e2e()
    validate_route_models_does_not_require_user_context()
    validate_x_user_id_header_sets_current_user()
    validate_x_user_id_cookie_sets_current_user()
    validate_x_user_id_header_cookie_mismatch_rejected()
    validate_x_user_id_missing_rejected()
    print("user provider validation passed")


if __name__ == "__main__":
    main()
