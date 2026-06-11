import collections
import io
import json
import queue
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
import urllib.error

import pytest

import api.config as config
import api.profiles as profiles
import api.streaming as streaming
import api.usage_telemetry as usage_telemetry
import api.user_provider as user_provider


class _FakeResponse:
    def __init__(self, body=b'{"data":{"id":"record-1"}}'):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False

    def read(self, *_args, **_kwargs):
        return self._body


class _NullLock:
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        return False


class _DummyMeter:
    def begin_session(self, _stream_id):
        return None

    def get_interval(self):
        return 10.0

    def get_stats(self):
        return {}

    def record_token(self, _stream_id, _count):
        return None

    def record_reasoning(self, _stream_id, _count):
        return None


class _FakeSession:
    def __init__(self, tmp_path):
        self.session_id = "session-1"
        self.title = "Existing title"
        self.workspace = str(tmp_path)
        self.model = "requested-model"
        self.model_provider = "requested-provider"
        self.profile = "profile-alpha"
        self.messages = []
        self.context_messages = []
        self.input_tokens = 0
        self.output_tokens = 0
        self.estimated_cost = None
        self.active_stream_id = "stream-1"
        self.pending_user_message = "hello"
        self.pending_attachments = []
        self.pending_started_at = 1_800_000_000.0
        self.tool_calls = []
        self.gateway_routing = None
        self.gateway_routing_history = []
        self.context_length = 0
        self.threshold_tokens = 0
        self.last_prompt_tokens = 0
        self.llm_title_generated = True
        self.path = tmp_path / "session-1.json"
        self.save_calls = []

    def save(self, touch_updated_at=True, skip_index=False):
        self.save_calls.append(
            {
                "touch_updated_at": touch_updated_at,
                "skip_index": skip_index,
            }
        )

    def compact(self):
        return {
            "session_id": self.session_id,
            "title": self.title,
            "profile": self.profile,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost": self.estimated_cost,
        }


class _SuccessAgent:
    model = "used-model"
    base_url = "https://provider.example"
    session_prompt_tokens = 7
    session_completion_tokens = 11
    session_estimated_cost_usd = 0.123
    context_compressor = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.session_id = kwargs.get("session_id")
        self.ephemeral_system_prompt = None

    def interrupt(self, _message):
        return None

    def run_conversation(self, *, persist_user_message, **_kwargs):
        return {
            "messages": [
                {"role": "user", "content": persist_user_message},
                {"role": "assistant", "content": "done"},
            ],
            "llm_gateway": {
                "used_provider": "used-provider",
                "used_model": "used-model",
            },
        }


class _FailingAgent(_SuccessAgent):
    def run_conversation(self, **_kwargs):
        raise RuntimeError("provider failed")


def _usage_kwargs(**overrides):
    kwargs = {
        "session_id": "session-1",
        "stream_id": "stream-1",
        "user_id": "user-1",
        "profile_name": "profile-alpha",
        "model": "gpt-test",
        "model_provider": "openai",
        "usage": {
            "input_tokens": 3,
            "output_tokens": 4,
            "estimated_cost": 0.12,
            "duration_seconds": 2.5,
            "tps": 1.6,
            "gateway_routing": {"used_provider": "openai"},
        },
    }
    kwargs.update(overrides)
    return kwargs


def test_webui_global_ephemeral_prompt_includes_skill_source_guard():
    prompt = streaming._build_webui_global_ephemeral_prompt(
        "profile-alpha",
        "/tmp/hermes/profiles/profile-alpha",
    )

    assert '当前对话 profile_name 是 "profile-alpha"' in prompt
    assert '/tmp/hermes/profiles/profile-alpha/cron/jobs.json' in prompt
    assert "但生成的内容仅作为参考方案" in prompt
    assert "禁止通过外部链接、文件路径、上传文件直接导入、加载、挂载或安装 Skill" in prompt
    assert "官方【技能工坊】完成，这是唯一正规入口" in prompt
    assert "你可以复制以上内容到【技能工坊】中正式创建" in prompt
    assert "我无法通过外部链接加载 Skill。请前往【技能工坊】" in prompt
    assert "所有 Skill 必须通过官方【技能工坊】创建，这是唯一正规入口" in prompt
    assert "如果涉及 Skill 创建，我是否在回复末尾明确引导了【技能工坊】？" in prompt
    assert "请前往技能工坊" in prompt


def test_build_chat_usage_done_event_normalizes_business_date_and_totals(monkeypatch):
    monkeypatch.setenv("HERMES_USAGE_TIMEZONE", "Asia/Shanghai")

    event = usage_telemetry.build_chat_usage_done_event(
        **_usage_kwargs(occurred_at=datetime(2026, 6, 10, 16, 30, tzinfo=timezone.utc))
    )

    assert event["event_key"] == "session-1:stream-1"
    assert event["total_tokens"] == 7
    assert event["business_date"] == "2026-06-11"
    assert event["business_week"] == "2026-W24"
    assert event["occurred_at"] == "2026-06-10T16:30:00Z"
    assert event["status"] == "done"
    assert event["source"] == "hermes-webui"
    assert event["gateway_routing"] == {"used_provider": "openai"}


