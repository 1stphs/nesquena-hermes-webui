"""Terminal endpoint handlers re-exported by api.routes."""

from api.routes_handlers._base import _routes_binding


def _terminal_session_and_workspace(body_or_query):
    sid = str(body_or_query.get("session_id", "")).strip()
    if not sid:
        raise ValueError("session_id required")
    try:
        s = _routes_binding("get_session")(sid)
    except KeyError:
        raise KeyError("Session not found")
    workspace = _routes_binding("resolve_trusted_workspace")(getattr(s, "workspace", "") or "")
    return sid, workspace


def _handle_terminal_start(handler, body):
    try:
        sid, workspace = _terminal_session_and_workspace(body)
        from api.terminal import start_terminal

        term = start_terminal(
            sid,
            workspace,
            rows=int(body.get("rows") or 24),
            cols=int(body.get("cols") or 80),
            restart=bool(body.get("restart")),
        )
        return _routes_binding("j")(
            handler,
            {
                "ok": True,
                "session_id": sid,
                "workspace": term.workspace,
                "running": term.is_alive(),
            },
        )
    except KeyError as e:
        return _routes_binding("bad")(handler, str(e), 404)
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e), 400)
    except Exception as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)


def _handle_terminal_input(handler, body):
    try:
        _routes_binding("require")(body, "session_id")
        data = str(body.get("data", ""))
        if len(data) > 8192:
            return _routes_binding("bad")(handler, "input too large", 413)
        from api.terminal import write_terminal

        write_terminal(body["session_id"], data)
        return _routes_binding("j")(handler, {"ok": True})
    except KeyError as e:
        return _routes_binding("bad")(handler, str(e), 404)
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e), 400)
    except Exception as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)


def _handle_terminal_resize(handler, body):
    try:
        _routes_binding("require")(body, "session_id")
        from api.terminal import resize_terminal

        resize_terminal(
            body["session_id"],
            rows=int(body.get("rows") or 24),
            cols=int(body.get("cols") or 80),
        )
        return _routes_binding("j")(handler, {"ok": True})
    except KeyError as e:
        return _routes_binding("bad")(handler, str(e), 404)
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e), 400)
    except Exception as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 500)


def _handle_terminal_close(handler, body):
    try:
        _routes_binding("require")(body, "session_id")
        from api.terminal import close_terminal

        closed = close_terminal(body["session_id"])
        return _routes_binding("j")(handler, {"ok": True, "closed": closed})
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e), 400)
