"""
Hermes Web UI -- Route handlers for GET and POST endpoints.
Extracted from server.py (Sprint 11) so server.py is a thin shell.

中文说明：Hermes Web UI 的 GET 和 POST endpoints（端点）route handlers
（路由处理器）。
这些处理器从 server.py 中拆出（Sprint 11），让 server.py 保持为 thin shell
（薄壳入口）。
"""

import html as _html
import calendar as _calendar
import copy
import json
import logging
import os
import queue
import re
import platform
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
from pathlib import Path
from contextlib import closing
from urllib.parse import parse_qs
from api.agent_sessions import (
    MESSAGING_SOURCES,
    is_cli_session_row,
    is_cli_session_row_visible,
)

logger = logging.getLogger(__name__)

# Treat stalled/closed HTTP clients as normal disconnects.  Long-lived SSE
# connections often end this way when a browser tab sleeps, a phone switches
# networks, or Tailscale leaves the socket half-closed.  If these bubble to the
# request handler, the server logs 500s and can leave CLOSE-WAIT sockets around
# until the OS-level timeout fires.
_CLIENT_DISCONNECT_ERRORS = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    TimeoutError,
    OSError,
)

# ── Cron run tracking ────────────────────────────────────────────────────────
from api.routes_helpers.cron import (
    _RUNNING_CRON_JOBS,
    _RUNNING_CRON_LOCK,
    _CRON_OUTPUT_CONTENT_LIMIT,
    _CRON_OUTPUT_HEADER_CONTEXT,
    _mark_cron_running,
    _mark_cron_done,
    _is_cron_running,
    _cron_response_marker_index,
    _cron_output_content_window,
    _cron_job_for_api,
    _cron_jobs_for_api,
    _normalize_cron_profile_lookup_name,
    _available_cron_profile_names,
    _normalize_cron_profile_value,
    _profile_home_for_cron_job,
    _profile_home_for_cron_profile_name,
    _parse_cron_calendar_month,
    _cron_job_frequency,
    _parse_iso_date,
    _days_from_next_run,
    _all_days,
    _parse_int_set,
    _cron_dow_value,
    _cron_expr_days,
    _weekday_days,
    _cron_calendar_days_for_job,
    _cron_calendar_entry,
    _cron_subprocess_result_timeout_seconds,
    _run_cron_job_in_profile_subprocess_impl,
    _run_cron_tracked as _run_cron_tracked_impl,
)

# ── SSE app-level heartbeat (#1623) ────────────────────────────────────────
#
# Kernel TCP keepalive (server.py setsockopt block) declares a peer dead at
# KEEPIDLE (10s) + KEEPINTVL (5s) * KEEPCNT (3) = 25s in the worst case. The
# app-level SSE heartbeat must fire well below that window so flaky-network
# probes never get the chance to kill an idle stream during long LLM thinking
# phases. 5s gives the kernel ~5x headroom: probe at 10s, heartbeat byte at
# every 5s of idle keeps the socket warm.
#
# Cost: ~12 bytes per heartbeat * 12 extra heartbeats/min = ~150B/min idle.
# Trivial; many production SSE deployments run 5-15s heartbeats specifically
# to handle proxies and mobile NAT.
_SSE_HEARTBEAT_INTERVAL_SECONDS = 5

from api.routes_helpers.messaging import (
    _MESSAGING_RAW_SOURCES,
    _MESSAGING_SESSION_METADATA_CACHE,
    _MESSAGING_SESSION_METADATA_LOCK,
    _STALE_MESSAGING_END_REASONS,
    CLI_VISIBLE_SESSION_CAP,
    _normalize_messaging_source,
    _is_known_messaging_source,
    _safe_first,
    _gateway_session_metadata_path,
    _load_gateway_session_identity_map,
    _lookup_gateway_session_identity,
    _lookup_cli_session_metadata,
    _messaging_session_identity,
    _session_messaging_raw_source,
    _has_durable_messaging_identity,
    _numeric_count,
    _should_hide_stale_messaging_session,
    _is_messaging_session_record,
    _is_messaging_session_id,
    _session_sort_timestamp,
    _is_cli_session_for_settings,
    _cap_recent_cli_sessions,
    _merge_cli_sidebar_metadata,
    _messaging_source_key,
    _keep_latest_messaging_session_per_source,
)

def _run_cron_job_in_profile_subprocess(job, execution_profile_home):
    """Execute cron.scheduler.run_job using a spawn child process."""
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    return _run_cron_job_in_profile_subprocess_impl(
        job,
        execution_profile_home,
        ctx,
        target=_cron_job_subprocess_main,
    )

def _cron_job_subprocess_main(job, execution_profile_home, result_queue):
    """Run one cron job inside a child process pinned to a profile home."""
    try:
        def _run():
            from cron.scheduler import run_job

            return run_job(job)

        if execution_profile_home is None:
            result = _run()
        else:
            from api.profiles import cron_profile_context_for_home

            with cron_profile_context_for_home(execution_profile_home):
                result = _run()
        result_queue.put(("ok", result))
    except BaseException as exc:  # pragma: no cover - surfaced in parent
        import traceback

        result_queue.put(("error", f"{type(exc).__name__}: {exc}", traceback.format_exc()))

def _run_cron_tracked(job, profile_home=None, execution_profile_home=None):
    """Wrapper that tracks running state around cron.scheduler.run_job."""
    return _run_cron_tracked_impl(
        job,
        profile_home,
        execution_profile_home,
        run_job_subprocess=_run_cron_job_in_profile_subprocess,
    )

# ── Profile-scoped session/project filtering (#1611, #1614) ────────────────
from api.routes_helpers.profile_filter import (
    _profiles_match,
    _all_profiles_query_flag,
    _requested_sessions_profile,
)

from api.routes_helpers.live_models import (
    _PROVIDER_ALIASES,
    _OPENAI_COMPAT_ENDPOINTS,
    _LIVE_MODELS_CACHE_TTL,
    _LIVE_MODELS_CACHE,
    _LIVE_MODELS_CACHE_LOCK,
    _active_profile_for_live_models_cache,
    _live_models_cache_key,
    _get_cached_live_models,
    _set_cached_live_models,
    _clear_live_models_cache,
)

from api.config import (
    STATE_DIR,
    SESSION_DIR,
    DEFAULT_WORKSPACE,
    DEFAULT_MODEL,
    SESSIONS,
    SESSIONS_MAX,
    LOCK,
    STREAMS,
    STREAMS_LOCK,
    CANCEL_FLAGS,
    SERVER_START_TIME,
    _resolve_cli_toolsets,
    get_available_models,
    IMAGE_EXTS,
    MD_EXTS,
    MIME_MAP,
    MAX_FILE_BYTES,
    MAX_UPLOAD_BYTES,
    CHAT_LOCK,
    _get_session_agent_lock,
    SESSION_AGENT_LOCKS,
    SESSION_AGENT_LOCKS_LOCK,
    load_settings,
    save_settings,
    set_hermes_default_model,
    model_with_provider_context,
    get_reasoning_status,
    set_reasoning_display,
    set_reasoning_effort,
    create_stream_channel,
    get_webui_session_save_mode,
)
from api.helpers import (
    require,
    bad,
    safe_resolve,
    j,
    t,
    read_body,
    _security_headers,
    _sanitize_error,
    redact_session_data,
    _redact_text,
)
from api.agent_health import build_agent_health_payload
from api.system_health import build_system_health_payload
from api.routes_handlers.logs import (
    _handle_logs,
)
from api.routes_handlers.memory import (
    _handle_memory_read,
    _handle_memory_write,
)
from api.routes_handlers.file import (
    _handle_list_dir,
    _handle_file_read,
    _handle_file_delete,
    _handle_file_save,
    _handle_file_create,
    _handle_file_rename,
    _handle_create_dir,
)
from api.routes_handlers.cron_read import (
    _cron_history_response,
    _cron_run_detail_response,
    _handle_cron_output,
    _handle_cron_status,
    _handle_cron_recent,
    _handle_cron_calendar,
)
from api.routes_handlers.mcp import (
    _handle_mcp_tools_list,
    _handle_mcp_servers_list,
    _handle_mcp_server_delete,
    _handle_mcp_server_update,
)
from api.routes_handlers.workspace import (
    _handle_workspace_add,
    _handle_workspace_remove,
    _handle_workspace_rename,
)
from api.routes_handlers.approval import (
    _handle_approval_pending,
    _handle_approval_inject,
    _handle_clarify_pending,
    _handle_clarify_inject,
    _handle_clarify_respond,
)
from api.routes_handlers.approval_extra import (
    _handle_approval_respond,
)
from api.routes_handlers.chat import (
    _checkpoint_user_message_for_eager_session_save,
    _handle_background,
    _handle_btw,
    _handle_chat_start,
    _handle_chat_sync,
    _handle_session_compress,
    _normalize_chat_attachments,
    _prepare_chat_start_session_for_stream,
)
from api.routes_handlers.cron_write import (
    _handle_cron_batch,
    _handle_cron_create,
    _handle_cron_delete,
    _handle_cron_history,
    _handle_cron_pause,
    _handle_cron_resume,
    _handle_cron_run,
    _handle_cron_run_detail,
    _handle_cron_update,
)
from api.routes_handlers.file_extra import (
    _handle_file_path,
    _handle_file_raw,
    _handle_file_reveal,
    _handle_media,
    _serve_file_bytes,
)
from api.routes_handlers.handoff import (
    _build_handoff_summary_tool_message,
    _extract_handoff_summary_payload,
    _handle_handoff_summary,
    _is_matching_handoff_summary_content,
    _is_matching_handoff_summary_message,
    _persist_handoff_summary,
    _persist_handoff_summary_locally,
    _persist_handoff_summary_to_state_db,
)
from api.routes_handlers.live_models import (
    _handle_live_models,
)
from api.routes_handlers.session_io import (
    _handle_session_import,
    _handle_conversation_rounds,
    _handle_sessions_search,
)
from api.routes_handlers.session_extra import (
    _handle_session_export,
    _handle_session_import_cli,
    _handle_sessions_cleanup,
    _is_cli_tool_metadata_enrichment,
    _is_messages_refresh_prefix_match,
    _message_has_cli_tool_metadata,
    _normalize_message_for_import_refresh,
    _strip_cli_tool_metadata_for_refresh,
)
from api.routes_handlers.streaming import (
    _handle_approval_sse_stream,
    _handle_clarify_sse_stream,
    _handle_gateway_sse_stream,
    _handle_sse_stream,
)
from api.routes_handlers.terminal import (
    _handle_terminal_start,
    _handle_terminal_input,
    _handle_terminal_resize,
    _handle_terminal_close,
)
from api.routes_handlers.workspace_extra import (
    _handle_workspace_reorder,
)
from api.routes_handlers.skill import (
    _handle_skill_save as _handle_skill_save_impl,
    _handle_skill_delete,
    _handle_profile_installed_skills,
    _community_skills_root,
    _community_skill_roots,
    _body_first_path,
    _coerce_hermes_home_path,
    _handle_skill_install_community,
    _handle_skill_uninstall_profile,
    _handle_user_skill_import,
    _handle_user_skill_import_cancel,
    _handle_user_skill_install_to_profile,
    _handle_user_skill_publish_from_profile,
    _handle_user_skill_update,
    _handle_user_skills_list,
)
from api.routes_handlers.profile import (
    _PROFILE_AGENT_NAME_MAX,
    _PROFILE_AGENT_DESCRIPTION_MAX,
    _PROFILE_AGENT_PROMPT_MAX,
    _PROFILE_AGENT_AVATAR_MAX,
    _PROFILE_AGENT_STATUSES,
    _PROFILE_AGENT_RECOMMENDED_SKILLS,
    _PROFILE_AGENT_DEFAULT_CLONE_FROM,
    _known_profile_memory_roots,
    _normalize_profile_memory_path,
    _resolve_profile_memory_file,
    _profile_memory_payload,
    _handle_profile_memory_read,
    _handle_profile_user_read,
    _profile_agent_text,
    _slugify_profile_agent_id,
    _normalize_profile_agent_status,
    _load_profile_agent_skills_catalog,
    _profile_agent_skill_matches,
    _recommended_profile_agent_skills,
    _normalize_profile_agent_skills,
    _profile_agent_create_options,
    _profile_agent_markdown,
    _write_profile_agent_files,
    _read_profile_agent_metadata,
    _coerce_profile_soul_candidate,
    _resolve_profile_soul_path,
    _profile_soul_path_from_body,
    _profile_soul_path_from_query,
    _handle_profile_soul_read,
    _handle_profile_change_soul,
    _profile_agent_detail_from_profile,
    _resolve_profile_agent_update_target,
    _handle_profile_agent_skills,
    _handle_profile_agents_list,
    _handle_profile_install_profiles,
    _handle_profile_agent_create,
    _handle_profile_agent_update,
    _handle_profile_memory_write,
    _handle_profile_user_write,
    _write_profile_memory_file,
)

def _kanban_unknown_endpoint(handler, parsed, method: str) -> bool:
    """Return a Kanban-specific 404 for stale clients/obsolete endpoint shapes."""
    return bad(
        handler,
        (
            f"unknown Kanban endpoint: {method} {parsed.path}. "
            "If this appeared after a WebUI update, your browser may be running "
            "a stale cached bundle; use Hard refresh now, then reopen Kanban."
        ),
        status=404,
    ) or True

