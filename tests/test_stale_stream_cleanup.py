import queue
import threading
from pathlib import Path

import api.config as config
import api.routes as routes
from tests.route_source import function_source, read_route_sources
from tests.route_test_utils import invoke_route

REPO = Path(__file__).resolve().parents[1]
ROUTE_SOURCES = read_route_sources()
CHAT_START_SRC = function_source("_handle_chat_start")
class _GateLock:
    def __init__(self):
        self._lock = threading.Lock()
        self.lookup_finished = threading.Event()
        self.writer_finished = threading.Event()

    def __enter__(self):
        self._lock.acquire()
        return self

    def __exit__(self, exc_type, exc, tb):
        self._lock.release()
        if not self.lookup_finished.is_set():
            self.lookup_finished.set()
            assert self.writer_finished.wait(2), "writer did not finish race setup"
        return False


class _FakeSession:
    session_id = "issue1533-session"

    def __init__(self):
        self.title = "Issue 1533"
        self.model = "test"
        self.model_provider = None
        self.messages = []
        self.tool_calls = []
        self.active_stream_id = "stale-stream"
        self.pending_user_message = "old prompt"
        self.pending_attachments = ["old.txt"]
        self.pending_started_at = 123
        self.context_length = 0
        self.threshold_tokens = 0
        self.last_prompt_tokens = 0
        self.saved_stream_ids = []

    def save(self):
        self.saved_stream_ids.append(self.active_stream_id)

    def compact(self):
        return {
            "session_id": self.session_id,
            "title": self.title,
            "model": self.model,
            "message_count": len(self.messages),
        }


def test_stale_stream_cleanup_helper_exists():
    assert "def _clear_stale_stream_state(session)" in ROUTE_SOURCES
    assert "stream_id in STREAMS" in ROUTE_SOURCES
    assert "session.active_stream_id = None" in ROUTE_SOURCES
    assert "session.pending_user_message = None" in ROUTE_SOURCES
    assert "session.pending_attachments = []" in ROUTE_SOURCES
    assert "session.pending_started_at = None" in ROUTE_SOURCES
    assert "session.save()" in ROUTE_SOURCES


def test_session_load_clears_stale_stream_before_response():
    session = _FakeSession()
    response = {}

    def fake_get_session(sid, metadata_only=False):
        response["metadata_only"] = metadata_only
        return session

    original_get_session = routes.get_session
    original_model = routes._resolve_effective_session_model_for_display
    original_provider = routes._resolve_effective_session_model_provider_for_display
    original_lookup = routes._lookup_cli_session_metadata
    try:
        routes.get_session = fake_get_session
        routes._resolve_effective_session_model_for_display = lambda _session: "test"
        routes._resolve_effective_session_model_provider_for_display = lambda _session: None
        routes._lookup_cli_session_metadata = lambda _sid: {}
        config.STREAMS.clear()

        result = invoke_route("get", "/api/session?session_id=issue1533-session")
    finally:
        routes.get_session = original_get_session
        routes._resolve_effective_session_model_for_display = original_model
        routes._resolve_effective_session_model_provider_for_display = original_provider
        routes._lookup_cli_session_metadata = original_lookup

    assert response["metadata_only"] is False
    assert result.body["session"]["active_stream_id"] is None
    assert result.body["session"]["pending_user_message"] is None
    assert result.body["session"]["pending_attachments"] == []


def test_chat_start_clears_stale_pending_state_not_only_active_id():
    stale_comment_pos = CHAT_START_SRC.index("# Stale stream id from a previous run; clear and continue.")
    cleanup_pos = CHAT_START_SRC.index("_clear_stale_stream_state(s)", stale_comment_pos)
    stream_id_pos = CHAT_START_SRC.index("stream_id = uuid.uuid4().hex", cleanup_pos)
    assert stale_comment_pos < cleanup_pos < stream_id_pos


def test_stale_stream_cleanup_does_not_clobber_concurrent_chat_start(monkeypatch):
    """Regression for #1533: stale cleanup must not erase a new stream id.

    The gate lock pauses the cleanup thread after it has decided that the old
    stream id is stale, then lets a chat_start-like writer register and persist
    a new active_stream_id for the same session.
    """
    config.STREAMS.clear()
    config.SESSION_AGENT_LOCKS.clear()
    gate_lock = _GateLock()
    session = _FakeSession()
    new_stream_id = "new-stream"
    result = {}

    monkeypatch.setattr(routes, "STREAMS_LOCK", gate_lock)

    def cleanup_stale_stream():
        result["cleared"] = routes._clear_stale_stream_state(session)

    def start_new_stream():
        assert gate_lock.lookup_finished.wait(2), "cleanup did not reach race point"
        with routes.STREAMS_LOCK:
            routes.STREAMS[new_stream_id] = queue.Queue()
        with routes._get_session_agent_lock(session.session_id):
            session.active_stream_id = new_stream_id
            session.pending_user_message = "new prompt"
            session.pending_attachments = ["new.txt"]
            session.pending_started_at = 456
            session.save()
        gate_lock.writer_finished.set()

    cleanup_thread = threading.Thread(target=cleanup_stale_stream)
    writer_thread = threading.Thread(target=start_new_stream)
    cleanup_thread.start()
    writer_thread.start()
    cleanup_thread.join(2)
    writer_thread.join(2)

    assert not cleanup_thread.is_alive()
    assert not writer_thread.is_alive()
    assert result["cleared"] is False
    assert session.active_stream_id == new_stream_id
    assert session.pending_user_message == "new prompt"
    assert session.pending_attachments == ["new.txt"]
    assert session.pending_started_at == 456
