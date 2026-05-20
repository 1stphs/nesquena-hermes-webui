"""Session import and conversation-round endpoint handlers re-exported by api.routes."""

from api.routes_handlers._base import _routes_binding


def _handle_conversation_rounds(handler, body):
    """Return conversation-round count for a gateway session.

    Request body::

        { "session_id": "...", "since": <unix_ts_or_iso> }

    Response::

        { "ok": true, "rounds": 12, "threshold": 10, "should_show": true }
    """
    try:
        _routes_binding("require")(body, "session_id")
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))

    sid = str(body.get("session_id") or "").strip()
    if not sid:
        return _routes_binding("bad")(handler, "session_id is required")

    since = body.get("since")
    if since is not None:
        try:
            since = float(since)
        except (TypeError, ValueError):
            return _routes_binding("bad")(handler, "since must be a unix timestamp (number)")

    count_conversation_rounds = _routes_binding("count_conversation_rounds")
    threshold = _routes_binding("CONVERSATION_ROUND_THRESHOLD")

    rounds = count_conversation_rounds(sid, since=since)
    return _routes_binding("j")(handler, {
        "ok": True,
        "rounds": rounds,
        "threshold": threshold,
        "should_show": rounds >= threshold,
    })


def _handle_session_import(handler, body):
    """Import a session from a JSON export. Creates a new session with a new ID."""
    if not body or not isinstance(body, dict):
        return _routes_binding("bad")(handler, "Request body must be a JSON object")
    messages = body.get("messages")
    if not isinstance(messages, list):
        return _routes_binding("bad")(handler, 'JSON must contain a "messages" array')
    title = body.get("title", "Imported session")
    workspace = body.get("workspace", str(_routes_binding("DEFAULT_WORKSPACE")))
    model = body.get("model", _routes_binding("DEFAULT_MODEL"))
    s = _routes_binding("Session")(
        title=title,
        workspace=workspace,
        model=model,
        messages=messages,
        tool_calls=body.get("tool_calls", []),
    )
    s.pinned = body.get("pinned", False)
    with _routes_binding("LOCK"):
        sessions = _routes_binding("SESSIONS")
        sessions[s.session_id] = s
        sessions.move_to_end(s.session_id)
        while len(sessions) > _routes_binding("SESSIONS_MAX"):
            sessions.popitem(last=False)
    s.save()
    return _routes_binding("j")(handler, {"ok": True, "session": s.compact() | {"messages": s.messages}})