def _clear_stale_stream_state(session) -> bool:
    """Clear persisted streaming flags when the in-memory stream no longer exists.

    A server restart or worker crash can leave active_stream_id/pending_* in the
    session JSON while STREAMS is empty. The frontend then keeps reconnecting to
    a dead stream and shows a permanent running/thinking state.

    SAFETY (#1558): If ``session`` was loaded with ``metadata_only=True``, its
    ``messages`` array is empty by design and calling ``save()`` would
    atomically overwrite the on-disk JSON, wiping the conversation. In that
    case we re-load the full session before mutating, so the persisted
    write carries the real messages forward.
    """
    stream_id = getattr(session, "active_stream_id", None)
    if not stream_id:
        return False
    with STREAMS_LOCK:
        stream_alive = stream_id in STREAMS
    if stream_alive:
        return False

    # ── #1558 P0 safety: if we were handed a metadata-only stub, reload the
    # full session before touching persisted state. The original
    # metadata-only object is left untouched so the caller's read path is
    # unaffected.
    original_stub = session  # SHOULD-FIX #1 (Opus): keep reference so we can
                             # patch the caller's in-memory copy after a
                             # successful clear, avoiding one ghost SSE
                             # reconnect on the very next /api/session GET.
    if getattr(session, "_loaded_metadata_only", False):
        try:
            from api.models import get_session as _get_session
            session = _get_session(session.session_id, metadata_only=False)
        except Exception:
            # If we cannot upgrade to a full load (file gone, decode error,
            # etc.) bail without clearing — better to leave a stale
            # active_stream_id than to wipe the conversation.
            logger.warning(
                "_clear_stale_stream_state: refused to clear stale stream %s "
                "for session %s — full reload failed and we will not save a "
                "metadata-only stub. See #1558.",
                stream_id, getattr(session, "session_id", "?"),
            )
            return False
        if session is None:
            return False
        # The full-load path may have already repaired stale pending fields
        # via _repair_stale_pending(); only re-assert if still set.
        if not getattr(session, "active_stream_id", None):
            # Patch the caller's stub so its read path also sees the cleared
            # field (matches the Opus SHOULD-FIX #1 — without this, /api/session
            # would briefly return the stale active_stream_id and the frontend
            # would attempt one ghost SSE reconnect before recovering).
            try:
                original_stub.active_stream_id = None
                if hasattr(original_stub, "pending_user_message"):
                    original_stub.pending_user_message = None
                if hasattr(original_stub, "pending_attachments"):
                    original_stub.pending_attachments = []
                if hasattr(original_stub, "pending_started_at"):
                    original_stub.pending_started_at = None
            except Exception:
                pass
            return False

    # ── #1533 race fix: acquire the per-session lock and re-read
    # active_stream_id under it. A concurrent chat_start may have already
    # registered a new stream after our STREAMS_LOCK check above; in that
    # case we must NOT clobber its session.active_stream_id.
    with _get_session_agent_lock(session.session_id):
        if getattr(session, "active_stream_id", None) != stream_id:
            return False
        session.active_stream_id = None
        if hasattr(session, "pending_user_message"):
            session.pending_user_message = None
        if hasattr(session, "pending_attachments"):
            session.pending_attachments = []
        if hasattr(session, "pending_started_at"):
            session.pending_started_at = None
        try:
            session.save()
        except Exception:
            logger.exception(
                "_clear_stale_stream_state: save() failed for session %s",
                getattr(session, "session_id", "?"),
            )
    # Patch the caller's stub (if different from the full-load object) so
    # its in-memory active_stream_id matches what just got persisted.
    if original_stub is not session:
        try:
            original_stub.active_stream_id = None
            if hasattr(original_stub, "pending_user_message"):
                original_stub.pending_user_message = None
            if hasattr(original_stub, "pending_attachments"):
                original_stub.pending_attachments = []
            if hasattr(original_stub, "pending_started_at"):
                original_stub.pending_started_at = None
        except Exception:
            pass
    return True

# ── CSRF: validate Origin/Referer on POST ────────────────────────────────────
from api.routes_helpers.csrf import (
    _normalize_host_port,
    _ports_match,
    _allowed_public_origins,
    _env_truthy,
    _check_csrf,
)

from api.routes_helpers.model_resolve import (
    _normalize_provider_id,
    _catalog_provider_id_sets,
    _catalog_has_provider,
    _model_matches_active_provider_family,
    _catalog_model_id_matches,
    _clean_session_model_provider,
    _split_provider_qualified_model,
    _should_attach_codex_provider_context,
    _resolve_compatible_session_model_state,
    _resolve_compatible_session_model,
    _normalize_session_model_in_place,
    _resolve_effective_session_model_for_display,
    _resolve_effective_session_model_provider_for_display,
    _session_model_state_from_request,
)

from api.models import (
    Session,
    get_session,
    new_session,
    all_sessions,
    title_from,
    _write_session_index,
    SESSION_INDEX_FILE,
    _active_state_db_path,
    load_projects,
    save_projects,
    import_cli_session,
    get_cli_sessions,
    get_cli_session_messages,
    count_conversation_rounds,
    CONVERSATION_ROUND_THRESHOLD,
    ensure_cron_project,
    is_cron_session,
)
from api.workspace import (
    load_workspaces,
    save_workspaces,
    get_last_workspace,
    set_last_workspace,
    list_dir,
    list_workspace_suggestions,
    read_file_content,
    safe_resolve_ws,
    resolve_trusted_workspace,
    validate_workspace_to_add,
    _is_blocked_system_path,
    _strip_surrounding_quotes,
    _workspace_blocked_roots,
)
from api.upload import handle_upload, handle_upload_extract, handle_transcribe
from api.streaming import _sse, _run_agent_streaming, cancel_stream
from api.providers import get_providers, get_provider_quota, set_provider_key, remove_provider_key
from api.onboarding import (
    apply_onboarding_setup,
    get_onboarding_status,
    complete_onboarding,
    probe_provider_endpoint,
)
from api.oauth import (
    cancel_onboarding_oauth_flow,
    poll_onboarding_oauth_flow,
    start_onboarding_oauth_flow,
)

# Approval system (optional -- graceful fallback if agent not available)
try:
    from tools.approval import (
        submit_pending as _submit_pending_raw,
        approve_session,
        approve_permanent,
        save_permanent_allowlist,
        is_approved,
        _pending,
        _lock,
        _permanent_approved,
        resolve_gateway_approval,
        enable_session_yolo,
        disable_session_yolo,
        is_session_yolo_enabled,
    )
except ImportError:
    _submit_pending_raw = lambda *a, **k: None
    approve_session = lambda *a, **k: None
    approve_permanent = lambda *a, **k: None
    save_permanent_allowlist = lambda *a, **k: None
    is_approved = lambda *a, **k: True
    resolve_gateway_approval = lambda *a, **k: 0
    enable_session_yolo = lambda *a, **k: None
    disable_session_yolo = lambda *a, **k: None
    is_session_yolo_enabled = lambda *a, **k: False
    _pending = {}
    _lock = threading.Lock()
    _permanent_approved = set()

# ── Approval SSE subscribers (long-connection push) ──────────────────────────
from api.routes_helpers.approval_sse import (
    _approval_sse_subscribers,
    _approval_sse_notify_subscribers,
)

def _approval_sse_subscribe(session_id: str) -> queue.Queue:
    """Register an SSE subscriber for approval events on a given session."""
    q = queue.Queue(maxsize=16)
    with _lock:
        _approval_sse_subscribers.setdefault(session_id, []).append(q)
    return q

def _approval_sse_unsubscribe(session_id: str, q: queue.Queue) -> None:
    """Remove an SSE subscriber."""
    with _lock:
        subs = _approval_sse_subscribers.get(session_id)
        if subs and q in subs:
            subs.remove(q)
            if not subs:
                _approval_sse_subscribers.pop(session_id, None)

