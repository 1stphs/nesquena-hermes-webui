import queue


_approval_sse_subscribers: dict[str, list[queue.Queue]] = {}


def _approval_sse_notify_subscribers(session_id: str, head: dict | None, total: int) -> None:
    payload = {"pending": dict(head) if head else None, "pending_count": total}
    subs = _approval_sse_subscribers.get(session_id, ())
    for q in subs:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass
