"""轻量级请求并发限制。

用于高成本入口的防崩保护：
- chat/start：限制全局并发和单用户并发
- session/new：限制全局并发
- upload / extract / transcribe / skill import：限制全局并发
"""

from __future__ import annotations

from dataclasses import dataclass
import threading


REQUEST_LIMIT_MESSAGE = "当前使用人数较多，请稍后再试"
REQUEST_LIMIT_CODE = "REQUEST_CONCURRENCY_LIMIT"
REQUEST_LIMIT_RETRY_AFTER = "10"

CHAT_START_GLOBAL_LIMIT = 50
CHAT_START_PER_USER_LIMIT = 2
SESSION_CREATE_LIMIT = 40
UPLOAD_LIMIT = 10

_ANONYMOUS_USER_ID = "__anonymous__"


@dataclass(frozen=True)
class RequestLimitRejection:
    kind: str
    limit: int
    active: int
    message: str = REQUEST_LIMIT_MESSAGE
    code: str = REQUEST_LIMIT_CODE
    retry_after: str = REQUEST_LIMIT_RETRY_AFTER


_REQUEST_LIMIT_LOCK = threading.Lock()
_REQUEST_LIMIT_ACTIVE_COUNTS = {
    "session_create": 0,
    "upload": 0,
}

_CHAT_LIMIT_LOCK = threading.Lock()
_CHAT_LIMIT_ACTIVE_COUNT = 0
_CHAT_LIMIT_ACTIVE_COUNTS_BY_USER: dict[str, int] = {}
_CHAT_LIMIT_STREAM_USERS: dict[str, str] = {}


def _normalize_user_id(user_id: str | None) -> str:
    normalized = str(user_id or "").strip()
    return normalized or _ANONYMOUS_USER_ID


def _build_rejection(kind: str, *, limit: int, active: int) -> RequestLimitRejection:
    return RequestLimitRejection(kind=kind, limit=limit, active=active)


def request_limit_payload(rejection: RequestLimitRejection) -> dict:
    return {
        "error": rejection.message,
        "code": rejection.code,
        "kind": rejection.kind,
        "limit": rejection.limit,
        "active": rejection.active,
    }


def request_limit_headers(
    rejection: RequestLimitRejection,
    *,
    close_connection: bool = False,
) -> dict[str, str]:
    headers = {
        "Retry-After": rejection.retry_after,
    }
    if close_connection:
        headers["Connection"] = "close"
    return headers


def try_acquire_session_create_slot() -> RequestLimitRejection | None:
    with _REQUEST_LIMIT_LOCK:
        active = _REQUEST_LIMIT_ACTIVE_COUNTS["session_create"]
        if active >= SESSION_CREATE_LIMIT:
            return _build_rejection("session_create", limit=SESSION_CREATE_LIMIT, active=active)
        _REQUEST_LIMIT_ACTIVE_COUNTS["session_create"] = active + 1
    return None


def release_session_create_slot() -> None:
    with _REQUEST_LIMIT_LOCK:
        active = _REQUEST_LIMIT_ACTIVE_COUNTS["session_create"]
        if active <= 0:
            return
        _REQUEST_LIMIT_ACTIVE_COUNTS["session_create"] = active - 1


def try_acquire_upload_slot() -> RequestLimitRejection | None:
    with _REQUEST_LIMIT_LOCK:
        active = _REQUEST_LIMIT_ACTIVE_COUNTS["upload"]
        if active >= UPLOAD_LIMIT:
            return _build_rejection("upload", limit=UPLOAD_LIMIT, active=active)
        _REQUEST_LIMIT_ACTIVE_COUNTS["upload"] = active + 1
    return None


def release_upload_slot() -> None:
    with _REQUEST_LIMIT_LOCK:
        active = _REQUEST_LIMIT_ACTIVE_COUNTS["upload"]
        if active <= 0:
            return
        _REQUEST_LIMIT_ACTIVE_COUNTS["upload"] = active - 1


def try_acquire_chat_start_slot(user_id: str | None, stream_id: str | None) -> RequestLimitRejection | None:
    global _CHAT_LIMIT_ACTIVE_COUNT

    normalized_user_id = _normalize_user_id(user_id)
    normalized_stream_id = str(stream_id or "").strip()
    if not normalized_stream_id:
        return _build_rejection("chat_start", limit=CHAT_START_GLOBAL_LIMIT, active=CHAT_START_GLOBAL_LIMIT)

    with _CHAT_LIMIT_LOCK:
        global_active = _CHAT_LIMIT_ACTIVE_COUNT
        if global_active >= CHAT_START_GLOBAL_LIMIT:
            return _build_rejection("chat_start_global", limit=CHAT_START_GLOBAL_LIMIT, active=global_active)

        user_active = _CHAT_LIMIT_ACTIVE_COUNTS_BY_USER.get(normalized_user_id, 0)
        if user_active >= CHAT_START_PER_USER_LIMIT:
            return _build_rejection("chat_start_user", limit=CHAT_START_PER_USER_LIMIT, active=user_active)

        _CHAT_LIMIT_ACTIVE_COUNT = global_active + 1
        _CHAT_LIMIT_ACTIVE_COUNTS_BY_USER[normalized_user_id] = user_active + 1
        _CHAT_LIMIT_STREAM_USERS[normalized_stream_id] = normalized_user_id
    return None


def release_chat_start_slot(stream_id: str | None) -> None:
    global _CHAT_LIMIT_ACTIVE_COUNT

    normalized_stream_id = str(stream_id or "").strip()
    if not normalized_stream_id:
        return

    with _CHAT_LIMIT_LOCK:
        user_id = _CHAT_LIMIT_STREAM_USERS.pop(normalized_stream_id, None)
        if not user_id:
            return

        if _CHAT_LIMIT_ACTIVE_COUNT > 0:
            _CHAT_LIMIT_ACTIVE_COUNT -= 1

        user_active = _CHAT_LIMIT_ACTIVE_COUNTS_BY_USER.get(user_id, 0)
        if user_active <= 1:
            _CHAT_LIMIT_ACTIVE_COUNTS_BY_USER.pop(user_id, None)
        else:
            _CHAT_LIMIT_ACTIVE_COUNTS_BY_USER[user_id] = user_active - 1


def reset_request_limits_for_tests() -> None:
    global _CHAT_LIMIT_ACTIVE_COUNT

    with _REQUEST_LIMIT_LOCK:
        for key in _REQUEST_LIMIT_ACTIVE_COUNTS:
            _REQUEST_LIMIT_ACTIVE_COUNTS[key] = 0
    with _CHAT_LIMIT_LOCK:
        _CHAT_LIMIT_ACTIVE_COUNT = 0
        _CHAT_LIMIT_ACTIVE_COUNTS_BY_USER.clear()
        _CHAT_LIMIT_STREAM_USERS.clear()
