"""Regression tests for filtering Hermes agent internal control user messages."""

import json
import pathlib
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(REPO_ROOT))

import api.config as config
import api.models as models
from api.models import Session
from api.streaming import (
    _filter_agent_control_messages,
    _is_agent_control_user_message,
    _merge_display_messages_after_agent_result,
    _sanitize_messages_for_api,
)
from tests.route_test_utils import invoke_route


CONTROL_TEXT = (
    "You've reached the maximum number of tool-calling iterations allowed. "
    "Please provide a final response summarizing what you've found and "
    "accomplished so far, without calling any more tools."
)


@pytest.fixture
def isolated_sessions(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    index_file = session_dir / "_index.json"

    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", index_file)
    monkeypatch.setattr(config, "SESSION_INDEX_FILE", index_file, raising=False)
    models.SESSIONS.clear()
    yield session_dir
    models.SESSIONS.clear()


def _contains_control_text(messages):
    return any(
        isinstance(message, dict)
        and CONTROL_TEXT in str(message.get("content") or "")
        for message in messages
    )


def test_is_agent_control_user_message_matches_only_internal_control_text():
    assert _is_agent_control_user_message({"role": "user", "content": CONTROL_TEXT})
    assert _is_agent_control_user_message(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "maximum number of tool-calling iterations"},
                {"type": "text", "text": "without calling any more tools"},
            ],
        }
    )

    assert not _is_agent_control_user_message(
        {"role": "user", "content": "Please explain maximum number of tool-calling iterations."}
    )
    assert not _is_agent_control_user_message(
        {"role": "user", "content": "Please answer without calling any more tools."}
    )
    assert not _is_agent_control_user_message({"role": "user", "content": "普通用户输入"})
    assert not _is_agent_control_user_message({"role": "assistant", "content": CONTROL_TEXT})
    assert not _is_agent_control_user_message({"role": "tool", "content": CONTROL_TEXT})
    assert not _is_agent_control_user_message(
        {
            "role": "user",
            "content": "[CONTEXT COMPACTION - REFERENCE ONLY] Earlier turns were compacted.",
        }
    )


def test_filter_agent_control_messages_preserves_tool_turn_and_final_summary():
    messages = [
        {"role": "user", "content": "real request"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"output":"ok"}'},
        {"role": "user", "content": CONTROL_TEXT},
        {"role": "assistant", "content": "final summary"},
    ]

    filtered = _filter_agent_control_messages(messages)

    assert [message["role"] for message in filtered] == ["user", "assistant", "tool", "assistant"]
    assert not _contains_control_text(filtered)
    assert filtered[1]["tool_calls"][0]["id"] == "call-1"
    assert filtered[2]["tool_call_id"] == "call-1"
    assert filtered[-1]["content"] == "final summary"


def test_merge_display_messages_uses_filtered_agent_result_without_control_user():
    previous_display = [{"role": "user", "content": "real request"}]
    previous_context = list(previous_display)
    result_messages = previous_context + [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "function": {"name": "terminal", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": '{"output":"ok"}'},
        {"role": "user", "content": CONTROL_TEXT},
        {"role": "assistant", "content": "final summary"},
    ]

    merged = _merge_display_messages_after_agent_result(
        previous_display,
        previous_context,
        _filter_agent_control_messages(result_messages),
        "real request",
    )

    assert not _contains_control_text(merged)
    assert [message["role"] for message in merged] == ["user", "assistant", "tool", "assistant"]
    assert merged[-1]["content"] == "final summary"


def test_sanitize_messages_for_api_does_not_forward_control_user_message():
    context_messages = [
        {"role": "user", "content": "real request"},
        {"role": "user", "content": CONTROL_TEXT},
        {"role": "assistant", "content": "final summary"},
    ]

    sanitized = _sanitize_messages_for_api(context_messages)

    assert not _contains_control_text(sanitized)
    assert [message["role"] for message in sanitized] == ["user", "assistant"]


def test_session_api_filters_control_user_message_without_rewriting_disk(isolated_sessions):
    session = Session(
        session_id="control_session",
        workspace=str(isolated_sessions.parent),
        messages=[
            {"role": "user", "content": "real request"},
            {"role": "user", "content": CONTROL_TEXT},
            {"role": "assistant", "content": "final summary"},
        ],
    )
    session.save(touch_updated_at=False)
    before = session.path.read_text(encoding="utf-8")
    assert _contains_control_text(json.loads(before)["messages"])

    response = invoke_route(
        "get",
        "/api/session?session_id=control_session&messages=1&resolve_model=0",
    )

    assert response.status == 200
    returned_messages = response.body["session"]["messages"]
    assert not _contains_control_text(returned_messages)
    assert [message["role"] for message in returned_messages] == ["user", "assistant"]
    assert response.body["session"]["message_count"] == 2
    assert response.body["session"]["user_message_count"] == 1
    assert session.path.read_text(encoding="utf-8") == before