def test_build_chat_usage_done_event_skips_missing_user_id():
    assert usage_telemetry.build_chat_usage_done_event(**_usage_kwargs(user_id="")) is None


def test_record_chat_usage_explicitly_disabled_does_not_open_url(monkeypatch):
    monkeypatch.setenv("HERMES_USAGE_TELEMETRY_ENABLED", "0")

    def fail_urlopen(*_args, **_kwargs):
        raise AssertionError("urlopen must not run when telemetry is disabled")

    monkeypatch.setattr(usage_telemetry.urllib.request, "urlopen", fail_urlopen)

    assert usage_telemetry.record_chat_usage_done(**_usage_kwargs()) is False
    assert usage_telemetry.record_chat_usage_done_async(**_usage_kwargs()) is None


def test_nocobase_create_uses_api_base_url_and_bearer_authorization(monkeypatch):
    captured = {}
    monkeypatch.delenv("HERMES_USAGE_TELEMETRY_ENABLED", raising=False)
    monkeypatch.setenv("NOCOBASE_API_BASE_URL", "https://nocobase.example/api")
    monkeypatch.setenv("NOCOBASE_AUTHORIZATION", "secret-token")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["authorization"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse()

    monkeypatch.setattr(usage_telemetry.urllib.request, "urlopen", fake_urlopen)

    assert usage_telemetry.record_chat_usage_done(**_usage_kwargs()) is True

    assert captured["url"] == "https://nocobase.example/api/hermes_chat_usage_events:create"
    assert captured["method"] == "POST"
    assert captured["authorization"] == "Bearer secret-token"
    assert captured["timeout"] == usage_telemetry.DEFAULT_NOCOBASE_TIMEOUT_SECONDS
    assert captured["body"]["event_key"] == "session-1:stream-1"


def test_nocobase_base_url_fallback_adds_api_suffix_and_preserves_bearer(monkeypatch):
    captured = {}
    monkeypatch.delenv("NOCOBASE_API_BASE_URL", raising=False)
    monkeypatch.setenv("NOCOBASE_BASE_URL", "https://nocobase.example")
    monkeypatch.setenv("NOCOBASE_AUTHORIZATION", "Bearer ready-token")

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["authorization"] = request.get_header("Authorization")
        return _FakeResponse()

    monkeypatch.setattr(usage_telemetry.urllib.request, "urlopen", fake_urlopen)

    assert usage_telemetry.record_chat_usage_done(**_usage_kwargs()) is True

    assert captured["url"] == "https://nocobase.example/api/hermes_chat_usage_events:create"
    assert captured["authorization"] == "Bearer ready-token"


def test_nocobase_http_error_redacts_authorization(monkeypatch):
    monkeypatch.setenv("NOCOBASE_API_BASE_URL", "https://nocobase.example/api")
    monkeypatch.setenv("NOCOBASE_AUTHORIZATION", "secret-token")

    def fake_urlopen(request, timeout=None):
        body = b'{"message":"bad secret-token Authorization: Bearer secret-token"}'
        raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, io.BytesIO(body))

    monkeypatch.setattr(usage_telemetry.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(usage_telemetry.UsageTelemetryError) as exc_info:
        usage_telemetry._nocobase_request("/hermes_chat_usage_events:create", method="POST", body={})

    message = str(exc_info.value)
    assert "secret-token" not in message
    assert "[REDACTED]" in message


def _install_streaming_harness(monkeypatch, tmp_path, agent_cls, telemetry_calls, *, stream_id="stream-1"):
    session = _FakeSession(tmp_path)
    event_queue = queue.Queue()
    streaming.STREAMS[stream_id] = event_queue
    streaming.CANCEL_FLAGS.pop(stream_id, None)
    streaming.AGENT_INSTANCES.pop(stream_id, None)
    streaming.STREAM_PARTIAL_TEXT.pop(stream_id, None)
    streaming.STREAM_REASONING_TEXT.pop(stream_id, None)
    streaming.STREAM_LIVE_TOOL_CALLS.pop(stream_id, None)

    monkeypatch.setattr(streaming, "get_session", lambda _session_id: session)
    monkeypatch.setattr(streaming, "_get_session_agent_lock", lambda _session_id: _NullLock())
    monkeypatch.setattr(streaming, "_get_ai_agent", lambda: agent_cls)
    monkeypatch.setattr(streaming, "meter", lambda: _DummyMeter())
    monkeypatch.setattr(streaming, "title_from", lambda _messages, title: title)
    monkeypatch.setattr(streaming, "_build_webui_global_ephemeral_prompt", lambda *_args: "")
    monkeypatch.setattr(streaming, "_build_native_multimodal_message", lambda _ctx, msg, _attachments, _workspace: msg)
    monkeypatch.setattr(streaming, "_sanitize_messages_for_api", lambda messages: list(messages or []))
    monkeypatch.setattr(streaming, "_filter_agent_control_messages", lambda messages: list(messages or []))
    monkeypatch.setattr(streaming, "_restore_reasoning_metadata", lambda _previous, updated: list(updated or []))
    monkeypatch.setattr(streaming, "_session_context_messages", lambda fake_session: list(fake_session.context_messages or []))
    monkeypatch.setattr(
        streaming,
        "_drop_checkpointed_current_user_from_context",
        lambda messages, _msg_text: list(messages or []),
    )
    monkeypatch.setattr(
        streaming,
        "_merge_display_messages_after_agent_result",
        lambda _previous_display, _previous_context, result_messages, _msg_text: list(result_messages or []),
    )
    monkeypatch.setattr(streaming, "_extract_tool_calls_from_messages", lambda _messages, live_tool_calls=None: [])
    monkeypatch.setattr(streaming, "_maybe_schedule_title_refresh", lambda *_args, **_kwargs: None)

    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda _profile: tmp_path)
    monkeypatch.setattr(profiles, "get_profile_runtime_env", lambda _profile_home: {})
    monkeypatch.setattr(config, "get_config", lambda: {})
    monkeypatch.setattr(config, "_resolve_cli_toolsets", lambda _cfg=None: [])
    monkeypatch.setattr(config, "SESSION_AGENT_CACHE", collections.OrderedDict())
    monkeypatch.setattr(config, "SESSION_AGENT_CACHE_LOCK", threading.Lock())
    monkeypatch.setattr(config, "SESSION_AGENT_CACHE_MAX", 50)
    monkeypatch.setattr(
        user_provider,
        "resolve_user_profile_provider",
        lambda _user_id, profile_name=None: SimpleNamespace(
            is_active=True,
            provider={
                "model_name": "used-model",
                "base_url": "https://provider.example",
                "api_key": "provider-key",
                "api_mode": "",
            },
        ),
    )
    monkeypatch.setattr(user_provider, "_validate_base_url", lambda raw: raw)
    monkeypatch.setattr(user_provider, "provider_runtime_signature", lambda _resolution: "provider-signature")
    monkeypatch.setattr(
        usage_telemetry,
        "record_chat_usage_done_async",
        lambda **kwargs: telemetry_calls.append(kwargs),
    )
    return session, event_queue


