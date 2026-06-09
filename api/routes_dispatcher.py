"""Route dispatcher implementations used by api.routes.

The functions in this module intentionally sync api.routes globals at call time.
That preserves the historical ``patch("api.routes.<name>")`` surface while
letting api/routes.py stay as the stable public entrypoint.
"""

import urllib.parse


def _sync_routes_bindings() -> None:
    import api.routes as routes

    current = globals()
    for name, value in vars(routes).items():
        if name.startswith("__") and name.endswith("__"):
            continue
        current[name] = value


def dispatch_get(handler, parsed) -> bool:
    """Handle all GET routes. Returns True if handled, False for 404."""
    _sync_routes_bindings()

    if parsed.path == "/sw.js":
        unregister_script = """/* Hermes API service: retire the removed WebUI service worker. */
self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((key) => caches.delete(key)));
    await self.registration.unregister();
    const clients = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const client of clients) {
      client.navigate(client.url);
    }
  })());
});

self.addEventListener('fetch', () => {});
"""
        data = unregister_script.encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/javascript; charset=utf-8")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Service-Worker-Allowed", "/")
        handler.send_header("Content-Length", str(len(data)))
        _security_headers(handler)
        handler.end_headers()
        handler.wfile.write(data)
        return True

    if parsed.path == "/":
        return j(
            handler,
            {
                "service": "hermes-api",
                "mode": "api-only",
                "status": "ok",
                "health": "/health",
                "api_base": "/api",
            },
        )

    if (
        parsed.path == "/index.html"
        or parsed.path.startswith("/session/")
        or parsed.path.startswith("/static/")
        or parsed.path in (
            "/manifest.json",
            "/manifest.webmanifest",
            "/favicon.ico",
        )
    ):
        return j(handler, {"error": "WebUI frontend has been removed"}, status=410)

    if parsed.path == "/login":
        return j(
            handler,
            {
                "error": "WebUI login page has been removed",
                "auth": "/api/auth/token-login",
            },
            status=410,
        )

    if parsed.path == "/api/auth/status":
        from api.auth import is_auth_enabled, parse_cookie, verify_session

        logged_in = False
        if is_auth_enabled():
            cv = parse_cookie(handler)
            logged_in = bool(cv and verify_session(cv))
        return j(handler, {"auth_enabled": is_auth_enabled(), "logged_in": logged_in})

    # ── Insights / knowledge status ──
    if parsed.path == "/api/insights":
        return _handle_insights(handler, parsed)

    if parsed.path.startswith("/api/kanban/"):
        from api.kanban_bridge import handle_kanban_get

        # Only treat an explicit False as "no route matched". None means the
        # bridge already sent a response via bad()/j() — emitting our own 404
        # on top of that produces concatenated JSON bodies on the wire.
        result = handle_kanban_get(handler, parsed)
        if result is False:
            return _kanban_unknown_endpoint(handler, parsed, "GET")
        return True
    if parsed.path == "/api/wiki/status":
        return _handle_llm_wiki_status(handler, parsed)
    if parsed.path == "/api/logs":
        return _handle_logs(handler, parsed)

    if parsed.path == "/health":
        return _handle_health(handler, parsed)

    if parsed.path == "/api/health/agent":
        return j(handler, build_agent_health_payload())

    if parsed.path == "/api/system/health":
        j(handler, build_system_health_payload())
        return True

    if parsed.path == "/api/models":
        from api.user_provider import (
            UserProviderAuthError,
            build_user_provider_models_payload,
            optional_user_id_from_handler,
        )

        try:
            user_id = optional_user_id_from_handler(handler)
            query = urllib.parse.parse_qs(parsed.query or "")
            profile_id = (query.get("profile_id") or query.get("profileId") or [""])[0]
            profile_name = (
                query.get("profile")
                or query.get("profile_name")
                or query.get("profileName")
                or query.get("hermes_profile")
                or [""]
            )[0]
            return j(
                handler,
                build_user_provider_models_payload(
                    user_id,
                    get_available_models,
                    profile_id=profile_id,
                    profile_name=profile_name,
                ),
            )
        except UserProviderAuthError as exc:
            return j(handler, {"error": str(exc), "code": exc.code}, status=exc.status)

    if parsed.path == "/api/user-ai-providers":
        from api.user_provider import current_user_id_from_handler
        from api.user_provider_management import error_payload, list_user_ai_providers_payload

        try:
            user_id = current_user_id_from_handler(handler)
            query = urllib.parse.parse_qs(parsed.query or "")
            profile_id = (query.get("profile_id") or query.get("profileId") or [""])[0]
            return j(handler, list_user_ai_providers_payload(user_id, profile_id=profile_id))
        except Exception as exc:
            payload, status = error_payload(exc)
            return j(handler, payload, status=status)

    if parsed.path == "/api/models/live":
        return _handle_live_models(handler, parsed)

    if parsed.path == "/api/dashboard/status":
        from api import dashboard_probe

        j(handler, dashboard_probe.get_dashboard_status())
        return True

    if parsed.path == "/api/dashboard/config":
        from api import dashboard_probe

        try:
            j(handler, dashboard_probe.get_dashboard_config())
        except ValueError as exc:
            bad(handler, str(exc), status=400)
        return True

    # ── Providers (GET) ──
    if parsed.path == "/api/providers":
        return j(handler, get_providers())

    # ── Plugins/hooks visibility (read-only, no callback/source internals) ──
    if parsed.path == "/api/plugins":
        return _handle_plugins(handler, parsed)
    if parsed.path == "/api/provider/quota":
        query = parse_qs(parsed.query)
        provider_id = (query.get("provider", [""])[0] or None)
        return j(handler, get_provider_quota(provider_id))

    if parsed.path == "/api/settings":
        settings = load_settings()
        # Never expose the stored password hash to clients
        settings.pop("password_hash", None)
        # Surface env-var precedence so the UI can disable the password field
        # instead of silently no-oping the save (#1560). The setting takes
        # precedence in api.auth.get_password_hash(), but until now the UI
        # had no way to know — see issue #1139 / #1560.
        settings["password_env_var"] = bool(
            os.getenv("HERMES_WEBUI_PASSWORD", "").strip()
        )
        # Inject the running version so the UI badge stays in sync with git tags
        # without any manual release step.
        try:
            from api.updates import AGENT_VERSION, WEBUI_VERSION
            settings["webui_version"] = WEBUI_VERSION
            settings["agent_version"] = AGENT_VERSION
        except Exception:
            pass
        return j(handler, settings)

    if parsed.path == "/api/reasoning":
        # Current reasoning config (shared source of truth with the CLI —
        # reads display.show_reasoning and agent.reasoning_effort from
        # the active profile's config.yaml).
        return j(handler, get_reasoning_status())

    if parsed.path == "/api/onboarding/status":
        return j(handler, get_onboarding_status())

    if parsed.path.startswith("/extensions/"):
        from api.extensions import serve_extension_static

        return serve_extension_static(handler, parsed)

    if parsed.path == "/api/session":
        import time as _time
        _t0 = _time.monotonic()
        _debug_slow = os.environ.get("HERMES_DEBUG_SLOW", "")
        query = parse_qs(parsed.query)
        sid = query.get("session_id", [""])[0]
        if not sid:
            return j(handler, {"error": "session_id is required"}, status=400)
        # ?messages=0 skips the message payload for fast session switching.
        # The frontend uses this when switching conversations in the sidebar
        # (only needs metadata). The full message array is loaded lazily
        # via ?messages=1 when the message panel opens.
        load_messages = query.get("messages", ["1"])[0] != "0"
        resolve_model_default = "1" if load_messages else "0"
        resolve_model = query.get("resolve_model", [resolve_model_default])[0] != "0"
        # ?msg_limit=N returns only the last N messages (tail window).
        # Used by the frontend for fast session switching — avoids serialising
        # and sending hundreds of messages when the user only sees the most
        # recent exchange.  Older messages are loaded on-demand via scrolling.
        _msg_limit = query.get("msg_limit", [None])[0]
        try:
            msg_limit = max(1, int(_msg_limit)) if _msg_limit else None
        except (ValueError, TypeError):
            msg_limit = None
        # ?msg_before=N — 0-based index into the full message array.
        # Returns messages before this index (for scroll-to-top lazy loading).
        # Combined with msg_limit for paging.
        _msg_before = query.get("msg_before", [None])[0]
        try:
            msg_before = int(_msg_before) if _msg_before else None
        except (ValueError, TypeError):
            msg_before = None
        try:
            _t1 = _time.monotonic()
            s = get_session(sid, metadata_only=(not load_messages))
            _clear_stale_stream_state(s)
            cli_meta = _lookup_cli_session_metadata(sid)
            is_messaging_session = _is_messaging_session_record(s) or _is_messaging_session_record(cli_meta)
            cli_messages = []
            if is_messaging_session:
                cli_messages = get_cli_session_messages(sid)
            _t2 = _time.monotonic()
            effective_model = (
                _resolve_effective_session_model_for_display(s)
                if resolve_model
                else None
            )
            effective_provider = (
                _resolve_effective_session_model_provider_for_display(s)
                if resolve_model
                else None
            )
            _t3 = _time.monotonic()
            if load_messages:
                if is_messaging_session and cli_messages:
                    sidecar_messages = getattr(s, "messages", []) or []
                    # Recovery/aggregate sidecars can intentionally contain a
                    # longer visible conversation than the single state.db
                    # segment for this messaging session id. Prefer the longer
                    # sidecar so repaired WebUI history is not hidden behind the
                    # canonical per-segment transcript.
                    _all_msgs = sidecar_messages if len(sidecar_messages) > len(cli_messages) else cli_messages
                else:
                    _all_msgs = s.messages
                from api.streaming import _filter_agent_control_messages
                _all_msgs = _filter_agent_control_messages(_all_msgs)
                _visible_user_message_count = sum(
                    1 for message in _all_msgs
                    if isinstance(message, dict) and message.get("role") == "user"
                )
            else:
                _all_msgs = []
                _visible_user_message_count = 0
            if load_messages:
                if msg_before is not None:
                    # Scroll-to-top paging: msg_before is a 0-based index into
                    # the full message list. Return the msg_limit messages that
                    # appear *before* this index (i.e. older messages).
                    # Using index instead of timestamp avoids issues with
                    # duplicate/missing timestamps.
                    _before_idx = max(0, min(int(msg_before), len(_all_msgs)))
                    _slice = _all_msgs[:_before_idx]
                    _truncated_msgs = _slice[-msg_limit:] if msg_limit else _slice
                elif msg_limit and len(_all_msgs) > msg_limit:
                    _truncated_msgs = _all_msgs[-msg_limit:]
                else:
                    _truncated_msgs = _all_msgs
            else:
                _truncated_msgs = _all_msgs
            # Resolve effective context_length with model-metadata fallback so
            # older sessions (pre-#1318) that have context_length=0 persisted
            # still render a meaningful indicator on load.  Mirrors the
            # SSE-path fallback in api/streaming.py:2333-2342.  Fixes #1436.
            _persisted_cl = getattr(s, "context_length", 0) or 0
            if not _persisted_cl:
                _model_for_lookup = (
                    getattr(s, "model", "") or effective_model or ""
                ).strip()
                if _model_for_lookup:
                    try:
                        from agent.model_metadata import get_model_context_length as _get_cl
                        _fb_cl = _get_cl(_model_for_lookup, "") or 0
                        if _fb_cl:
                            _persisted_cl = _fb_cl
                    except Exception:
                        pass
            raw = s.compact() | {
                "messages": _truncated_msgs,
                "tool_calls": getattr(s, "tool_calls", []) if load_messages else [],
                "active_stream_id": getattr(s, "active_stream_id", None),
                "pending_user_message": getattr(s, "pending_user_message", None),
                "pending_attachments": getattr(s, "pending_attachments", []) if load_messages else [],
                "pending_started_at": getattr(s, "pending_started_at", None),
                "context_length": _persisted_cl,
                "threshold_tokens": getattr(s, "threshold_tokens", 0) or 0,
                "last_prompt_tokens": getattr(s, "last_prompt_tokens", 0) or 0,
            }
            if load_messages:
                raw["message_count"] = len(_all_msgs)
                raw["user_message_count"] = _visible_user_message_count
            if cli_meta and _is_messaging_session_record(cli_meta):
                raw = _merge_cli_sidebar_metadata(raw, cli_meta)
            # Signal to the frontend that older messages were omitted.
            # For msg_before paging, compare against the filtered set,
            # not the full list — otherwise we signal truncation even when
            # all older messages were returned.
            if msg_before is not None:
                _truncated = load_messages and msg_limit is not None and len(_slice) > msg_limit
            else:
                _truncated = load_messages and msg_limit is not None and len(_all_msgs) > msg_limit
            raw["_messages_truncated"] = _truncated
            # Index of the first returned message in the full message array.
            # Frontend uses this as cursor for scroll-to-top paging.
            if msg_before is not None:
                raw["_messages_offset"] = max(0, _before_idx - len(_truncated_msgs))
            else:
                raw["_messages_offset"] = max(0, len(_all_msgs) - len(_truncated_msgs))
            _t4 = _time.monotonic()
            if effective_model:
                raw["model"] = effective_model
            if effective_provider:
                raw["model_provider"] = effective_provider
            redact = redact_session_data(raw)
            _t5 = _time.monotonic()
            resp = j(handler, {"session": redact})
            _t6 = _time.monotonic()
            if _debug_slow:
                logger.warning(
                    "[SLOW] session_id=%s get_session=%.1fms model_resolve=%.1fms "
                    "compact=%.1fms redact=%.1fms json_write=%.1fms total=%.1fms",
                    sid,
                    (_t2-_t1)*1000, (_t3-_t2)*1000, (_t4-_t3)*1000,
                    (_t5-_t4)*1000, (_t6-_t5)*1000, (_t6-_t0)*1000,
                )
            return resp
        except KeyError:
            # Not a WebUI session -- try CLI store
            cli_meta = _lookup_cli_session_metadata(sid)
            msgs = get_cli_session_messages(sid)
            if msgs:
                sess = {
                    "session_id": sid,
                    "title": (cli_meta or {}).get("title", "CLI Session"),
                    "workspace": (cli_meta or {}).get("workspace", ""),
                    "model": (cli_meta or {}).get("model", "unknown"),
                    "message_count": len(msgs),
                    "created_at": (cli_meta or {}).get("created_at", 0),
                    "updated_at": (cli_meta or {}).get("updated_at", 0),
                    "last_message_at": (cli_meta or {}).get("last_message_at")
                    or (cli_meta or {}).get("updated_at", 0)
                    or (msgs[-1] if msgs else {"timestamp": 0}).get("timestamp", 0),
                    "pinned": False,
                    "archived": False,
                    "project_id": None,
                    "profile": (cli_meta or {}).get("profile"),
                    "is_cli_session": True,
                    "source_tag": (cli_meta or {}).get("source_tag"),
                    "raw_source": (cli_meta or {}).get("raw_source"),
                    "session_source": (cli_meta or {}).get("session_source"),
                    "source_label": (cli_meta or {}).get("source_label"),
                    "read_only": bool((cli_meta or {}).get("read_only")),
                    "messages": msgs,
                    "tool_calls": [],
                }
                sess = _merge_cli_sidebar_metadata(sess, cli_meta)
                return j(handler, {"session": redact_session_data(sess)})
            return bad(handler, "Session not found", 404)

    if parsed.path == "/api/session/status":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        try:
            from api.session_ops import session_status
            _clear_stale_stream_state(get_session(sid, metadata_only=True))
            return j(handler, session_status(sid))
        except KeyError:
            return bad(handler, "Session not found", 404)

    if parsed.path == "/api/session/yolo":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        return j(handler, {"yolo_enabled": is_session_yolo_enabled(sid)})

    if parsed.path == "/api/session/usage":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        try:
            from api.session_ops import session_usage
            return j(handler, session_usage(sid))
        except KeyError:
            return bad(handler, "Session not found", 404)

    if parsed.path == "/api/background/status":
        sid = parse_qs(parsed.query).get("session_id", [""])[0]
        if not sid:
            return bad(handler, "Missing session_id")
        from api.background import get_results
        return j(handler, {"results": get_results(sid)})

    if parsed.path == "/api/sessions":
        webui_sessions = all_sessions()
        settings = load_settings()
        show_cli_sessions = bool(settings.get("show_cli_sessions"))
        if show_cli_sessions:
            cli = get_cli_sessions()
            cli_by_id = {s["session_id"]: s for s in cli}
            for s in webui_sessions:
                meta = cli_by_id.get(s.get("session_id"))
                if not meta:
                    continue
                if _is_messaging_session_record(meta):
                    s.update(_merge_cli_sidebar_metadata(s, meta))
                    if s.get("session_id") != meta.get("session_id"):
                        s["session_id"] = meta.get("session_id")
                else:
                    for key in ("source_tag", "raw_source", "session_source", "source_label"):
                        if not s.get(key) and meta.get(key):
                            s[key] = meta[key]
            # Apply the same CLI visibility semantics to imported local copies so
            # low-value imported artifacts do not leak into the sidebar.
            webui_sessions = [s for s in webui_sessions if is_cli_session_row_visible(s)]
            webui_ids = {s["session_id"] for s in webui_sessions}
            from api.models import _hide_from_default_sidebar as _cron_hide
            deduped_cli = [s for s in cli if s["session_id"] not in webui_ids and is_cli_session_row_visible(s) and not _cron_hide(s)]
        else:
            webui_sessions = [s for s in webui_sessions if not _is_cli_session_for_settings(s)]
            deduped_cli = []
        merged = webui_sessions + deduped_cli
        merged.sort(
            key=lambda s: s.get("last_message_at") or s.get("updated_at", 0) or 0,
            reverse=True,
        )
        # ── Profile scoping (#1611) ────────────────────────────────────────
        # Default: filter to the active profile. ?all_profiles=1 opts into
        # the aggregate view used by the "All profiles" sidebar toggle.
        # The other_profile_count is always returned so the UI can render
        # the "Show N from other profiles" affordance without sending the
        # cross-profile rows by default.
        #
        # IMPORTANT: scope BEFORE _keep_latest_messaging_session_per_source.
        # _messaging_source_key is profile-blind (#1614 follow-up): if the
        # same Slack/Telegram identity has sessions in profiles A and B, a
        # profile-blind dedupe would discard the older one even when scoped
        # to its own profile, leaving that profile with zero rows for that
        # source. Filter first so the dedupe operates only within the active
        # profile's rows.
        from api.profiles import get_active_profile_name
        try:
            requested_profile = _requested_sessions_profile(parsed)
        except ValueError as e:
            return bad(handler, str(e), 400)
        active_profile = requested_profile or get_active_profile_name()
        all_profiles = _all_profiles_query_flag(parsed)
        if all_profiles:
            scoped = merged
            other_profile_count = 0
        else:
            scoped = [s for s in merged
                      if _profiles_match(s.get("profile"), active_profile)]
            other_profile_count = len(merged) - len(scoped)
        scoped = _keep_latest_messaging_session_per_source(scoped)
        if show_cli_sessions:
            scoped = _cap_recent_cli_sessions(scoped, cli_cap=CLI_VISIBLE_SESSION_CAP)
        safe_merged = []
        for s in scoped:
            item = dict(s)
            if isinstance(item.get("title"), str):
                item["title"] = _redact_text(item["title"])
            safe_merged.append(item)
        return j(handler, {
            "sessions": safe_merged,
            "cli_count": len(deduped_cli),
            "all_profiles": all_profiles,
            "active_profile": active_profile,
            "other_profile_count": other_profile_count,
            "server_time": time.time(),
            "server_tz": time.strftime("%z"),
        })

    if parsed.path == "/api/projects":
        # ── Profile scoping (#1614) ────────────────────────────────────────
        # Default: filter to the active profile. ?all_profiles=1 returns the
        # aggregate list so settings/admin UIs can still see everything.
        from api.profiles import get_active_profile_name
        active_profile = get_active_profile_name()
        all_projects = load_projects()
        all_profiles = _all_profiles_query_flag(parsed)
        if all_profiles:
            scoped = all_projects
        else:
            scoped = [p for p in all_projects
                      if _profiles_match(p.get("profile"), active_profile)]
        return j(handler, {
            "projects": scoped,
            "all_profiles": all_profiles,
            "active_profile": active_profile,
            "other_profile_count": len(all_projects) - len(scoped),
        })

    if parsed.path == "/api/session/export":
        return _handle_session_export(handler, parsed)

    if parsed.path == "/api/workspaces":
        return j(
            handler, {"workspaces": load_workspaces(), "last": get_last_workspace()}
        )

    if parsed.path == "/api/workspaces/suggest":
        qs = parse_qs(parsed.query)
        prefix = qs.get("prefix", [""])[0]
        return j(
            handler,
            {
                "suggestions": list_workspace_suggestions(prefix),
                "prefix": prefix,
            },
        )

    if parsed.path == "/api/sessions/search":
        return _handle_sessions_search(handler, parsed)

    if parsed.path == "/api/list":
        return _handle_list_dir(handler, parsed)

    if parsed.path == "/api/personalities":
        # Read personalities from config.yaml agent.personalities section
        # (matches hermes-agent CLI behavior, not filesystem SOUL.md approach)
        from api.config import reload_config as _reload_cfg

        _reload_cfg()  # pick up config.yaml changes without server restart
        from api.config import get_config as _get_cfg

        _cfg = _get_cfg()
        agent_cfg = _cfg.get("agent", {})
        raw_personalities = agent_cfg.get("personalities", {})
        personalities = []
        if isinstance(raw_personalities, dict):
            for name, value in raw_personalities.items():
                desc = ""
                if isinstance(value, dict):
                    desc = value.get("description", "")
                elif isinstance(value, str):
                    desc = value[:80] + ("..." if len(value) > 80 else "")
                personalities.append({"name": name, "description": desc})
        return j(handler, {"personalities": personalities})

    if parsed.path == "/api/git-info":
        qs = parse_qs(parsed.query)
        sid = qs.get("session_id", [""])[0]
        if not sid:
            return bad(handler, "session_id required")
        try:
            s = get_session(sid)
        except KeyError:
            return bad(handler, "Session not found", 404)
        from api.workspace import git_info_for_workspace

        info = git_info_for_workspace(Path(s.workspace))
        return j(handler, {"git": info})

    if parsed.path == "/api/commands":
        from api.commands import list_commands
        return j(handler, {"commands": list_commands()})

    if parsed.path == "/api/updates/check":
        settings = load_settings()
        if not settings.get("check_for_updates", True):
            return j(handler, {"disabled": True})
        qs = parse_qs(parsed.query)
        force = qs.get("force", ["0"])[0] == "1"
        # ?simulate=1 returns fake behind counts for UI testing (localhost only)
        if (
            qs.get("simulate", ["0"])[0] == "1"
            and handler.client_address[0] == "127.0.0.1"
        ):
            return j(
                handler,
                {
                    "webui": {
                        "name": "webui",
                        "behind": 3,
                        "current_sha": "abc1234",
                        "latest_sha": "def5678",
                        "branch": "master",
                    },
                    "agent": {
                        "name": "agent",
                        "behind": 1,
                        "current_sha": "aaa0001",
                        "latest_sha": "bbb0002",
                        "branch": "master",
                    },
                    "checked_at": 0,
                },
            )
        from api.updates import check_for_updates

        return j(handler, check_for_updates(force=force))

    if parsed.path == "/api/chat/stream/status":
        stream_id = parse_qs(parsed.query).get("stream_id", [""])[0]
        return j(handler, {"active": stream_id in STREAMS, "stream_id": stream_id})

    if parsed.path == "/api/chat/cancel":
        stream_id = parse_qs(parsed.query).get("stream_id", [""])[0]
        if not stream_id:
            return bad(handler, "stream_id required")
        cancelled = cancel_stream(stream_id)
        return j(handler, {"ok": True, "cancelled": cancelled, "stream_id": stream_id})

    if parsed.path == "/api/chat/stream":
        return _handle_sse_stream(handler, parsed)

    if parsed.path == "/api/terminal/output":
        return _handle_terminal_output(handler, parsed)

    if parsed.path == '/api/sessions/gateway/stream':
        return _handle_gateway_sse_stream(handler, parsed)

    if parsed.path == "/api/media":
        return _handle_media(handler, parsed)

    if parsed.path == "/api/file/raw":
        return _handle_file_raw(handler, parsed)

    if parsed.path == "/api/file":
        return _handle_file_read(handler, parsed)

    if parsed.path == "/api/approval/pending":
        return _handle_approval_pending(handler, parsed)

    if parsed.path == "/api/approval/stream":
        return _handle_approval_sse_stream(handler, parsed)

    if parsed.path == "/api/approval/inject_test":
        # Loopback-only: used by automated tests; blocked from any remote client
        if handler.client_address[0] != "127.0.0.1":
            return j(handler, {"error": "not found"}, status=404)
        return _handle_approval_inject(handler, parsed)

    if parsed.path == "/api/clarify/pending":
        return _handle_clarify_pending(handler, parsed)

    if parsed.path == "/api/clarify/stream":
        return _handle_clarify_sse_stream(handler, parsed)

    if parsed.path == "/api/clarify/inject_test":
        # Loopback-only: used by automated tests; blocked from any remote client
        if handler.client_address[0] != "127.0.0.1":
            return j(handler, {"error": "not found"}, status=404)
        return _handle_clarify_inject(handler, parsed)

    if parsed.path == "/api/onboarding/oauth/poll":
        qs = parse_qs(parsed.query)
        flow_id = qs.get("flow_id", [""])[0]
        try:
            return j(
                handler,
                poll_onboarding_oauth_flow(flow_id),
                extra_headers={"Cache-Control": "no-store"},
            )
        except ValueError as e:
            return bad(handler, str(e))
        except KeyError as e:
            return bad(handler, str(e), 404)

    # ── Cron API (GET) ──
    # All cron handlers touch cron.jobs which resolves HERMES_HOME from
    # os.environ (process-global) at call time. Wrap in cron_profile_context
    # so the TLS-active profile's jobs.json is read, not the process default.
    if parsed.path == "/api/crons":
        from cron.jobs import list_jobs
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return j(handler, {"jobs": _cron_jobs_for_api(list_jobs(include_disabled=True))})

    if parsed.path == "/api/crons/output":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_output(handler, parsed)

    if parsed.path == "/api/crons/history":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_history(handler, parsed)

    if parsed.path == "/api/crons/run":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_run_detail(handler, parsed)

    if parsed.path == "/api/crons/recent":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_recent(handler, parsed)

    if parsed.path == "/api/crons/status":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_status(handler, parsed)

    # ── Skills API (GET) ──
    if parsed.path == "/api/skills":
        from tools.skills_tool import skills_list as _skills_list

        raw = _skills_list()
        data = json.loads(raw) if isinstance(raw, str) else raw
        return j(handler, {"skills": data.get("skills", [])})

    if parsed.path == "/api/skills/content":
        from tools.skills_tool import skill_view as _skill_view, SKILLS_DIR

        qs = parse_qs(parsed.query)
        name = qs.get("name", [""])[0]
        if not name:
            return j(handler, {"error": "name required"}, status=400)
        file_path = qs.get("file", [""])[0]
        if file_path:
            # Serve a linked file from the skill directory
            import re as _re

            if _re.search(r"[*?\[\]]", name):
                return bad(handler, "Invalid skill name", 400)
            skill_dir = None
            for p in SKILLS_DIR.rglob(name):
                if p.is_dir():
                    skill_dir = p
                    break
            if not skill_dir:
                return bad(handler, "Skill not found", 404)
            target = (skill_dir / file_path).resolve()
            try:
                target.relative_to(skill_dir.resolve())
            except ValueError:
                return bad(handler, "Invalid file path", 400)
            if not target.exists() or not target.is_file():
                return bad(handler, "File not found", 404)
            return j(
                handler,
                {"content": target.read_text(encoding="utf-8"), "path": file_path},
            )
        raw = _skill_view(name)
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data.get("linked_files"), dict):
            data["linked_files"] = {}
        return j(handler, data)

    if parsed.path == "/api/profile/installed-skills":
        return _handle_profile_installed_skills(handler, parsed)

    if parsed.path == "/api/user-skills":
        return _handle_user_skills_list(handler, parsed)

    if parsed.path == "/api/user-skills/files":
        return _handle_user_skill_files_list(handler, parsed)

    if parsed.path == "/api/user-skills/file":
        return _handle_user_skill_file_read(handler, parsed)

    if parsed.path == "/api/user-skills/test-availability/status":
        return _handle_user_skill_test_availability_status(handler, parsed)

    # ── Memory API (GET) ──
    if parsed.path == "/api/memory":
        return _handle_memory_read(handler)

    if parsed.path == "/api/profile/memory":
        return _handle_profile_memory_read(handler, parsed)

    if parsed.path == "/api/profile/user":
        return _handle_profile_user_read(handler, parsed)

    # ── Profile API (GET) ──
    if parsed.path == "/api/profiles":
        from api.profiles import list_profiles_api, get_active_profile_name

        return j(
            handler,
            {"profiles": list_profiles_api(), "active": get_active_profile_name()},
        )

    if parsed.path == "/api/profile/agents":
        return _handle_profile_agents_list(handler)

    if parsed.path == "/api/profile/create-agent/skills":
        return _handle_profile_agent_skills(handler, parsed)

    if parsed.path == "/api/profile/default":
        from api.profiles import list_profiles_api

        profiles = list_profiles_api()
        profile = (
            next((p for p in profiles if p.get("is_default")), None)
            or next((p for p in profiles if p.get("name") == "default"), None)
            or (profiles[0] if profiles else None)
        )
        if not profile:
            return bad(handler, "No profile available", 404)

        name = str(profile.get("name") or "default").strip() or "default"
        raw_path = str(profile.get("path") or "").strip()
        path = ""
        if raw_path:
            try:
                path = str(Path(raw_path).expanduser().resolve())
            except Exception:
                path = raw_path

        return j(
            handler,
            {
                "path": path,
                "avatar": str(profile.get("avatar") or ""),
                "profile_key": str(profile.get("profile_key") or name),
                "profile_name": str(profile.get("profile_name") or name),
                "webui_profile_id": str(
                    profile.get("webui_profile_id") or profile.get("id") or name
                ),
            },
        )

    if parsed.path == "/api/profile/file":
        from api.profiles import (
            _PROFILE_ID_RE,
            get_active_profile_name,
            get_active_hermes_home,
            get_hermes_home_for_profile,
            list_profiles_api,
        )

        qs = parse_qs(parsed.query)
        requested_profile = qs.get("profile", [""])[0].strip()
        if requested_profile and requested_profile != "default" and not _PROFILE_ID_RE.fullmatch(requested_profile):
            return bad(handler, "invalid profile", 400)
        if requested_profile:
            profile_name = requested_profile
            profile_home = Path(get_hermes_home_for_profile(requested_profile)).expanduser().resolve()
        else:
            profile_name = get_active_profile_name() or "default"
            profile_home = Path(get_active_hermes_home()).expanduser().resolve()

        requested_path = qs.get("path", [""])[0].strip() or "profiles/default.md"
        request_path = Path(requested_path).expanduser()
        target = request_path.resolve() if request_path.is_absolute() else (profile_home / request_path).resolve()
        try:
            target.relative_to(profile_home)
        except ValueError:
            return bad(handler, "Invalid profile file path", 400)

        try:
            relative_path = target.relative_to(profile_home).as_posix()
        except ValueError:
            relative_path = requested_path

        profile_info = None
        for item in list_profiles_api():
            if item.get("name") == profile_name:
                profile_info = item
                break
            try:
                item_path = Path(str(item.get("path") or "")).expanduser().resolve()
            except Exception:
                continue
            if item_path == profile_home:
                profile_info = item
                break
        if profile_info and profile_info.get("name"):
            profile_name = str(profile_info.get("name"))

        default_key = re.sub(
            r"[^a-zA-Z0-9_-]+",
            "_",
            Path(relative_path).with_suffix("").as_posix(),
        ).strip("_").lower() or profile_name
        display_name = qs.get("profile_name", [""])[0].strip() or target.stem or profile_name
        profile_key = qs.get("profile_key", [""])[0].strip() or default_key

        return j(
            handler,
            {
                "path": str(target),
                "avatar": qs.get("avatar", [""])[0].strip() or str((profile_info or {}).get("avatar") or ""),
                "profile_key": profile_key,
                "profile_name": display_name,
                "webui_profile_id": qs.get("webui_profile_id", [""])[0].strip()
                or f"{profile_name}:{relative_path}",
                "source": "registration",
                "is_default": bool((profile_info or {}).get("is_default", profile_name == "default")),
                "profile_path": str(profile_home),
            },
        )

    if parsed.path == "/api/profile/soul":
        return _handle_profile_soul_read(handler, parsed)

    if parsed.path == "/api/profile/active":
        from api.profiles import get_active_profile_name, get_active_hermes_home

        return j(
            handler,
            {"name": get_active_profile_name(), "path": str(get_active_hermes_home())},
        )

    # ── Gateway Status (GET) ──
    if parsed.path == "/api/gateway/status":
        import datetime
        identity_map = _load_gateway_session_identity_map()
        sessions_path = _gateway_session_metadata_path()

        # Detect whether the gateway process is alive, independent of
        # connected messaging platforms.  An empty identity_map just
        # means zero platforms connected, not that the gateway is down.
        #
        # agent_health.build_agent_health_payload() is the authoritative
        # signal: it reads gateway.status runtime metadata and returns a
        # tri-state `alive` field (True/False/None).  This avoids the
        # false-negative where the gateway is running but has zero active
        # messaging sessions (empty identity_map).
        #
        # `alive` tri-state semantics:
        #   True  → gateway process is alive
        #   False → gateway metadata exists but process is down
        #   None  → no gateway metadata/status available; this WebUI
        #           setup is probably not configured with a gateway
        health = build_agent_health_payload()
        alive = health.get("alive")
        if alive is True:
            running = True
            configured = True
        elif alive is False:
            running = False
            configured = True
        else:  # alive is None → gateway not configured / unavailable
            running = bool(identity_map)
            configured = False

        platforms_set: set[str] = set()
        for meta in identity_map.values():
            raw = meta.get("raw_source") or meta.get("platform") or ""
            norm = _normalize_messaging_source(raw)
            if norm:
                platforms_set.add(norm)
        _PLATFORM_LABELS = {
            "telegram": "Telegram",
            "discord": "Discord",
            "slack": "Slack",
            "web": "Web",
            "api": "API",
        }
        platforms = sorted(
            [{"name": p, "label": _PLATFORM_LABELS.get(p, p.title())} for p in platforms_set],
            key=lambda x: x["label"],
        )
        last_active = ""
        if running and sessions_path.exists():
            try:
                mtime = sessions_path.stat().st_mtime
                last_active = datetime.datetime.fromtimestamp(mtime).isoformat()
            except Exception:
                pass
        return j(handler, {
            "running": running,
            "configured": configured,
            "platforms": platforms,
            "last_active": last_active,
            "session_count": len(identity_map),
        })

    # ── MCP Servers (GET) ──
    if parsed.path == "/api/mcp/servers":
        return _handle_mcp_servers_list(handler)

    # ── MCP Tools (GET) ──
    if parsed.path == "/api/mcp/tools":
        return _handle_mcp_tools_list(handler)

    # ── Checkpoints / Rollback (GET) ──
    if parsed.path == "/api/rollback/list":
        qs = parse_qs(parsed.query)
        workspace = qs.get("workspace", [""])[0]
        if not workspace:
            return bad(handler, "workspace query parameter is required")
        try:
            from api.rollback import list_checkpoints
            return j(handler, list_checkpoints(workspace))
        except ValueError as e:
            return bad(handler, str(e))
        except Exception as e:
            logger.exception("rollback/list failed")
            return bad(handler, str(e), status=500)

    if parsed.path == "/api/rollback/diff":
        qs = parse_qs(parsed.query)
        workspace = qs.get("workspace", [""])[0]
        checkpoint = qs.get("checkpoint", [""])[0]
        if not workspace or not checkpoint:
            return bad(handler, "workspace and checkpoint query parameters are required")
        try:
            from api.rollback import get_checkpoint_diff
            return j(handler, get_checkpoint_diff(workspace, checkpoint))
        except ValueError as e:
            return bad(handler, str(e))
        except Exception as e:
            logger.exception("rollback/diff failed")
            return bad(handler, str(e), status=500)

    return False  # 404


