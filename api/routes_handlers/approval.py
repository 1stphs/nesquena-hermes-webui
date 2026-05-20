"""Approval and clarify endpoint handlers re-exported by api.routes."""

from urllib.parse import parse_qs

from api.routes_handlers._base import _routes_binding


def _handle_approval_pending(handler, parsed):
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    with _routes_binding("_lock"):
        queue = _routes_binding("_pending").get(sid)
        # Support both the new list format and a legacy single-dict value.
        if isinstance(queue, list):
            p = queue[0] if queue else None
            total = len(queue)
        elif queue:
            p = queue
            total = 1
        else:
            p = None
            total = 0
    if p:
        return _routes_binding("j")(handler, {"pending": dict(p), "pending_count": total})
    return _routes_binding("j")(handler, {"pending": None, "pending_count": 0})


def _handle_approval_inject(handler, parsed):
    """Inject a fake pending approval -- loopback-only, used by automated tests."""
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    key = qs.get("pattern_key", ["test_pattern"])[0]
    cmd = qs.get("command", ["rm -rf /tmp/test"])[0]
    if sid:
        _routes_binding("submit_pending")(
            sid,
            {
                "command": cmd,
                "pattern_key": key,
                "pattern_keys": [key],
                "description": "test pattern",
            },
        )
        return _routes_binding("j")(handler, {"ok": True, "session_id": sid})
    return _routes_binding("j")(handler, {"error": "session_id required"}, status=400)


def _handle_clarify_pending(handler, parsed):
    sid = parse_qs(parsed.query).get("session_id", [""])[0]
    pending = _routes_binding("get_clarify_pending")(sid)
    if pending:
        return _routes_binding("j")(handler, {"pending": pending})
    return _routes_binding("j")(handler, {"pending": None})


def _handle_clarify_inject(handler, parsed):
    """Inject a fake pending clarify prompt -- loopback-only, used by automated tests."""
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    question = qs.get("question", ["Which option?"])[0]
    choices = qs.get("choices", [])
    if sid:
        _routes_binding("submit_clarify_pending")(
            sid,
            {
                "question": question,
                "choices_offered": choices,
                "session_id": sid,
                "kind": "clarify",
            },
        )
        return _routes_binding("j")(handler, {"ok": True, "session_id": sid})
    return _routes_binding("j")(handler, {"error": "session_id required"}, status=400)


def _handle_clarify_respond(handler, body):
    sid = body.get("session_id", "")
    if not sid:
        return _routes_binding("bad")(handler, "session_id is required")
    response = body.get("response")
    if response is None:
        response = body.get("answer")
    if response is None:
        response = body.get("choice")
    response = str(response or "").strip()
    if not response:
        return _routes_binding("bad")(handler, "response is required")
    _routes_binding("resolve_clarify")(sid, response, resolve_all=False)
    return _routes_binding("j")(handler, {"ok": True, "response": response})