def _run_streaming_case(monkeypatch, tmp_path, agent_cls, *, ephemeral=False, user_id="user-1"):
    telemetry_calls = []
    session, event_queue = _install_streaming_harness(monkeypatch, tmp_path, agent_cls, telemetry_calls)

    streaming._run_agent_streaming(
        "session-1",
        "hello",
        "requested-model",
        str(tmp_path),
        "stream-1",
        None,
        ephemeral=ephemeral,
        model_provider="requested-provider",
        user_id=user_id,
    )

    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return session, events, telemetry_calls


def test_streaming_success_done_records_usage_once(monkeypatch, tmp_path):
    session, events, telemetry_calls = _run_streaming_case(monkeypatch, tmp_path, _SuccessAgent)

    event_names = [name for name, _payload in events]
    assert event_names.count("done") == 1
    assert len(telemetry_calls) == 1
    assert telemetry_calls[0]["session_id"] == session.session_id
    assert telemetry_calls[0]["stream_id"] == "stream-1"
    assert telemetry_calls[0]["user_id"] == "user-1"
    assert telemetry_calls[0]["profile_name"] == "profile-alpha"
    assert telemetry_calls[0]["usage"]["input_tokens"] == 7
    assert telemetry_calls[0]["usage"]["output_tokens"] == 11


def test_streaming_ephemeral_done_does_not_record_usage(monkeypatch, tmp_path):
    _session, events, telemetry_calls = _run_streaming_case(
        monkeypatch,
        tmp_path,
        _SuccessAgent,
        ephemeral=True,
    )

    event_names = [name for name, _payload in events]
    assert event_names.count("done") == 1
    assert telemetry_calls == []


def test_streaming_exception_path_does_not_record_usage(monkeypatch, tmp_path):
    _session, events, telemetry_calls = _run_streaming_case(monkeypatch, tmp_path, _FailingAgent)

    event_names = [name for name, _payload in events]
    assert "apperror" in event_names
    assert telemetry_calls == []


def test_streaming_cancel_before_agent_run_does_not_record_usage(monkeypatch, tmp_path):
    class _CancelBeforeRunAgent(_SuccessAgent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            streaming.CANCEL_FLAGS["stream-1"].set()

        def run_conversation(self, **_kwargs):
            raise AssertionError("run_conversation must not run after cancellation")

    _session, events, telemetry_calls = _run_streaming_case(monkeypatch, tmp_path, _CancelBeforeRunAgent)

    event_names = [name for name, _payload in events]
    assert "cancel" in event_names
    assert telemetry_calls == []