def _approval_sse_notify_locked(session_id: str, head: dict | None, total: int) -> None:
    """Push an approval event to all SSE subscribers for a session.

    CALLER MUST HOLD `_lock`. Snapshots the subscriber list under the held
    lock and then calls `q.put_nowait()` on each (which is itself thread-safe).

    `head` is the approval entry currently at the head of the queue (the one
    the UI should display) — NOT the just-appended entry. With multiple
    parallel approvals (#527), the just-appended entry is at the TAIL, but
    `/api/approval/pending` always returns the HEAD, so SSE must match.

    `total` is the total number of pending approvals.

    Pass `head=None` and `total=0` when the queue has just been emptied (e.g.
    `_handle_approval_respond` popped the last entry) so the client knows to
    hide its approval card.
    """
    # The helper catches queue.Full so slow subscribers are dropped without
    # blocking the approval lock.
    _approval_sse_notify_subscribers(session_id, head, total)

def _approval_sse_notify(session_id: str, head: dict | None, total: int) -> None:
    """Convenience wrapper that takes `_lock` itself.

    Use only from contexts that don't already hold `_lock`. Production call
    sites (submit_pending, _handle_approval_respond) MUST hold the lock and
    call `_approval_sse_notify_locked` directly to avoid a notify-ordering
    race where a later append's notify can fire before an earlier append's
    notify (resulting in stale `pending_count`).
    """
    with _lock:
        _approval_sse_notify_locked(session_id, head, total)

def submit_pending(session_key: str, approval: dict) -> None:
    """Append a pending approval to the per-session queue.

    Wraps the agent's submit_pending to:
    - Add a stable approval_id (uuid4 hex) so the respond endpoint can target
      a specific entry even when multiple approvals are queued simultaneously.
    - Change the storage from a single overwriting dict value to a list, so
      parallel tool calls each get their own approval slot (fixes #527).
    - Notify any connected SSE subscribers immediately.
    """
    entry = dict(approval)
    entry.setdefault("approval_id", uuid.uuid4().hex)
    with _lock:
        queue_list = _pending.setdefault(session_key, [])
        # Replace a legacy non-list value if the agent version uses the old pattern.
        if not isinstance(queue_list, list):
            _pending[session_key] = [queue_list]
            queue_list = _pending[session_key]
        queue_list.append(entry)
        total = len(queue_list)
        head = queue_list[0]  # /api/approval/pending always returns head
        # Push to SSE subscribers from inside _lock so two parallel
        # submit_pending calls can't deliver out-of-order (T2's later
        # notify arriving before T1's earlier notify with a stale count).
        _approval_sse_notify_locked(session_key, head, total)
    # NOTE: We do NOT call _submit_pending_raw here — that function overwrites
    # _pending[session_key] with a single dict, which would undo the list we just
    # built. The gateway blocking path uses _gateway_queues (a separate mechanism
    # managed by check_all_command_guards / register_gateway_notify), which is
    # unaffected by _pending. The _pending dict is only used for UI polling.

# Clarify prompts (optional -- graceful fallback if agent not available)
try:
    from api.clarify import (
        submit_pending as submit_clarify_pending,
        get_pending as get_clarify_pending,
        resolve_clarify,
        sse_subscribe as clarify_sse_subscribe,
        sse_unsubscribe as clarify_sse_unsubscribe,
    )
except ImportError:
    submit_clarify_pending = lambda *a, **k: None
    get_clarify_pending = lambda *a, **k: None
    clarify_sse_subscribe = None
    resolve_clarify = lambda *a, **k: 0

# ── Insights endpoint ──────────────────────────────────────────────────────────

_LLM_WIKI_DOCS_URL = "https://hermes-agent.nousresearch.com/docs/user-guide/skills/bundled/research/research-llm-wiki"
_LLM_WIKI_PAGE_DIRS = ("entities", "concepts", "comparisons", "queries")

def _llm_wiki_active_hermes_home() -> Path:
    try:
        from api.profiles import get_active_hermes_home
        return Path(get_active_hermes_home()).expanduser()
    except Exception:
        return Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()

def _llm_wiki_env_file_path(hermes_home: Path) -> str | None:
    env_path = hermes_home / ".env"
    if not env_path.exists() or not env_path.is_file():
        return None
    try:
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() != "WIKI_PATH":
                continue
            value = value.strip().strip('"').strip("'")
            return value or None
    except Exception:
        return None
    return None

def _llm_wiki_get_config_path_value(config: dict, dotted_key: str) -> str | None:
    if not isinstance(config, dict):
        return None
    if dotted_key in config and config.get(dotted_key):
        return str(config.get(dotted_key))
    cur = config
    for part in dotted_key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return str(cur) if cur else None

def _llm_wiki_config_path() -> str | None:
    try:
        from api.config import get_config as _get_cfg
        cfg = _get_cfg()
    except Exception:
        return None
    return (
        _llm_wiki_get_config_path_value(cfg, "skills.config.wiki.path")
        or _llm_wiki_get_config_path_value(cfg, "wiki.path")
    )

# Cap WIKI walks to prevent self-DoS if WIKI_PATH points at /, /etc, /home, etc.
# Real LLM wikis have under a few thousand files; 10k is generous and catches misconfig.
_LLM_WIKI_MAX_FILES = 10000
# Refuse to walk these system roots even if explicitly configured.
_LLM_WIKI_FORBIDDEN_ROOTS = frozenset(
    str(Path(p).expanduser().resolve()) for p in ("/", "/etc", "/usr", "/var", "/opt", "/sys", "/proc")
)

def _llm_wiki_resolve_path() -> tuple[Path, str, bool]:
    hermes_home = _llm_wiki_active_hermes_home()
    raw = os.getenv("WIKI_PATH") or _llm_wiki_env_file_path(hermes_home)
    source = "WIKI_PATH" if raw else "default"
    configured = bool(raw)
    if not raw:
        raw = _llm_wiki_config_path()
        if raw:
            source = "skills.config.wiki.path"
            configured = True
    if not raw:
        raw = "~/wiki"
    return Path(os.path.expandvars(raw)).expanduser(), source, configured

def _llm_wiki_safe_iso(ts: float | None) -> str | None:
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None

def _llm_wiki_count_files(root: Path) -> int:
    if not root.exists() or not root.is_dir():
        return 0
    # Defense in depth: refuse to walk forbidden system roots even if WIKI_PATH
    # was set to one. The endpoint is auth-gated but a misconfigured server
    # shouldn't self-DoS by rglob'ing all of /etc on every Insights load.
    try:
        if str(root.resolve()) in _LLM_WIKI_FORBIDDEN_ROOTS:
            return 0
    except Exception:
        return 0
    count = 0
    iterated = 0
    for item in root.rglob("*"):
        iterated += 1
        if iterated > _LLM_WIKI_MAX_FILES:
            break  # bounded — prevents hangs on symlink loops or huge trees
        try:
            if item.is_file() and not any(part.startswith(".") for part in item.relative_to(root).parts):
                count += 1
        except Exception:
            continue
    return count

