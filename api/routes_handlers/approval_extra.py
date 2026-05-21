"""Endpoint handlers migrated from api.routes for routes step3."""

from __future__ import annotations

from api.routes_handlers._base import _sync_routes_bindings


def _handle_approval_respond(handler, body):
    _sync_routes_bindings(globals())
    sid = body.get("session_id", "")
    if not sid:
        return bad(handler, "session_id is required")
    choice = body.get("choice", "deny")
    if choice not in ("once", "session", "always", "deny"):
        return bad(handler, f"Invalid choice: {choice}")
    approval_id = body.get("approval_id", "")

    # Pop the targeted entry from the pending queue by approval_id.
    # Falls back to popping the first entry for backward-compat with old clients.
    pending = None
    with _lock:
        queue = _pending.get(sid)
        if isinstance(queue, list):
            if approval_id:
                # Find and remove the specific entry by approval_id.
                for i, entry in enumerate(queue):
                    if entry.get("approval_id") == approval_id:
                        pending = queue.pop(i)
                        break
                else:
                    # approval_id not found -- fall back to oldest entry.
                    pending = queue.pop(0) if queue else None
            else:
                pending = queue.pop(0) if queue else None
            if not queue:
                _pending.pop(sid, None)
        elif queue:
            # Legacy single-dict value.
            pending = _pending.pop(sid, None)
        # Notify SSE subscribers of the new head (or empty state) so the UI
        # surfaces any trailing approvals that were queued behind this one
        # without waiting for the next submit_pending. Without this, a parallel
        # tool-call scenario (#527) would leave the second approval invisible
        # in the SSE path until the next event ever fired (the agent thread
        # would be parked indefinitely from the user's perspective).
        if isinstance(_pending.get(sid), list) and _pending[sid]:
            _approval_sse_notify_locked(sid, _pending[sid][0], len(_pending[sid]))
        else:
            _approval_sse_notify_locked(sid, None, 0)

    if pending:
        keys = pending.get("pattern_keys") or [pending.get("pattern_key", "")]
        if choice in ("once", "session"):
            for k in keys:
                approve_session(sid, k)
        elif choice == "always":
            for k in keys:
                approve_session(sid, k)
                approve_permanent(k)
            save_permanent_allowlist(_permanent_approved)
    # Unblock the agent thread waiting in the gateway approval queue.
    # This is the primary signal when streaming is active — the agent
    # thread is parked in entry.event.wait() and needs to be woken up.
    resolve_gateway_approval(sid, choice, resolve_all=False)
    return j(handler, {"ok": True, "choice": choice})