def dispatch_post(handler, parsed) -> bool:
    """Handle all POST routes. Returns True if handled, False for 404."""
    _sync_routes_bindings()
    # CSRF: reject cross-origin browser requests
    if parsed.path != "/api/auth/token-login" and not _check_csrf(handler):
        return j(handler, {"error": "Cross-origin request rejected"}, status=403)

    if parsed.path == "/api/upload":
        return handle_upload(handler)
    if parsed.path == "/api/upload/extract":
        return handle_upload_extract(handler)

    if parsed.path == "/api/user-skills/import":
        return _handle_user_skill_import(handler)

    if parsed.path == "/api/transcribe":
        return handle_transcribe(handler)

    body = read_body(handler)

    if parsed.path.startswith("/api/kanban/"):
        from api.kanban_bridge import handle_kanban_post

        result = handle_kanban_post(handler, parsed, body)
        if result is False:
            return _kanban_unknown_endpoint(handler, parsed, "POST")
        return True
    if parsed.path == "/api/dashboard/config":
        from api import dashboard_probe

        try:
            j(handler, dashboard_probe.save_dashboard_config(body))
        except ValueError as exc:
            bad(handler, str(exc), status=400)
        except Exception as exc:
            logger.exception("dashboard config save failed")
            bad(handler, str(exc), status=500)
        return True

    if parsed.path == "/api/session/new":
        try:
            workspace = str(resolve_trusted_workspace(body.get("workspace"))) if body.get("workspace") else None
        except ValueError as e:
            return bad(handler, str(e))
        try:
            from api.user_provider import (
                UserProviderAuthError,
                optional_user_id_from_handler,
                verify_user_profile_access,
            )

            user_id = optional_user_id_from_handler(handler)
            if user_id:
                verify_user_profile_access(user_id, body.get("profile"))
        except UserProviderAuthError as exc:
            return j(handler, {"error": str(exc), "code": exc.code}, status=exc.status)
        model, model_provider = _session_model_state_from_request(
            body.get("model"),
            body.get("model_provider"),
        )
        # Use the profile sent by the client tab (if any) so that two tabs on
        # different profiles never clobber each other via the process-level global.
        s = new_session(
            workspace=workspace,
            model=model,
            model_provider=model_provider,
            profile=body.get("profile") or None,
            project_id=body.get("project_id") or None,
        )
        if user_id:
            s.user_id = user_id
        return j(handler, {"session": s.compact() | {"messages": s.messages}})

    if parsed.path == "/api/session/duplicate":
        try:
            sid = body.get("session_id")
            if not sid:
                return bad(handler, "session_id is required")

            session = Session.load(sid)
            if not session:
                # 404, not 400 — missing resource, not a malformed request.
                return bad(handler, "Session not found", status=404)

            # Deep-copy mutable lists so the duplicate is *actually* independent.
            # `Session.__init__` does `self.messages = messages or []` — plain
            # assignment, no copy. Without deepcopy, both sessions share the same
            # list object in memory; appending to one mutates the other.
            # Items inside `messages` are dicts with mutable values (tool_calls,
            # content arrays), so a shallow `list(...)` is not enough.
            copied_session = Session(
                session_id=uuid.uuid4().hex[:12],
                # Defensive: legacy sessions may have title=None on disk; fall back to 'Untitled'
                # so `+ " (copy)"` doesn't TypeError.
                title=(session.title or "Untitled") + " (copy)",
                workspace=session.workspace,
                model=session.model,
                model_provider=session.model_provider,
                messages=copy.deepcopy(session.messages),
                tool_calls=copy.deepcopy(session.tool_calls),
                # Reset ephemeral / per-session-instance flags. Duplicating an
                # archived conversation should produce a visible (un-archived)
                # copy; pinned status doesn't transfer either.
                pinned=False,
                archived=False,
                project_id=session.project_id,
                profile=session.profile,
                input_tokens=session.input_tokens,
                output_tokens=session.output_tokens,
                estimated_cost=session.estimated_cost,
                # Per-session settings the user may have customized — carry them over
                # so the duplicate behaves identically until further edits. Compression
                # anchor + last_prompt_tokens are intentionally NOT carried — those
                # re-derive on the next turn.
                personality=session.personality,
                enabled_toolsets=getattr(session, "enabled_toolsets", None),
                context_length=getattr(session, "context_length", None),
                threshold_tokens=getattr(session, "threshold_tokens", None),
                created_at=time.time(),
                updated_at=time.time(),
            )

            with LOCK:
                SESSIONS[copied_session.session_id] = copied_session
                SESSIONS.move_to_end(copied_session.session_id)
                while len(SESSIONS) > SESSIONS_MAX:
                    SESSIONS.popitem(last=False)
            # Persist immediately. The pre-PR flow (/api/session/new + /api/session/rename)
            # accidentally avoided this because `/api/session/rename` calls `s.save()`.
            # Without this explicit save, the duplicate is in-memory only — if the user
            # refreshes before sending a turn, the duplicate vanishes.
            copied_session.save()

            return j(handler, {"session": copied_session.compact() | {"messages": copied_session.messages}})
        except Exception as e:
            return bad(handler, str(e))

    if parsed.path == "/api/default-model":
        try:
            return j(handler, set_hermes_default_model(body.get("model")))
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), 500)

    # ── Providers (POST) ──
    if parsed.path == "/api/providers":
        provider_id = (body.get("provider") or "").strip().lower()
        api_key = body.get("api_key")
        if not provider_id:
            return bad(handler, "provider is required")
        if api_key is not None:
            api_key = str(api_key).strip() or None
        result = set_provider_key(provider_id, api_key)
        if not result.get("ok"):
            return bad(handler, result.get("error", "Unknown error"))
        return j(handler, result)

    if parsed.path == "/api/providers/delete":
        provider_id = (body.get("provider") or "").strip().lower()
        if not provider_id:
            return bad(handler, "provider is required")
        result = remove_provider_key(provider_id)
        if not result.get("ok"):
            return bad(handler, result.get("error", "Unknown error"))
        return j(handler, result)

    if parsed.path.startswith("/api/user-ai-providers/") or parsed.path == "/api/user-provider/test":
        from api.user_provider import current_user_id_from_handler
        from api.user_provider_management import (
            delete_user_ai_provider_payload,
            disable_user_ai_provider_payload,
            enable_user_ai_provider_payload,
            error_payload,
            save_user_ai_provider_payload,
            sync_user_ai_provider_payload,
            sync_user_ai_provider_profile_payload,
            test_user_ai_provider_payload,
        )

        try:
            user_id = current_user_id_from_handler(handler)
            if parsed.path in ("/api/user-ai-providers/test", "/api/user-provider/test"):
                return j(handler, test_user_ai_provider_payload(user_id, body))
            if parsed.path == "/api/user-ai-providers/save":
                return j(handler, save_user_ai_provider_payload(user_id, body))
            if parsed.path == "/api/user-ai-providers/enable":
                profile_id = body.get("profile_id") or body.get("profileId")
                provider_id = body.get("provider_id") or body.get("providerId")
                return j(handler, enable_user_ai_provider_payload(user_id, profile_id, provider_id))
            if parsed.path == "/api/user-ai-providers/disable":
                profile_id = body.get("profile_id") or body.get("profileId")
                return j(handler, disable_user_ai_provider_payload(user_id, profile_id))
            if parsed.path == "/api/user-ai-providers/delete":
                provider_id = body.get("id") or body.get("provider_id") or body.get("providerId")
                return j(handler, delete_user_ai_provider_payload(user_id, provider_id))
            if parsed.path == "/api/user-ai-providers/sync-profile":
                return j(handler, sync_user_ai_provider_profile_payload(user_id, body))
            if parsed.path == "/api/user-ai-providers/sync":
                return j(handler, sync_user_ai_provider_payload(user_id, body))
        except Exception as exc:
            payload, status = error_payload(exc)
            return j(handler, payload, status=status)

    if parsed.path == "/api/internal/provider-sync/root-profiles":
        from api.internal_provider_sync import (
            error_payload,
            sync_internal_provider_root_profiles_payload,
            verify_internal_provider_sync_token,
        )

        try:
            verify_internal_provider_sync_token(handler)
            return j(handler, sync_internal_provider_root_profiles_payload(body))
        except Exception as exc:
            payload, status = error_payload(exc)
            return j(handler, payload, status=status)

    if parsed.path == "/api/reasoning":
        # CLI-parity /reasoning handler — writes to the same config.yaml keys
        # the CLI uses (display.show_reasoning, agent.reasoning_effort) so a
        # preference set via WebUI is honoured in the terminal REPL and vice
        # versa.  Body is one of:
        #   {"display": "show"|"hide"|"on"|"off"}   → display.show_reasoning
        #   {"effort":  "none"|"minimal"|"low"|"medium"|"high"|"xhigh"}
        #                                            → agent.reasoning_effort
        try:
            display = body.get("display")
            effort = body.get("effort")
            if display is not None:
                flag = str(display).strip().lower()
                if flag in ("show", "on", "true", "1"):
                    return j(handler, set_reasoning_display(True))
                if flag in ("hide", "off", "false", "0"):
                    return j(handler, set_reasoning_display(False))
                return bad(handler, f"display must be show|hide|on|off (got '{display}')")
            if effort is not None:
                return j(handler, set_reasoning_effort(effort))
            return bad(handler, "reasoning: must supply 'display' or 'effort'")
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), 500)

    if parsed.path == "/api/admin/reload":
        # Hot-reload api.models module to pick up code changes without restart.
        import importlib
        from api import models as _models
        importlib.reload(_models)
        # Also re-expose get_session from the reloaded module so routes.py
        # continues to work (routes.py imported it at module level).
        import api.routes as _routes
        _routes.get_session = _models.get_session
        _routes.Session = _models.Session
        _routes.compact = _models.compact
        return j(handler, {"status": "ok", "reloaded": "api.models"})

    if parsed.path == "/api/sessions/cleanup":
        return _handle_sessions_cleanup(handler, body, zero_only=False)

    if parsed.path == "/api/sessions/cleanup_zero_message":
        return _handle_sessions_cleanup(handler, body, zero_only=True)

    if parsed.path == "/api/session/rename":
        try:
            require(body, "session_id", "title")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        with _get_session_agent_lock(body["session_id"]):
            s.title = str(body["title"]).strip()[:80] or "Untitled"
            s.save()
        return j(handler, {"session": s.compact()})

    if parsed.path == "/api/personality/set":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        if "name" not in body:
            return bad(handler, "Missing required field: name")
        sid = body["session_id"]
        name = body["name"].strip()
        try:
            s = get_session(sid)
        except KeyError:
            return bad(handler, "Session not found", 404)
        # Resolve personality from config.yaml agent.personalities section
        # (matches hermes-agent CLI behavior)
        prompt = ""
        if name:
            from api.config import reload_config as _reload_cfg2

            _reload_cfg2()  # pick up config changes without restart
            from api.config import get_config as _get_cfg2

            _cfg2 = _get_cfg2()
            agent_cfg = _cfg2.get("agent", {})
            raw_personalities = agent_cfg.get("personalities", {})
            if not isinstance(raw_personalities, dict) or name not in raw_personalities:
                return bad(
                    handler, f'Personality "{name}" not found in config.yaml', 404
                )
            value = raw_personalities[name]
            # Resolve prompt using the same logic as hermes-agent cli.py
            if isinstance(value, dict):
                parts = [value.get("system_prompt", "") or value.get("prompt", "")]
                if value.get("tone"):
                    parts.append(f"Tone: {value['tone']}")
                if value.get("style"):
                    parts.append(f"Style: {value['style']}")
                prompt = "\n".join(p for p in parts if p)
            else:
                prompt = str(value)
        with _get_session_agent_lock(sid):
            s.personality = name if name else None
            s.save()
        return j(handler, {"ok": True, "personality": s.personality, "prompt": prompt})

    if parsed.path == "/api/session/toolsets":
        """Set or clear per-session toolset override (#493).

        POST body: { session_id, toolsets: [...] | null }
        - toolsets: list of toolset names to restrict the session to, or null to clear.
        """
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        sid = body["session_id"]
        toolsets = body.get("toolsets")
        # Validate: if not None, must be a non-empty list of strings
        if toolsets is not None:
            if not isinstance(toolsets, list) or not toolsets:
                return bad(handler, "toolsets must be a non-empty list or null")
            if not all(isinstance(t, str) and t for t in toolsets):
                return bad(handler, "each toolset must be a non-empty string")
        try:
            s = get_session(sid)
        except KeyError:
            return bad(handler, "Session not found", 404)
        with _get_session_agent_lock(sid):
            s.enabled_toolsets = toolsets
            s.save()
        return j(handler, {"ok": True, "enabled_toolsets": s.enabled_toolsets})

    if parsed.path == "/api/session/update":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        old_ws = getattr(s, "workspace", "")
        try:
            new_ws = str(resolve_trusted_workspace(body.get("workspace", s.workspace)))
        except ValueError as e:
            return bad(handler, str(e))
        with _get_session_agent_lock(body["session_id"]):
            s.workspace = new_ws
            if "model" in body or "model_provider" in body:
                model, provider = _session_model_state_from_request(
                    body.get("model", s.model),
                    body.get("model_provider") if "model_provider" in body else None,
                    getattr(s, "model_provider", None),
                )
                if model is not None:
                    s.model = model
                s.model_provider = provider
            s.save()
        if str(old_ws or "") != str(new_ws or ""):
            try:
                from api.terminal import close_terminal
                close_terminal(body["session_id"])
            except Exception:
                logger.debug("Failed to close workspace terminal after workspace update")
        set_last_workspace(new_ws)
        return j(handler, {"session": s.compact() | {"messages": s.messages}})

    if parsed.path == "/api/session/delete":
        sid = body.get("session_id", "")
        if not sid:
            return bad(handler, "session_id is required")
        if not all(c in '0123456789abcdefghijklmnopqrstuvwxyz_' for c in sid):
            return bad(handler, "Invalid session_id", 400)
        cli_meta_for_delete = _lookup_cli_session_metadata(sid)
        if cli_meta_for_delete.get("read_only"):
            return bad(handler, "Read-only imported sessions cannot be deleted from WebUI", 400)
        is_messaging_session = _is_messaging_session_id(sid)
        # Delete from WebUI session store
        with LOCK:
            SESSIONS.pop(sid, None)
        try:
            SESSION_INDEX_FILE.unlink(missing_ok=True)
        except Exception:
            logger.debug("Failed to unlink session index")
        # Evict cached agent so turn count doesn't leak into a recycled session
        from api.config import _evict_session_agent
        _evict_session_agent(sid)
        try:
            p = (SESSION_DIR / f"{sid}.json").resolve()
            p.relative_to(SESSION_DIR.resolve())
        except Exception:
            return bad(handler, "Invalid session_id", 400)
        try:
            p.unlink(missing_ok=True)
        except Exception:
            logger.debug("Failed to unlink session file %s", p)
        # Prune the per-session agent lock so deleted sessions don't leak
        # Lock entries in SESSION_AGENT_LOCKS forever.
        with SESSION_AGENT_LOCKS_LOCK:
            SESSION_AGENT_LOCKS.pop(sid, None)
        try:
            from api.terminal import close_terminal
            close_terminal(sid)
        except Exception:
            logger.debug("Failed to close workspace terminal for deleted session %s", sid)
        # Also delete from CLI state.db for CLI sessions shown in sidebar,
        # but never erase external messaging channel memory via WebUI delete.
        if not is_messaging_session:
            try:
                from api.models import delete_cli_session

                delete_cli_session(sid)
            except Exception:
                logger.debug("Failed to delete CLI session %s", sid)
        return j(handler, {"ok": True})

    if parsed.path == "/api/session/clear":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        with _get_session_agent_lock(body["session_id"]):
            s.messages = []
            s.tool_calls = []
            s.title = "Untitled"
            s.save()
            # Evict cached agent — cleared session is a fresh conversation
            from api.config import _evict_session_agent
            _evict_session_agent(body["session_id"])
        return j(handler, {"ok": True, "session": s.compact()})

    if parsed.path == "/api/session/truncate":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        if body.get("keep_count") is None:
            return bad(handler, "Missing required field(s): keep_count")
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        keep = int(body["keep_count"])
        with _get_session_agent_lock(body["session_id"]):
            s.messages = s.messages[:keep]
            s.save()
        return j(
            handler, {"ok": True, "session": s.compact() | {"messages": s.messages}}
        )

    if parsed.path == "/api/session/branch":
        # Fork a conversation from any message point (#465).
        # Accepts: {session_id, keep_count?, title?}
        #   keep_count: number of messages to copy (0=empty, undefined=full history)
        #   title: custom title (defaults to "<original title> (fork)")
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        # Reject non-string session_id explicitly so the failure surfaces as a
        # 400 instead of a generic 500 from get_session() raising TypeError.
        # (Opus pre-release follow-up.)
        if not isinstance(body["session_id"], str):
            return bad(handler, "session_id must be a string")
        try:
            source = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)

        keep_count = body.get("keep_count")
        if keep_count is not None:
            try:
                keep_count = int(keep_count)
            except (ValueError, TypeError):
                return bad(handler, "keep_count must be an integer")
            # Negative slice (`messages[:-N]`) returns "all but last N", which
            # is a confusing fork semantic. Reject explicitly so the user
            # doesn't accidentally fork a session with the tail truncated when
            # they meant to copy the prefix. (Opus pre-release follow-up.)
            if keep_count < 0:
                return bad(handler, "keep_count must be non-negative")

        custom_title = body.get("title")
        if custom_title:
            custom_title = str(custom_title).strip()[:80] or None

        # Build messages slice
        source_messages = source.messages or []
        if keep_count is not None:
            forked_messages = source_messages[:keep_count]
        else:
            forked_messages = list(source_messages)

        # Derive title
        if custom_title:
            branch_title = custom_title
        else:
            source_title = source.title or "Untitled"
            branch_title = f"{source_title} (fork)"

        # Create new session inheriting workspace/model/profile
        branch = Session(
            workspace=source.workspace,
            model=source.model,
            profile=getattr(source, "profile", None),
            title=branch_title,
            messages=forked_messages,
            parent_session_id=source.session_id,
        )
        with LOCK:
            SESSIONS[branch.session_id] = branch
            SESSIONS.move_to_end(branch.session_id)
            while len(SESSIONS) > SESSIONS_MAX:
                SESSIONS.popitem(last=False)

        # Persist only if there are messages (matches new_session pattern)
        if forked_messages:
            branch.save()

        return j(handler, {
            "session_id": branch.session_id,
            "title": branch_title,
            "parent_session_id": source.session_id,
        })

    if parsed.path == "/api/session/compress":
        return _handle_session_compress(handler, body)

    if parsed.path == "/api/session/conversation-rounds":
        return _handle_conversation_rounds(handler, body)

    if parsed.path == "/api/session/handoff-summary":
        return _handle_handoff_summary(handler, body)

    if parsed.path == "/api/session/retry":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            from api.session_ops import retry_last
            result = retry_last(body["session_id"])
            return j(handler, {"ok": True, **result})
        except KeyError:
            return bad(handler, "Session not found", 404)
        except ValueError as e:
            return j(handler, {"error": str(e)})

    if parsed.path == "/api/session/undo":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            from api.session_ops import undo_last
            result = undo_last(body["session_id"])
            return j(handler, {"ok": True, **result})
        except KeyError:
            return bad(handler, "Session not found", 404)
        except ValueError as e:
            return j(handler, {"error": str(e)})

    # ── YOLO mode toggle (POST) ──
    # Session-scoped only — stored in-memory on the server side.
    # Important lifecycle notes:
    #   • Page reload: state PERSISTS (frontend re-fetches via GET endpoint)
    #   • Cross-tab: state is SHARED (same server-side flag per session)
    #   • Server restart: state is LOST (in-memory only)
    #   • Cross-session: isolated (each session has its own flag)
    # Fixes #467
    if parsed.path == "/api/session/yolo":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        sid = body["session_id"]
        enabled = bool(body.get("enabled", True))
        if enabled:
            enable_session_yolo(sid)
            # Also resolve any pending approvals for this session so the
            # agent doesn't stay stuck waiting on an already-dismissed card.
            try:
                from tools.approval import _pending as _p, _lock as _l
                with _l:
                    _p.pop(sid, None)
            except Exception:
                pass
            resolve_gateway_approval(sid, "once", resolve_all=True)
        else:
            disable_session_yolo(sid)
        return j(handler, {"ok": True, "yolo_enabled": enabled})

    if parsed.path == "/api/btw":
        return _handle_btw(handler, body)

    if parsed.path == "/api/background":
        return _handle_background(handler, body)

    if parsed.path == "/api/chat/start":
        return _handle_chat_start(handler, body)

    if parsed.path == "/api/chat":
        return _handle_chat_sync(handler, body)

    if parsed.path == "/api/chat/steer":
        from api.streaming import _handle_chat_steer
        return _handle_chat_steer(handler, body)

    if parsed.path == "/api/terminal/start":
        return _handle_terminal_start(handler, body)

    if parsed.path == "/api/terminal/input":
        return _handle_terminal_input(handler, body)

    if parsed.path == "/api/terminal/resize":
        return _handle_terminal_resize(handler, body)

    if parsed.path == "/api/terminal/close":
        return _handle_terminal_close(handler, body)

    # ── Cron API (POST) ──
    # See GET-side comment above: wrap in cron_profile_context so writes go
    # to the TLS-active profile's jobs.json instead of the process default.
    if parsed.path == "/api/crons/create":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_create(handler, body)

    if parsed.path == "/api/crons/batch":
        return _handle_cron_batch(handler, body)

    if parsed.path == "/api/crons/calendar":
        return _handle_cron_calendar(handler, body)

    if parsed.path == "/api/crons/calendar/create":
        return _handle_cron_calendar_create(handler, body)

    if parsed.path == "/api/crons/update":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_update(handler, body)

    if parsed.path == "/api/crons/delete":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_delete(handler, body)

    if parsed.path == "/api/crons/run":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_run(handler, body)

    if parsed.path == "/api/crons/pause":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_pause(handler, body)

    if parsed.path == "/api/crons/resume":
        from api.profiles import cron_profile_context

        with cron_profile_context():
            return _handle_cron_resume(handler, body)

    # ── File ops (POST) ──
    if parsed.path == "/api/file/delete":
        return _handle_file_delete(handler, body)

    if parsed.path == "/api/file/save":
        return _handle_file_save(handler, body)

    if parsed.path == "/api/file/create":
        return _handle_file_create(handler, body)

    if parsed.path == "/api/file/rename":
        return _handle_file_rename(handler, body)

    if parsed.path == "/api/file/create-dir":
        return _handle_create_dir(handler, body)

    if parsed.path == "/api/file/reveal":
        return _handle_file_reveal(handler, body)

    if parsed.path == "/api/file/path":
        return _handle_file_path(handler, body)

    # ── Workspace management (POST) ──
    if parsed.path == "/api/workspaces/add":
        return _handle_workspace_add(handler, body)

    if parsed.path == "/api/workspaces/remove":
        return _handle_workspace_remove(handler, body)

    if parsed.path == "/api/workspaces/rename":
        return _handle_workspace_rename(handler, body)

    if parsed.path == "/api/workspaces/reorder":
        return _handle_workspace_reorder(handler, body)

    # ── Approval (POST) ──
    if parsed.path == "/api/approval/respond":
        return _handle_approval_respond(handler, body)

    # ── Clarify (POST) ──
    if parsed.path == "/api/clarify/respond":
        return _handle_clarify_respond(handler, body)

    # ── Skills (POST) ──
    if parsed.path == "/api/skills/save":
        return _handle_skill_save(handler, body)

    if parsed.path == "/api/skills/delete":
        return _handle_skill_delete(handler, body)

    if parsed.path == "/api/skills/install-community":
        return _handle_skill_install_community(handler, body)

    if parsed.path == "/api/skills/uninstall-profile":
        return _handle_skill_uninstall_profile(handler, body)

    if parsed.path == "/api/user-skills/publish-from-profile":
        return _handle_user_skill_publish_from_profile(handler, body)

    if parsed.path == "/api/user-skills/install-to-profile":
        return _handle_user_skill_install_to_profile(handler, body)

    if parsed.path == "/api/user-skills/update":
        return _handle_user_skill_update(handler, body)

    if parsed.path == "/api/user-skills/file/update":
        return _handle_user_skill_file_update(handler, body)

    if parsed.path == "/api/user-skills/test-security":
        return _handle_user_skill_test_security(handler, body)

    if parsed.path == "/api/user-skills/test-availability":
        return _handle_user_skill_test_availability(handler, body)

    if parsed.path == "/api/user-skills/import/cancel":
        return _handle_user_skill_import_cancel(handler, body)

    # ── Memory (POST) ──
    if parsed.path == "/api/memory/write":
        return _handle_memory_write(handler, body)

    if parsed.path == "/api/profile/memory":
        return _handle_profile_memory_write(handler, body)

    if parsed.path == "/api/profile/user":
        return _handle_profile_user_write(handler, body)

    # ── Profile API (POST) ──
    if parsed.path == "/api/profile/switch":
        name = body.get("name", "").strip()
        if not name:
            return bad(handler, "name is required")
        try:
            from api.profiles import switch_profile, _validate_profile_name
            from api.helpers import build_profile_cookie
            if name != 'default':
                _validate_profile_name(name)
            # process_wide=False: don't mutate the process-global _active_profile.
            # Per-client profile is managed via cookie + thread-local (#798).
            result = switch_profile(name, process_wide=False)
            # Invalidate the models cache so the very next /api/models request
            # rebuilds from the new profile's config.yaml rather than returning
            # the old profile's cached model list (#1200 — profile-switch model bug).
            from api.config import invalidate_models_cache
            invalidate_models_cache()
            return j(handler, result, extra_headers={
                'Set-Cookie': build_profile_cookie(name),
            })
        except (ValueError, FileNotFoundError) as e:
            return bad(handler, _sanitize_error(e), 404)
        except RuntimeError as e:
            return bad(handler, str(e), 409)

    if parsed.path == "/api/profile/create-agent":
        return _handle_profile_agent_create(handler, body)

    if parsed.path == "/api/profile/install_profiles":
        return _handle_profile_install_profiles(handler, body)

    if parsed.path == "/api/profile/update-agent":
        return _handle_profile_agent_update(handler, body)

    if parsed.path in ("/api/profile/soul", "/api/profile/change_soul", "/api/profile/change-soul"):
        return _handle_profile_change_soul(handler, body)

    if parsed.path == "/api/profile/create":
        name = body.get("name", "").strip()
        if not name:
            return bad(handler, "name is required")
        import re as _re

        if not _re.match(r"^[a-z0-9][a-z0-9_-]{0,149}$", name):
            return bad(
                handler,
                "Invalid profile name: lowercase letters, numbers, hyphens, underscores only",
            )
        clone_from = body.get("clone_from")
        if clone_from is not None:
            clone_from = str(clone_from).strip()
            if not _re.match(r"^[a-z0-9][a-z0-9_-]{0,149}$", clone_from):
                return bad(handler, "Invalid clone_from name")
        base_url = body.get("base_url", "").strip() if body.get("base_url") else None
        api_key = body.get("api_key", "").strip() if body.get("api_key") else None
        if base_url and not base_url.startswith(("http://", "https://")):
            return bad(handler, "base_url must start with http:// or https://")
        try:
            from api.profiles import create_profile_api

            result = create_profile_api(
                name,
                clone_from=clone_from,
                clone_config=bool(body.get("clone_config", False)),
                base_url=base_url,
                api_key=api_key,
            )
            provider_sync = None
            try:
                from api.user_provider import optional_user_id_from_handler
                from api.user_provider_management import error_payload, sync_new_profile_if_enabled

                user_id = optional_user_id_from_handler(handler)
                if user_id:
                    provider_sync = sync_new_profile_if_enabled(user_id, name)
            except Exception as exc:
                sync_payload, _sync_status = error_payload(exc)
                provider_sync = {
                    "status": "sync_failed",
                    "error": sync_payload.get("error") or "Provider config sync failed",
                    "code": sync_payload.get("code") or "sync_failed",
                }
            return j(handler, {"ok": True, "profile": result, "provider_sync": provider_sync})
        except (ValueError, FileExistsError, RuntimeError) as e:
            return bad(handler, str(e))

    if parsed.path == "/api/profile/delete":
        name = body.get("name", "").strip()
        if not name:
            return bad(handler, "name is required")
        try:
            from api.profiles import delete_profile_api, _validate_profile_name

            _validate_profile_name(name)
            result = delete_profile_api(name)
            return j(handler, result)
        except (ValueError, FileNotFoundError) as e:
            return bad(handler, _sanitize_error(e))
        except RuntimeError as e:
            return bad(handler, str(e), 409)

    # ── Settings (POST) ──
    if parsed.path == "/api/settings":
        from api.auth import (
            create_session,
            is_auth_enabled,
            parse_cookie,
            set_auth_cookie,
            verify_session,
        )

        if "bot_name" in body:
            body["bot_name"] = (str(body["bot_name"]) or "").strip() or "Hermes"

        auth_enabled_before = is_auth_enabled()
        current_cookie = parse_cookie(handler)
        logged_in_before = bool(current_cookie and verify_session(current_cookie))
        requested_password = bool(
            isinstance(body.get("_set_password"), str)
            and body.get("_set_password", "").strip()
        )
        requested_clear_password = bool(body.get("_clear_password"))

        # #1560: HERMES_WEBUI_PASSWORD env var takes precedence in
        # api.auth.get_password_hash(), so writing password_hash to settings.json
        # has no effect on auth. Refuse loudly with 409 instead of silently
        # succeeding — the previous behaviour returned 200 + a green save toast
        # while every subsequent login still required the env-var password.
        if requested_password or requested_clear_password:
            if os.getenv("HERMES_WEBUI_PASSWORD", "").strip():
                return bad(
                    handler,
                    "HERMES_WEBUI_PASSWORD env var is set — it overrides the settings password. "
                    "Unset the env var and restart the server before changing the password here.",
                    409,
                )

        saved = save_settings(body)
        saved.pop("password_hash", None)  # never expose hash to client

        auth_enabled_after = is_auth_enabled()
        auth_just_enabled = bool(
            requested_password and auth_enabled_after and not auth_enabled_before
        )
        logged_in_after = logged_in_before
        new_cookie = None

        if auth_just_enabled and not logged_in_before:
            new_cookie = create_session()
            logged_in_after = True

        saved["auth_enabled"] = auth_enabled_after
        saved["logged_in"] = logged_in_after
        saved["auth_just_enabled"] = auth_just_enabled

        if not new_cookie:
            return j(handler, saved)

        response_body = json.dumps(saved, ensure_ascii=False, indent=2).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(response_body)))
        handler.send_header("Cache-Control", "no-store")
        set_auth_cookie(handler, new_cookie)
        _security_headers(handler)
        handler.end_headers()
        handler.wfile.write(response_body)
        return True

    if parsed.path == "/api/onboarding/oauth/start":
        from api.auth import is_auth_enabled
        import os as _os
        if not is_auth_enabled() and not _os.getenv("HERMES_WEBUI_ONBOARDING_OPEN"):
            import ipaddress
            try:
                _xff = handler.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                _xri = handler.headers.get("X-Real-IP", "").strip()
                _raw = handler.client_address[0]
                addr = ipaddress.ip_address(_xff or _xri or _raw)
                is_local = addr.is_loopback or addr.is_private
            except ValueError:
                is_local = False
            if not is_local:
                return bad(handler, "Onboarding OAuth is only available from local networks when auth is not enabled. To bypass this on a remote server, set HERMES_WEBUI_ONBOARDING_OPEN=1.", 403)
        try:
            return j(handler, start_onboarding_oauth_flow(body), extra_headers={"Cache-Control": "no-store"})
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), 500)

    if parsed.path == "/api/onboarding/oauth/cancel":
        try:
            return j(handler, cancel_onboarding_oauth_flow(body), extra_headers={"Cache-Control": "no-store"})
        except ValueError as e:
            return bad(handler, str(e))

    if parsed.path == "/api/onboarding/setup":
        # Writing API keys to disk - restrict to local/private networks unless auth is active.
        # In Docker, requests arrive from the bridge network (172.x.x.x), not 127.0.0.1,
        # even when the user accesses via localhost:8787 on the host.
        # Behind a reverse proxy (nginx/Caddy/Traefik) or SSH tunnel, X-Forwarded-For
        # carries the real origin IP — read it first before falling back to the raw socket addr.
        # HERMES_WEBUI_ONBOARDING_OPEN=1 lets operators on remote servers explicitly bypass
        # the check when they control network access themselves (e.g. firewall + VPN).
        from api.auth import is_auth_enabled
        import os as _os
        if not is_auth_enabled() and not _os.getenv("HERMES_WEBUI_ONBOARDING_OPEN"):
            import ipaddress
            try:
                # Prefer forwarded headers set by reverse proxies
                _xff = handler.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                _xri = handler.headers.get("X-Real-IP", "").strip()
                _raw = handler.client_address[0]
                _ip_str = _xff or _xri or _raw
                addr = ipaddress.ip_address(_ip_str)
                is_local = addr.is_loopback or addr.is_private
            except ValueError:
                is_local = False
            if not is_local:
                return bad(handler, "Onboarding setup is only available from local networks when auth is not enabled. To bypass this on a remote server, set HERMES_WEBUI_ONBOARDING_OPEN=1.", 403)
        try:
            return j(handler, apply_onboarding_setup(body))
        except ValueError as e:
            return bad(handler, str(e))
        except RuntimeError as e:
            return bad(handler, str(e), 500)

    if parsed.path == "/api/onboarding/complete":
        return j(handler, complete_onboarding())

    if parsed.path == "/api/onboarding/probe":
        # Probe a self-hosted provider endpoint (#1499).  Validates the
        # configured base URL is reachable + parses /models, returns the
        # model catalog so the wizard can populate its dropdown.
        # Read-only: no config.yaml or .env writes happen here.  Same local-
        # network gate as /api/onboarding/setup (also writing-adjacent in
        # spirit because it carries an api_key the user typed).
        from api.auth import is_auth_enabled
        import os as _os
        if not is_auth_enabled() and not _os.getenv("HERMES_WEBUI_ONBOARDING_OPEN"):
            import ipaddress
            try:
                _xff = handler.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                _xri = handler.headers.get("X-Real-IP", "").strip()
                _raw = handler.client_address[0]
                _ip_str = _xff or _xri or _raw
                addr = ipaddress.ip_address(_ip_str)
                is_local = addr.is_loopback or addr.is_private
            except ValueError:
                is_local = False
            if not is_local:
                return bad(handler, "Onboarding probe is only available from local networks when auth is not enabled. To bypass this on a remote server, set HERMES_WEBUI_ONBOARDING_OPEN=1.", 403)
        provider = str((body or {}).get("provider") or "").strip().lower()
        base_url = str((body or {}).get("base_url") or "")
        api_key = str((body or {}).get("api_key") or "").strip() or None
        try:
            return j(handler, probe_provider_endpoint(provider, base_url, api_key))
        except Exception as e:
            return bad(handler, f"probe failed: {e}", 500)

    # ── Session pin (POST) ──
    if parsed.path == "/api/session/pin":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        with _get_session_agent_lock(body["session_id"]):
            s.pinned = bool(body.get("pinned", True))
            s.save()
        return j(handler, {"ok": True, "session": s.compact()})

    # ── Session archive (POST) ──
    if parsed.path == "/api/session/archive":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        sid = body["session_id"]
        try:
            s = get_session(sid)
        except KeyError:
            cli_meta = _lookup_cli_session_metadata(sid)
            if not cli_meta:
                return bad(handler, "Session not found", 404)
            if cli_meta.get("read_only"):
                return bad(handler, "Read-only imported sessions cannot be archived from WebUI", 400)
            if _is_messaging_session_record(cli_meta):
                s = Session(
                    session_id=sid,
                    title=cli_meta.get("title") or title_from(get_cli_session_messages(sid), "CLI Session"),
                    workspace=get_last_workspace(),
                    messages=[],
                    model=cli_meta.get("model") or "unknown",
                    created_at=cli_meta.get("created_at"),
                    updated_at=cli_meta.get("updated_at"),
                )
                s.is_cli_session = True
                s.source_tag = cli_meta.get("source_tag")
                s.raw_source = cli_meta.get("raw_source") or cli_meta.get("source_tag")
                s.session_source = cli_meta.get("session_source")
                s.source_label = cli_meta.get("source_label")
                s.user_id = cli_meta.get("user_id")
                s.chat_id = cli_meta.get("chat_id")
                s.chat_type = cli_meta.get("chat_type")
                s.thread_id = cli_meta.get("thread_id")
                s.session_key = cli_meta.get("session_key")
                s.platform = cli_meta.get("platform")
                s.save(touch_updated_at=False)
            else:
                msgs = get_cli_session_messages(sid)
                if not msgs:
                    return bad(handler, "Session not found", 404)
                s = import_cli_session(
                    sid,
                    cli_meta.get("title") or title_from(msgs, "CLI Session"),
                    msgs,
                    cli_meta.get("model") or "unknown",
                    profile=cli_meta.get("profile"),
                    created_at=cli_meta.get("created_at"),
                    updated_at=cli_meta.get("updated_at"),
                )
                s.is_cli_session = True
                s.source_tag = cli_meta.get("source_tag")
                s.raw_source = cli_meta.get("raw_source") or cli_meta.get("source_tag")
                s.session_source = cli_meta.get("session_source")
                s.source_label = cli_meta.get("source_label")
                s.user_id = cli_meta.get("user_id")
                s.chat_id = cli_meta.get("chat_id")
                s.chat_type = cli_meta.get("chat_type")
                s.thread_id = cli_meta.get("thread_id")
                s.session_key = cli_meta.get("session_key")
                s.platform = cli_meta.get("platform")
        with _get_session_agent_lock(sid):
            s.archived = bool(body.get("archived", True))
            s.save(touch_updated_at=False)
        return j(handler, {"ok": True, "session": s.compact()})

    # ── Session move to project (POST) ──
    if parsed.path == "/api/session/move":
        try:
            require(body, "session_id")
        except ValueError as e:
            return bad(handler, str(e))
        try:
            s = get_session(body["session_id"])
        except KeyError:
            return bad(handler, "Session not found", 404)
        # #1614: refuse moves into a project owned by another profile.
        target_pid = body.get("project_id") or None
        if target_pid:
            from api.profiles import get_active_profile_name
            active_profile = get_active_profile_name()
            target = next(
                (p for p in load_projects() if p["project_id"] == target_pid),
                None,
            )
            if not target:
                return bad(handler, "Project not found", 404)
            if not _profiles_match(target.get("profile"), active_profile):
                return bad(handler, "Project not found", 404)
        with _get_session_agent_lock(body["session_id"]):
            s.project_id = target_pid
            s.save()
        return j(handler, {"ok": True, "session": s.compact()})

    # ── Project CRUD (POST) ──
    if parsed.path == "/api/projects/create":
        try:
            require(body, "name")
        except ValueError as e:
            return bad(handler, str(e))
        import re as _re
        from api.profiles import get_active_profile_name

        name = body["name"].strip()[:128]
        if not name:
            return bad(handler, "name required")
        color = body.get("color")
        if color and not _re.match(r"^#[0-9a-fA-F]{3,8}$", color):
            return bad(handler, "Invalid color format")
        projects = load_projects()
        proj = {
            "project_id": uuid.uuid4().hex[:12],
            "name": name,
            "color": color,
            "profile": get_active_profile_name() or 'default',
            "created_at": time.time(),
        }
        projects.append(proj)
        save_projects(projects)
        return j(handler, {"ok": True, "project": proj})

    if parsed.path == "/api/projects/rename":
        try:
            require(body, "project_id", "name")
        except ValueError as e:
            return bad(handler, str(e))
        import re as _re
        from api.profiles import get_active_profile_name

        projects = load_projects()
        proj = next(
            (p for p in projects if p["project_id"] == body["project_id"]), None
        )
        if not proj:
            return bad(handler, "Project not found", 404)
        # #1614: a project can only be renamed by the profile that owns it.
        active_profile = get_active_profile_name()
        if not _profiles_match(proj.get("profile"), active_profile):
            return bad(handler, "Project not found", 404)
        proj["name"] = body["name"].strip()[:128]
        if "color" in body:
            color = body["color"]
            if color and not _re.match(r"^#[0-9a-fA-F]{3,8}$", color):
                return bad(handler, "Invalid color format")
            proj["color"] = color
        save_projects(projects)
        return j(handler, {"ok": True, "project": proj})

    if parsed.path == "/api/projects/delete":
        try:
            require(body, "project_id")
        except ValueError as e:
            return bad(handler, str(e))
        from api.profiles import get_active_profile_name
        projects = load_projects()
        proj = next(
            (p for p in projects if p["project_id"] == body["project_id"]), None
        )
        if not proj:
            return bad(handler, "Project not found", 404)
        # #1614: a project can only be deleted by the profile that owns it.
        active_profile = get_active_profile_name()
        if not _profiles_match(proj.get("profile"), active_profile):
            return bad(handler, "Project not found", 404)
        projects = [p for p in projects if p["project_id"] != body["project_id"]]
        save_projects(projects)
        # Unassign all sessions that belonged to this project
        if SESSION_INDEX_FILE.exists():
            try:
                index = json.loads(SESSION_INDEX_FILE.read_text(encoding="utf-8"))
                for entry in index:
                    if entry.get("project_id") == body["project_id"]:
                        try:
                            s = get_session(entry["session_id"])
                            s.project_id = None
                            s.save()
                        except Exception:
                            logger.debug("Failed to update session %s", entry.get("session_id"))
            except Exception:
                logger.debug("Failed to load session index for project unlink")
        return j(handler, {"ok": True})

    # ── Session import from JSON (POST) ──
    if parsed.path == "/api/session/import":
        return _handle_session_import(handler, body)

    # ── Self-update (POST) ──
    if parsed.path == "/api/updates/apply":
        target = body.get("target", "")
        if target not in ("webui", "agent"):
            return bad(handler, 'target must be "webui" or "agent"')
        from api.updates import apply_update

        return j(handler, apply_update(target))

    if parsed.path == "/api/updates/force":
        target = body.get("target", "")
        if target not in ("webui", "agent"):
            return bad(handler, 'target must be "webui" or "agent"')
        from api.updates import apply_force_update

        return j(handler, apply_force_update(target))

    # ── CLI session import (POST) ──
    if parsed.path == "/api/session/import_cli":
        return _handle_session_import_cli(handler, body)

    # ── Auth endpoints (POST) ──
    if parsed.path == "/api/auth/token-login":
        from api.auth import create_session, set_auth_cookie, verify_api_token

        record = verify_api_token(body.get("token"), handler.headers.get("Origin"))
        if not record:
            return bad(handler, "Invalid token", 401)
        cookie_val = create_session()
        payload = {"ok": True, "token_id": str(record.get("id") or "")}
        raw = json.dumps(payload).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.send_header("Cache-Control", "no-store")
        _security_headers(handler)
        set_auth_cookie(handler, cookie_val)
        handler.end_headers()
        handler.wfile.write(raw)
        return True

    if parsed.path == "/api/auth/login":
        from api.auth import (
            verify_password,
            create_session,
            set_auth_cookie,
            is_auth_enabled,
        )
        from api.auth import _check_login_rate, _record_login_attempt

        if not is_auth_enabled():
            return j(handler, {"ok": True, "message": "Auth not enabled"})
        client_ip = handler.client_address[0]
        if not _check_login_rate(client_ip):
            return j(
                handler,
                {"error": "Too many attempts. Try again in a minute."},
                status=429,
            )
        password = body.get("password", "")
        if not verify_password(password):
            _record_login_attempt(client_ip)
            return bad(handler, "Invalid password", 401)
        cookie_val = create_session()
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Cache-Control", "no-store")
        _security_headers(handler)
        set_auth_cookie(handler, cookie_val)
        handler.end_headers()
        handler.wfile.write(json.dumps({"ok": True}).encode())
        return True

    if parsed.path == "/api/auth/logout":
        from api.auth import clear_auth_cookie, invalidate_session, parse_cookie

        cookie_val = parse_cookie(handler)
        if cookie_val:
            invalidate_session(cookie_val)
        handler.send_response(200)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Cache-Control", "no-store")
        _security_headers(handler)
        clear_auth_cookie(handler)
        handler.end_headers()
        handler.wfile.write(json.dumps({"ok": True}).encode())
        return True

    # ── Checkpoints / Rollback (POST) ──
    if parsed.path == "/api/rollback/restore":
        if not body:
            return bad(handler, "request body is required")
        workspace = body.get("workspace", "")
        checkpoint = body.get("checkpoint", "")
        if not workspace or not checkpoint:
            return bad(handler, "workspace and checkpoint are required")
        try:
            from api.rollback import restore_checkpoint
            return j(handler, restore_checkpoint(workspace, checkpoint))
        except ValueError as e:
            return bad(handler, str(e))
        except Exception as e:
            logger.exception("rollback/restore failed")
            return bad(handler, str(e), status=500)

    return False  # 404


def dispatch_patch(handler, parsed) -> bool:
    """Handle all PATCH routes. Returns True if handled, False for 404."""
    _sync_routes_bindings()
    if not _check_csrf(handler):
        return j(handler, {"error": "Cross-origin request rejected"}, status=403)
    body = read_body(handler)
    if parsed.path.startswith("/api/kanban/"):
        from api.kanban_bridge import handle_kanban_patch

        result = handle_kanban_patch(handler, parsed, body)
        if result is False:
            return _kanban_unknown_endpoint(handler, parsed, "PATCH")
        return True
    return False


def dispatch_delete(handler, parsed) -> bool:
    """Handle all DELETE routes. Returns True if handled, False for 404."""
    _sync_routes_bindings()
    if not _check_csrf(handler):
        return j(handler, {"error": "Cross-origin request rejected"}, status=403)
    body = read_body(handler)
    if parsed.path.startswith("/api/kanban/"):
        from api.kanban_bridge import handle_kanban_delete

        result = handle_kanban_delete(handler, parsed, body)
        if result is False:
            return _kanban_unknown_endpoint(handler, parsed, "DELETE")
        return True
    return False