def _llm_wiki_page_files(wiki_path: Path) -> list[Path]:
    pages: list[Path] = []
    # Defense in depth: refuse forbidden system roots.
    try:
        if str(wiki_path.resolve()) in _LLM_WIKI_FORBIDDEN_ROOTS:
            return pages
    except Exception:
        return pages
    iterated = 0
    for dirname in _LLM_WIKI_PAGE_DIRS:
        section = wiki_path / dirname
        if not section.exists() or not section.is_dir():
            continue
        for item in section.rglob("*.md"):
            iterated += 1
            if iterated > _LLM_WIKI_MAX_FILES:
                return pages  # bounded
            try:
                rel = item.relative_to(section)
                if item.is_file() and not any(part.startswith(".") for part in rel.parts):
                    pages.append(item)
            except Exception:
                continue
    return pages

def _build_llm_wiki_status() -> dict:
    """Return private-safe LLM Wiki status metadata without reading page bodies."""
    try:
        wiki_path, path_source, path_configured = _llm_wiki_resolve_path()
        base = {
            "available": False,
            "enabled": False,
            "status": "missing",
            "entry_count": 0,
            "page_count": 0,
            "raw_source_count": 0,
            "last_updated": None,
            "last_writer": None,
            "path_configured": path_configured,
            "path_source": path_source,
            "toggle_available": False,
            "toggle_reason": "Hermes Agent exposes WIKI_PATH/wiki.path for location, but no stable on/off config flag is currently available.",
            "docs_url": _LLM_WIKI_DOCS_URL,
        }
        if not wiki_path.exists():
            return base
        if not wiki_path.is_dir():
            base["status"] = "not_directory"
            return base

        page_files = _llm_wiki_page_files(wiki_path)
        status_files = [p for p in (wiki_path / "SCHEMA.md", wiki_path / "index.md", wiki_path / "log.md") if p.exists() and p.is_file()]
        status_files.extend(page_files)
        latest = None
        for item in status_files:
            try:
                mtime = item.stat().st_mtime
            except Exception:
                continue
            latest = mtime if latest is None else max(latest, mtime)

        base.update({
            "available": True,
            "enabled": True,
            "status": "ready" if page_files else "empty",
            "entry_count": len(page_files),
            "page_count": len(page_files),
            "raw_source_count": _llm_wiki_count_files(wiki_path / "raw"),
            "last_updated": _llm_wiki_safe_iso(latest),
        })
        return base
    except Exception as exc:
        return {
            "available": False,
            "enabled": False,
            "status": "error",
            "entry_count": 0,
            "page_count": 0,
            "raw_source_count": 0,
            "last_updated": None,
            "last_writer": None,
            "path_configured": False,
            "path_source": "unknown",
            "toggle_available": False,
            "toggle_reason": "Unable to inspect LLM Wiki status safely.",
            "docs_url": _LLM_WIKI_DOCS_URL,
            "error": type(exc).__name__,
        }

def _handle_llm_wiki_status(handler, parsed) -> bool:
    j(handler, _build_llm_wiki_status())
    return True

def _handle_insights(handler, parsed) -> bool:
    """Return usage analytics from local WebUI session data."""
    import collections
    import time as _time

    query = parse_qs(parsed.query)
    try:
        days = min(max(int(query.get("days", ["30"])[0]), 1), 365)
    except (ValueError, TypeError):
        days = 30

    now = _time.time()
    today = _time.localtime(now)
    today_midnight = _time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 0, 0, 0, today.tm_wday, today.tm_yday, today.tm_isdst))
    day_secs = 86400
    first_day_ts = today_midnight - ((days - 1) * day_secs)
    cutoff = first_day_ts

    def _safe_usage_int(value) -> int:
        try:
            return max(int(float(value or 0)), 0)
        except (TypeError, ValueError):
            return 0

    def _safe_cost_float(value) -> float:
        if value is None:
            return 0.0
        try:
            if isinstance(value, str):
                value = value.strip().replace("$", "").replace(",", "")
                if not value:
                    return 0.0
            return max(float(value), 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _session_usage_ts(session: dict) -> float:
        return session.get("updated_at", session.get("created_at", 0)) or session.get("created_at", 0) or 0

    # Walk session index (fast, no full JSON parse)
    sessions_data = []
    idx_path = SESSION_DIR / "_index.json"
    if idx_path.exists():
        try:
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:
            idx = []
    else:
        idx = []

    for entry in idx:
        created = entry.get("created_at", 0) or 0
        updated = entry.get("updated_at", 0) or 0
        # Session is relevant if it was created or updated within the calendar window.
        if max(created, updated) < cutoff:
            continue
        sessions_data.append(entry)

    # Aggregate
    total_sessions = len(sessions_data)
    total_messages = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_cost = 0.0
    model_stats: dict[str, dict] = {}
    daily_tokens: dict[str, dict] = {}
    # Activity by day of week (0=Mon .. 6=Sun)
    dow_activity = collections.Counter()
    # Activity by hour of day (0-23)
    hod_activity = collections.Counter()

    for s in sessions_data:
        input_tokens = _safe_usage_int(s.get("input_tokens"))
        output_tokens = _safe_usage_int(s.get("output_tokens"))
        cost_value = _safe_cost_float(s.get("estimated_cost"))
        total_messages += _safe_usage_int(s.get("message_count"))
        total_input_tokens += input_tokens
        total_output_tokens += output_tokens
        total_cost += cost_value

        model = s.get("model") or "unknown"
        bucket = model_stats.setdefault(model, {
            "sessions": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost": 0.0,
        })
        bucket["sessions"] += 1
        bucket["input_tokens"] += input_tokens
        bucket["output_tokens"] += output_tokens
        bucket["cost"] += cost_value

        # Activity patterns
        ts = _session_usage_ts(s)
        if ts:
            try:
                dt = _time.localtime(ts)
                day_key = _time.strftime("%Y-%m-%d", dt)
                daily_bucket = daily_tokens.setdefault(day_key, {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "sessions": 0,
                    "cost": 0.0,
                })
                daily_bucket["input_tokens"] += input_tokens
                daily_bucket["output_tokens"] += output_tokens
                daily_bucket["sessions"] += 1
                daily_bucket["cost"] += cost_value
                dow_activity[dt.tm_wday] += 1
                hod_activity[dt.tm_hour] += 1
            except Exception:
                pass

    # Build model breakdown
    total_tokens = total_input_tokens + total_output_tokens
    models_breakdown = []
    for model, stats in model_stats.items():
        row_total_tokens = stats["input_tokens"] + stats["output_tokens"]
        row_cost = round(stats["cost"], 6)
        models_breakdown.append({
            "model": model,
            "sessions": stats["sessions"],
            "input_tokens": stats["input_tokens"],
            "output_tokens": stats["output_tokens"],
            "total_tokens": row_total_tokens,
            "cost": row_cost,
            "session_share": int(round((stats["sessions"] / total_sessions) * 100)) if total_sessions else 0,
            "token_share": int(round((row_total_tokens / total_tokens) * 100)) if total_tokens else 0,
            "cost_share": int(round((row_cost / total_cost) * 100)) if total_cost else 0,
        })
    models_breakdown.sort(key=lambda r: (-r["cost"], -r["sessions"], r["model"]))

    daily_series = []
    for i in range(days):
        day_ts = first_day_ts + (i * day_secs)
        day_key = _time.strftime("%Y-%m-%d", _time.localtime(day_ts))
        bucket = daily_tokens.get(day_key, {
            "input_tokens": 0,
            "output_tokens": 0,
            "sessions": 0,
            "cost": 0.0,
        })
        daily_series.append({
            "date": day_key,
            "input_tokens": bucket["input_tokens"],
            "output_tokens": bucket["output_tokens"],
            "sessions": bucket["sessions"],
            "cost": round(bucket["cost"], 6),
        })

    # Day-of-week labels
    dow_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dow_data = [{"day": dow_labels[i], "sessions": dow_activity.get(i, 0)} for i in range(7)]

    # Hour-of-day data
    hod_data = [{"hour": h, "sessions": hod_activity.get(h, 0)} for h in range(24)]

    return j(handler, {
        "period_days": days,
        "total_sessions": total_sessions,
        "total_messages": total_messages,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_tokens": total_tokens,
        "total_cost": round(total_cost, 6),
        "models": models_breakdown,
        "daily_tokens": daily_series,
        "activity_by_day": dow_data,
        "activity_by_hour": hod_data,
    })

# ── GET routes ────────────────────────────────────────────────────────────────

def _accept_loop_health(handler) -> dict:
    server = getattr(handler, "server", None)
    return {
        "requests_total": int(getattr(server, "accept_loop_requests_total", 0) or 0),
        "last_request_at": round(float(getattr(server, "accept_loop_last_request_at", 0.0) or 0.0), 3),
    }

def _streams_lock_health(timeout_seconds: float = 0.5) -> dict:
    t0 = time.time()
    acquired = STREAMS_LOCK.acquire(timeout=timeout_seconds)
    elapsed_ms = round((time.time() - t0) * 1000, 1)
    if not acquired:
        return {
            "status": "blocked",
            "timeout_seconds": timeout_seconds,
            "ms": elapsed_ms,
        }
    try:
        return {
            "status": "ok",
            "active_streams": len(STREAMS),
            "ms": elapsed_ms,
        }
    finally:
        STREAMS_LOCK.release()

def _deep_health_checks(stream_check: dict | None = None) -> tuple[dict, bool]:
    """Run cheap probes that exercise the state paths used by the UI shell.

    Plain /health intentionally stays tiny. /health?deep=1 is for supervisors
    and watchdogs that need to know whether the process can still touch the
    shared stream map, sidebar/session path, project state, and Hermes state.db
    without hitting the RST-before-write failure mode from #1458.

    `stream_check` is the result from a prior `_streams_lock_health()` call;
    if provided, it's reused so we don't acquire `STREAMS_LOCK` twice on the
    same /health?deep=1 request (per Opus advisor on stage-297).
    """
    checks: dict[str, dict] = {}

    checks["streams_lock"] = stream_check if stream_check is not None else _streams_lock_health()
    if checks["streams_lock"].get("status") != "ok":
        return checks, False

    t0 = time.time()
    try:
        sessions = all_sessions()
        checks["sessions"] = {
            "status": "ok",
            "count": len(sessions),
            "ms": round((time.time() - t0) * 1000, 1),
        }
    except Exception as exc:
        checks["sessions"] = {
            "status": "error",
            "error": type(exc).__name__,
            "ms": round((time.time() - t0) * 1000, 1),
        }

    t0 = time.time()
    try:
        projects = load_projects(_migrate=False)
        checks["projects"] = {
            "status": "ok",
            "count": len(projects),
            "ms": round((time.time() - t0) * 1000, 1),
        }
    except Exception as exc:
        checks["projects"] = {
            "status": "error",
            "error": type(exc).__name__,
            "ms": round((time.time() - t0) * 1000, 1),
        }

    t0 = time.time()
    try:
        db_path = _active_state_db_path()
        if not db_path.exists():
            checks["state_db"] = {
                "status": "missing",
                "ms": round((time.time() - t0) * 1000, 1),
            }
        else:
            with closing(sqlite3.connect(str(db_path))) as conn:
                conn.execute("PRAGMA schema_version").fetchone()
            checks["state_db"] = {
                "status": "ok",
                "ms": round((time.time() - t0) * 1000, 1),
            }
    except Exception as exc:
        checks["state_db"] = {
            "status": "error",
            "error": type(exc).__name__,
            "ms": round((time.time() - t0) * 1000, 1),
        }

    healthy = all(
        check.get("status") in {"ok", "missing"}
        for check in checks.values()
    )
    return checks, healthy

def _handle_health(handler, parsed):
    deep = parse_qs(parsed.query or "").get("deep", [""])[0].lower() in {"1", "true", "yes", "on"}
    stream_check = _streams_lock_health()
    payload = {
        "status": "ok" if stream_check.get("status") == "ok" else "degraded",
        "sessions": len(SESSIONS),
        "active_streams": int(stream_check.get("active_streams") or 0),
        "uptime_seconds": round(time.time() - SERVER_START_TIME, 1),
        "accept_loop": _accept_loop_health(handler),
    }
    if deep:
        if stream_check.get("status") != "ok":
            payload["checks"] = {"streams_lock": stream_check}
            return j(handler, payload, status=503)
        checks, healthy = _deep_health_checks(stream_check=stream_check)
        payload["checks"] = checks
        if not healthy:
            payload["status"] = "degraded"
            return j(handler, payload, status=503)
    if payload["status"] != "ok":
        return j(handler, payload, status=503)
    return j(handler, payload)

# ── Plugin visibility endpoint (#539) ───────────────────────────────────────
_PLUGIN_VISIBILITY_HOOKS = (
    "pre_tool_call",
    "post_tool_call",
    "pre_llm_call",
    "post_llm_call",
)
_PLUGIN_VISIBILITY_HOOK_SET = set(_PLUGIN_VISIBILITY_HOOKS)

def _get_plugin_manager_for_visibility():
    """Return Hermes Agent's plugin manager for read-only WebUI visibility."""
    from hermes_cli.plugins import get_plugin_manager

    return get_plugin_manager()

def _clean_plugin_visibility_text(value, *, limit=240) -> str:
    """Return bounded display text without path/callback-like internals."""
    if value is None:
        return ""
    text = str(value).replace("\x00", "").strip()
    # Display metadata should be plain labels/descriptions. Drop multiline text
    # and common path separators rather than risk leaking local plugin paths.
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text

def _plugin_visibility_payload(manager=None) -> dict:
    """Build a sanitized plugin/hook visibility payload for Settings.

    The Hermes Agent manager stores manifests and callback objects internally.
    This endpoint intentionally exposes only safe, user-facing metadata and the
    four lifecycle hook names called out by the Settings visibility MVP. It
    never includes plugin source paths, callback names, callback reprs, or raw
    load errors because those can contain private filesystem details.
    """
    manager = manager or _get_plugin_manager_for_visibility()
    manager.discover_and_load(force=False)

    plugins = []
    raw_plugins = getattr(manager, "_plugins", {}) or {}
    for key, loaded in sorted(raw_plugins.items(), key=lambda item: str(item[0])):
        manifest = getattr(loaded, "manifest", None)
        if manifest is None:
            continue
        plugin_key = _clean_plugin_visibility_text(
            getattr(manifest, "key", None) or key or getattr(manifest, "name", ""),
            limit=120,
        )
        name = _clean_plugin_visibility_text(getattr(manifest, "name", "") or plugin_key, limit=120)
        version = _clean_plugin_visibility_text(getattr(manifest, "version", ""), limit=80)
        description = _clean_plugin_visibility_text(getattr(manifest, "description", ""), limit=280)
        registered = []
        for hook in list(getattr(manifest, "provides_hooks", []) or []) + list(getattr(loaded, "hooks_registered", []) or []):
            hook_name = str(hook or "").strip()
            if hook_name in _PLUGIN_VISIBILITY_HOOK_SET and hook_name not in registered:
                registered.append(hook_name)
        registered.sort(key=_PLUGIN_VISIBILITY_HOOKS.index)
        plugins.append({
            "name": name,
            "key": plugin_key or name,
            "version": version,
            "description": description,
            "enabled": bool(getattr(loaded, "enabled", False)),
            "hooks": registered,
        })

    return {
        "plugins": plugins,
        "empty": not bool(plugins),
        "supported_hooks": list(_PLUGIN_VISIBILITY_HOOKS),
        "read_only": True,
    }

def _handle_plugins(handler, parsed) -> bool:
    try:
        return j(handler, _plugin_visibility_payload())
    except Exception as exc:
        logger.warning("Failed to build plugin visibility payload: %s", exc)
        return j(
            handler,
            {
                "plugins": [],
                "empty": True,
                "supported_hooks": list(_PLUGIN_VISIBILITY_HOOKS),
                "read_only": True,
                "unavailable": True,
            },
        )

from api.routes_dispatcher import (
    dispatch_delete,
    dispatch_get,
    dispatch_patch,
    dispatch_post,
)


def handle_get(handler, parsed) -> bool:
    """Handle all GET routes. Returns True if handled, False for 404."""
    return dispatch_get(handler, parsed)


def handle_post(handler, parsed) -> bool:
    """Handle all POST routes. Returns True if handled, False for 404."""
    return dispatch_post(handler, parsed)


def handle_patch(handler, parsed) -> bool:
    """Handle all PATCH routes. Returns True if handled, False for 404."""
    return dispatch_patch(handler, parsed)


def handle_delete(handler, parsed) -> bool:
    """Handle all DELETE routes. Returns True if handled, False for 404."""
    return dispatch_delete(handler, parsed)

def _handle_terminal_output(handler, parsed):
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    if not sid:
        return bad(handler, "session_id required")
    from api.terminal import get_terminal
    term = get_terminal(sid)
    if term is None:
        return j(handler, {"error": "terminal not running"}, status=404)

    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Connection", "keep-alive")
    handler.end_headers()
    try:
        while True:
            try:
                event, data = term.output.get(timeout=_SSE_HEARTBEAT_INTERVAL_SECONDS)
            except queue.Empty:
                handler.wfile.write(b": terminal heartbeat\n\n")
                handler.wfile.flush()
                if term.closed.is_set() and term.output.empty():
                    _sse(handler, "terminal_closed", {"exit_code": term.proc.poll()})
                    break
                continue
            _sse(handler, event, data)
            if event in ("terminal_closed", "terminal_error"):
                break
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        pass
    return True

def _gateway_sse_probe_payload(settings, watcher):
    enabled = bool(settings.get('show_cli_sessions'))
    # Use the public is_alive() accessor where available (current GatewayWatcher);
    # fall back to the private _thread check for any older in-memory instance
    # that might still be hanging around mid-upgrade, and for test doubles that
    # don't implement the full public API.
    if watcher is None:
        watcher_alive = False
    elif hasattr(watcher, 'is_alive') and callable(getattr(watcher, 'is_alive')):
        watcher_alive = bool(watcher.is_alive())
    else:
        _t = getattr(watcher, '_thread', None)
        watcher_alive = _t is not None and _t.is_alive()
    payload = {
        'enabled': enabled,
        'fallback_poll_ms': 30000,
        'ok': enabled and watcher_alive,
        'watcher_running': watcher_alive,
    }
    if not enabled:
        payload['error'] = 'agent sessions not enabled'
        return payload, 404
    if not watcher_alive:
        payload['error'] = 'watcher not started'
        return payload, 503
    return payload, 200


def _content_disposition_value(disposition: str, filename: str) -> str:
    """Build a latin-1-safe Content-Disposition value with RFC 5987 filename*."""
    import urllib.parse as _up

    safe_name = Path(filename).name.replace("\r", "").replace("\n", "")
    ascii_fallback = "".join(
        ch if 32 <= ord(ch) < 127 and ch not in {'"', '\\'} else "_"
        for ch in safe_name
    ).strip(" .")
    if not ascii_fallback:
        suffix = Path(safe_name).suffix
        ascii_suffix = "".join(
            ch if 32 <= ord(ch) < 127 and ch not in {'"', '\\'} else "_"
            for ch in suffix
        )
        ascii_fallback = f"download{ascii_suffix}" if ascii_suffix else "download"
    quoted_name = _up.quote(safe_name, safe="")
    return (
        f'{disposition}; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quoted_name}"
    )

def _parse_range_header(range_header: str, file_size: int) -> tuple[int, int] | None:
    """Parse a single HTTP bytes range into inclusive start/end offsets."""
    if not range_header or not range_header.startswith("bytes=") or file_size < 1:
        return None
    spec = range_header.split("=", 1)[1].strip()
    if "," in spec or "-" not in spec:
        return None
    start_s, end_s = spec.split("-", 1)
    try:
        if start_s == "":
            # suffix range: bytes=-500
            suffix_len = int(end_s)
            if suffix_len <= 0:
                return None
            start = max(0, file_size - suffix_len)
            end = file_size - 1
        else:
            start = int(start_s)
            end = int(end_s) if end_s else file_size - 1
            if start < 0:
                return None
            end = min(end, file_size - 1)
        if start > end or start >= file_size:
            return None
        return start, end
    except ValueError:
        return None









# ── POST route helpers ────────────────────────────────────────────────────────





























def _handle_skill_save(handler, body):
    return _handle_skill_save_impl(handler, body)







# ── MCP Server helpers ──
from api.config import get_config, _save_yaml_config_file, _get_config_path, reload_config

def _mask_secrets(obj):
    """Mask sensitive values in env vars and headers."""
    if not isinstance(obj, dict):
        return obj
    sensitive = ("auth", "token", "key", "secret", "password", "credential")
    masked = {}
    for k, v in obj.items():
        if isinstance(v, str) and any(s in k.lower() for s in sensitive):
            masked[k] = "••••••"
        elif isinstance(v, dict):
            masked[k] = _mask_secrets(v)
        else:
            masked[k] = v
    return masked

def _parse_mcp_enabled(value) -> bool:
    """Parse Hermes MCP ``enabled`` values without raising on bad config."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return True

def _mcp_runtime_status_by_name() -> dict[str, dict]:
    """Return already-known MCP runtime status without starting servers.

    ``tools.mcp_tool.get_mcp_status()`` only reads the existing MCP registry and
    configuration; it does not probe or spawn MCP subprocesses. If Hermes Agent
    is unavailable, fall back to an empty map so the API remains safe.
    """
    try:
        from tools.mcp_tool import get_mcp_status
        statuses = get_mcp_status()
    except Exception:
        return {}
    if not isinstance(statuses, list):
        return {}
    return {
        str(entry.get("name")): entry
        for entry in statuses
        if isinstance(entry, dict) and entry.get("name")
    }

def _server_summary(name, cfg, runtime_status=None):
    """Return a safe summary of an MCP server config."""
    runtime_status = runtime_status if isinstance(runtime_status, dict) else {}
    out = {"name": name}
    if not isinstance(cfg, dict):
        out.update({
            "transport": "invalid",
            "timeout": 120,
            "connect_timeout": 60,
            "enabled": False,
            "active": False,
            "status": "invalid_config",
            "tool_count": None,
        })
        return out

    enabled = _parse_mcp_enabled(cfg.get("enabled", True))
    connected = bool(runtime_status.get("connected")) if enabled else False
    if "url" in cfg:
        out["transport"] = "http"
        # Mask auth headers
        if "headers" in cfg:
            out["headers"] = _mask_secrets(cfg["headers"])
        out["url"] = cfg["url"]
    elif "command" in cfg:
        out["transport"] = "stdio"
        out["command"] = cfg.get("command", "")
        out["args"] = cfg.get("args", [])
        if "env" in cfg:
            out["env"] = _mask_secrets(cfg["env"])
    else:
        out["transport"] = "invalid"
        enabled = False
        connected = False

    out["timeout"] = cfg.get("timeout", 120)
    out["connect_timeout"] = cfg.get("connect_timeout", 60)
    out["enabled"] = enabled
    out["active"] = connected
    if out["transport"] == "invalid":
        out["status"] = "invalid_config"
    elif not enabled:
        out["status"] = "disabled"
    elif connected:
        out["status"] = "active"
    else:
        out["status"] = "configured"
    out["tool_count"] = runtime_status.get("tools") if runtime_status else None
    return out

def _mcp_safe_display_text(value, *, limit: int) -> str:
    """Return redacted, bounded MCP text safe for WebUI inventory rows."""
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    value = _redact_text(value).strip()
    value = re.sub(r"Authorization:\s*Bearer\s+\S+", "[REDACTED CREDENTIAL]", value, flags=re.I)
    if len(value) > limit:
        value = value[: max(0, limit - 1)].rstrip() + "…"
    return value

def _mcp_schema_type(schema) -> str:
    """Return a compact, non-sensitive display type for a JSON schema node."""
    if not isinstance(schema, dict):
        return "unknown"
    typ = schema.get("type")
    if isinstance(typ, list):
        typ = "/".join(str(t) for t in typ if t)
    if isinstance(typ, str) and typ:
        return typ
    for composite in ("anyOf", "oneOf", "allOf"):
        if isinstance(schema.get(composite), list) and schema[composite]:
            return composite
    if "enum" in schema:
        return "enum"
    return "unknown"

def _mcp_schema_summary(schema, *, limit: int = 12) -> list[dict]:
    """Summarize an MCP input schema without exposing raw defaults/examples.

    The WebUI only needs searchable/displayable argument hints. Returning raw
    JSON Schema can overexpose server-provided defaults, examples, enums, or
    vendor extensions, so this strips each parameter down to name/type/required
    and a redacted description.
    """
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = schema.get("required")
    required_names = set(required) if isinstance(required, list) else set()
    out = []
    for name, prop in properties.items():
        if len(out) >= limit:
            break
        if not isinstance(name, str):
            continue
        prop = prop if isinstance(prop, dict) else {}
        desc = prop.get("description", "")
        if not isinstance(desc, str):
            desc = ""
        desc = _mcp_safe_display_text(desc, limit=180)
        out.append({
            "name": name,
            "type": _mcp_schema_type(prop),
            "required": name in required_names,
            "description": desc,
        })
    return out

def _mcp_tool_schema_from_payload(tool):
    if not isinstance(tool, dict):
        return {}
    for key in ("parameters", "inputSchema", "input_schema", "schema"):
        value = tool.get(key)
        if isinstance(value, dict):
            if key == "schema" and isinstance(value.get("parameters"), dict):
                return value["parameters"]
            return value
    return {}

def _mcp_tool_summary(name, tool, server_summary):
    """Return a safe global inventory row for one MCP tool."""
    server_summary = server_summary if isinstance(server_summary, dict) else {}
    if isinstance(tool, str):
        tool = {"name": tool}
    elif not isinstance(tool, dict):
        tool = {}
    tool_name = str(tool.get("name") or name or "")
    description = tool.get("description") or ""
    if not isinstance(description, str):
        description = str(description)
    description = _mcp_safe_display_text(description, limit=360)
    return {
        "name": tool_name,
        "server": str(server_summary.get("name") or ""),
        "description": description,
        "active": bool(server_summary.get("active")),
        "enabled": bool(server_summary.get("enabled")),
        "status": server_summary.get("status") or "unknown",
        "schema_summary": _mcp_schema_summary(_mcp_tool_schema_from_payload(tool)),
    }

def _mcp_tools_from_runtime_status(runtime_by_name, server_summaries):
    """Read detailed MCP tool payloads from runtime status when available."""
    tools = []
    if not isinstance(runtime_by_name, dict):
        return tools
    for server_name, runtime in runtime_by_name.items():
        if not isinstance(runtime, dict):
            continue
        raw_tools = runtime.get("tools")
        if not isinstance(raw_tools, list):
            raw_tools = runtime.get("tool_schemas")
        if not isinstance(raw_tools, list):
            continue
        server_summary = server_summaries.get(str(server_name), {"name": str(server_name)})
        for index, tool in enumerate(raw_tools):
            fallback_name = f"{server_name}:{index}"
            summary = _mcp_tool_summary(fallback_name, tool, server_summary)
            if summary["name"]:
                tools.append(summary)
    return tools

def _mcp_tools_from_registry(server_summaries):
    """Read already-registered MCP tool schemas without probing MCP servers."""
    try:
        from tools.registry import registry
    except Exception:
        return []
    tools = []
    try:
        names = registry.get_all_tool_names()
    except Exception:
        return []
    for tool_name in names:
        try:
            toolset = registry.get_toolset_for_tool(tool_name)
        except Exception:
            continue
        if not isinstance(toolset, str) or not toolset.startswith("mcp-"):
            continue
        server_name = toolset[len("mcp-"):]
        schema = registry.get_schema(tool_name) or {}
        server_summary = server_summaries.get(server_name, {
            "name": server_name,
            "enabled": True,
            "active": False,
            "status": "configured",
        })
        tools.append(_mcp_tool_summary(tool_name, schema, server_summary))
    return tools

_MASKED_PLACEHOLDER = "••••••"

def _strip_masked_values(submitted, existing):
    """Remove masked placeholder values from submitted dict, keeping originals."""
    if not isinstance(submitted, dict) or not isinstance(existing, dict):
        return submitted
    cleaned = {}
    for k, v in submitted.items():
        if isinstance(v, str) and v == _MASKED_PLACEHOLDER:
            if k in existing and isinstance(existing[k], str):
                cleaned[k] = existing[k]  # preserve original real value
                continue
        elif isinstance(v, dict) and k in existing and isinstance(existing[k], dict):
            cleaned[k] = _strip_masked_values(v, existing[k])
        else:
            cleaned[k] = v
    return cleaned
