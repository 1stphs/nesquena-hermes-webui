"""User-scoped AI provider resolver for the external Hermes frontend.

This module treats NoCoBase providers as optional runtime overlays. The stored
provider resolver is used only when a request carries frontend-supplied
X-User-Id context. Requests without user context keep the normal Hermes model
fallback. The server-side NoCoBase token is used only for table access; Hermes
WebUI determines the current user from X-User-Id.
"""

from __future__ import annotations

import copy
import hashlib
import http.cookies
import ipaddress
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from api.core.helpers import _redact_text

logger = logging.getLogger(__name__)

USER_PROVIDER_COLLECTION = "hermes_user_ai_providers"
GLOBAL_PROVIDER_COLLECTION = "hermes_providers"
PROFILE_COLLECTION = "hermes_profiles"
PROFILE_PROVIDER_FOREIGN_KEY = "hermes_providers_id"
# Legacy env names are kept as public constants for compatibility, but runtime
# provider lookup no longer depends on these switches.
X_USER_ID_CONTEXT_ENABLE_ENV = "HERMES_USER_PROVIDER_ENABLE_X_USER_ID_CONTEXT"
UNTRUSTED_CONTEXT_ENABLE_ENV = "HERMES_USER_PROVIDER_ENABLE_UNTRUSTED_CONTEXT"
LEGACY_NOCOBASE_AUTH_ENABLE_ENV = "HERMES_USER_PROVIDER_ENABLE_NOCOBASE_AUTH"
SUPPORTED_API_MODES = {"anthropic_messages", "codex_responses", "chat_completions"}
NOCOBASE_PROVIDER_API_MODE_MAP = {
    "anthropic": "anthropic_messages",
    "anthropic_messages": "anthropic_messages",
    "openai-response": "codex_responses",
    "openai-responses": "codex_responses",
    "codex_responses": "codex_responses",
    "openai-chat-complete": "chat_completions",
    "openai-chat-completion": "chat_completions",
    "openai-chat-completions": "chat_completions",
    "chat_completions": "chat_completions",
}
VALID_THINKING_LEVELS = {"minimal", "low", "medium", "high", "xhigh"}
CANONICAL_AGENT_PROVIDER = "custom"
DEFAULT_NOCOBASE_BASE_URL = "https://www.foxuai.com"
NOCOBASE_TIMEOUT_SECONDS = 6.0
PROVIDER_TEST_TIMEOUT_SECONDS = 8.0
PROVIDER_MODELS_TIMEOUT_SECONDS = 6.0
PROVIDER_RESPONSE_MAX_BYTES = 512 * 1024
USER_MODELS_CACHE_TTL_SECONDS = 60.0
PROVIDER_WRITE_FIELDS = {
    "name",
    "provider_slug",
    "base_url",
    "model_name",
    "api_mode",
    "thinking_level",
    "api_key",
    "status",
}
BLOCKED_PROVIDER_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
}
BLOCKED_PROVIDER_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}


class UserProviderAuthError(RuntimeError):
    """Raised when current-user or profile ownership checks fail."""

    def __init__(self, message: str, *, status: int = 403, code: str = "forbidden") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class UserProviderLookupError(RuntimeError):
    """Raised for optional Provider lookup failures."""


@dataclass(frozen=True)
class UserProviderResolution:
    status: str
    reason: str
    user_id: str
    provider: dict[str, Any] | None = None
    error: str = ""
    profile_id: str = ""
    profile_name: str = ""

    @property
    def is_active(self) -> bool:
        return self.status == "active" and bool(self.provider)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """拒绝自动跳转，避免用户填写的 base_url 被跳到非预期地址。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirectHandler())
_USER_MODELS_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}


def _hash_text(value: str | None, *, length: int = 16) -> str:
    if not value:
        return ""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:length]


def _normalize_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if len(normalized) > 128 or any(ch in normalized for ch in "\r\n\t"):
        raise UserProviderAuthError("Invalid user context", status=400, code="invalid_user_context")
    return normalized


def normalize_nocobase_provider_api_mode(raw: Any) -> str:
    return NOCOBASE_PROVIDER_API_MODE_MAP.get(str(raw or "").strip().lower(), "")


def _is_nocobase_false(value: Any) -> bool:
    if value is False or value is None:
        return value is False
    if isinstance(value, (int, float)):
        return value == 0
    return str(value).strip().lower() in {"false", "0", "no", "off"}


def _is_nocobase_true(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"true", "1", "yes", "on"}


def _normalize_runtime_base_url(base_url: str, api_mode: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    suffixes: tuple[str, ...] = ()
    if api_mode in {"chat_completions", "codex_responses"}:
        suffixes = ("/chat/completions", "/responses")
    elif api_mode == "anthropic_messages":
        suffixes = ("/v1/messages", "/messages")
    lower = normalized.lower()
    for suffix in suffixes:
        if lower.endswith(suffix):
            normalized = normalized[: -len(suffix)].rstrip("/")
            break
    return normalized


def _header_value(headers, name: str) -> str:
    if not headers:
        return ""
    value = headers.get(name, "")
    if value:
        return str(value)
    target = name.lower()
    if isinstance(headers, dict):
        for key, candidate in headers.items():
            if str(key).lower() == target:
                return str(candidate)
    return ""


def _user_context_ids_from_handler(handler) -> tuple[str, str]:
    headers = getattr(handler, "headers", None)
    header_user_id = _normalize_id(_header_value(headers, "X-User-Id"))
    cookie_user_id = ""
    cookie_header = _header_value(headers, "Cookie")
    if cookie_header:
        cookie = http.cookies.SimpleCookie()
        try:
            cookie.load(cookie_header)
            morsel = cookie.get("X-User-Id")
            cookie_user_id = _normalize_id(morsel.value if morsel else "")
        except http.cookies.CookieError:
            cookie_user_id = ""
    return header_user_id, cookie_user_id


def optional_user_id_from_handler(handler) -> str | None:
    """Read optional frontend-supplied X-User-Id user context."""

    header_user_id, cookie_user_id = _user_context_ids_from_handler(handler)
    if header_user_id and cookie_user_id and header_user_id != cookie_user_id:
        raise UserProviderAuthError("User context mismatch", status=400, code="user_context_mismatch")
    return header_user_id or cookie_user_id or None


def current_user_id_from_handler(handler) -> str:
    """Read the required frontend-supplied X-User-Id user context."""

    user_id = optional_user_id_from_handler(handler)
    if not user_id:
        raise UserProviderAuthError("Missing user context", status=400, code="missing_user_context")
    return user_id


def _nocobase_base_url() -> str:
    return (
        os.getenv("HERMES_USER_PROVIDER_NOCOBASE_BASE_URL", "").strip()
        or os.getenv("FOXUAI_NOCOBASE_BASE_URL", "").strip()
        or os.getenv("NOCOBASE_BASE_URL", "").strip()
        or DEFAULT_NOCOBASE_BASE_URL
    ).rstrip("/")


def _nocobase_authorization() -> str:
    raw = (
        os.getenv("HERMES_USER_PROVIDER_NOCOBASE_AUTHORIZATION", "").strip()
        or os.getenv("FOXUAI_NOCOBASE_AUTHORIZATION", "").strip()
        or os.getenv("NOCOBASE_AUTHORIZATION", "").strip()
    )
    if not raw:
        return ""
    lower = raw.lower()
    if lower.startswith("bearer ") or lower.startswith("basic "):
        return raw
    return f"Bearer {raw}"


def _redact_error(message: Any, api_key: str | None = None) -> str:
    text = str(message or "")
    text = force_redact_provider_secret(text, api_key)
    return _redact_text(text)[:500]


def force_redact_provider_secret(message: Any, api_key: str | None = None) -> str:
    """强制遮蔽当前用户 Provider key，不依赖全局可关闭的 redaction。"""

    text = str(message or "")
    secret = str(api_key or "").strip()
    if not secret:
        return text
    replacements = {
        secret,
        f"Bearer {secret}",
        urllib.parse.quote(secret, safe=""),
    }
    for value in replacements:
        if value:
            text = text.replace(value, "***")
    return text


def _safe_json_loads(raw: bytes) -> Any:
    text = raw.decode("utf-8", errors="replace")
    return json.loads(text)


def _request_json(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
    timeout: float = NOCOBASE_TIMEOUT_SECONDS,
    no_redirect: bool = False,
) -> Any:
    data = None
    request_headers = {
        "Accept": "application/json",
        "User-Agent": "hermes-webui-user-provider",
    }
    if headers:
        request_headers.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    open_url = _NO_REDIRECT_OPENER.open if no_redirect else urllib.request.urlopen
    with open_url(req, timeout=timeout) as resp:  # nosec B310 - URL is validated by callers.
        raw = resp.read(PROVIDER_RESPONSE_MAX_BYTES + 1)
    if len(raw) > PROVIDER_RESPONSE_MAX_BYTES:
        raise ValueError("response too large")
    return _safe_json_loads(raw)


def _nocobase_headers(user_id: str) -> dict[str, str]:
    headers = {
        "X-User-Id": user_id,
        "X-Hostname": os.getenv("NOCOBASE_HOSTNAME", "www.foxuai.com"),
        "X-Authenticator": os.getenv("NOCOBASE_AUTHENTICATOR", "basic"),
    }
    authorization = _nocobase_authorization()
    if authorization:
        headers["Authorization"] = authorization
    return headers


def _nocobase_service_headers() -> dict[str, str]:
    headers = {
        "X-Hostname": os.getenv("NOCOBASE_HOSTNAME", "www.foxuai.com"),
        "X-Authenticator": os.getenv("NOCOBASE_AUTHENTICATOR", "basic"),
    }
    authorization = _nocobase_authorization()
    if authorization:
        headers["Authorization"] = authorization
    return headers


def _nocobase_list_records_from_payload(payload: Any) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    if not isinstance(data, list):
        raise UserProviderLookupError("NoCoBase response data is not a list")
    return [item for item in data if isinstance(item, dict)]


def _nocobase_list(collection: str, user_id: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    base_url = _nocobase_base_url()
    query = {"paginate": "false"}
    if params:
        query.update(params)
    encoded = urllib.parse.urlencode(query, doseq=True)
    url = f"{base_url}/api/{collection}:list?{encoded}"
    try:
        payload = _request_json(
            url,
            headers=_nocobase_headers(user_id),
            timeout=NOCOBASE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise UserProviderLookupError(_redact_error(exc)) from exc
    return _nocobase_list_records_from_payload(payload)


def _nocobase_mutation(
    collection: str,
    action: str,
    user_id: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> Any:
    base_url = _nocobase_base_url()
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode(params, doseq=True)
    url = f"{base_url}/api/{collection}:{action}{query}"
    try:
        return _request_json(
            url,
            method="POST",
            headers=_nocobase_headers(user_id),
            body=body,
            timeout=NOCOBASE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise UserProviderLookupError(_redact_error(exc, body.get("api_key") if isinstance(body, dict) else None)) from exc


def _nocobase_response_record(payload: Any) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        return data
    return {}


def list_user_profile_records(user_id: str) -> list[dict[str, Any]]:
    user_id = _normalize_id(user_id)
    return _nocobase_list(
        PROFILE_COLLECTION,
        user_id,
        {
            "filter[user_id]": user_id,
        },
    )


def get_user_profile_record_by_id(user_id: str, profile_id: str) -> dict[str, Any]:
    user_id = _normalize_id(user_id)
    profile_id = _normalize_id(profile_id)
    if not profile_id:
        raise UserProviderAuthError("Missing profile", status=400, code="missing_profile")
    records = _nocobase_list(
        PROFILE_COLLECTION,
        user_id,
        {
            "filter[id]": profile_id,
        },
    )
    if not records:
        raise UserProviderAuthError("Profile is not available for current user", status=403, code="profile_forbidden")
    record = records[0]
    if _record_user_id(record) != user_id:
        raise UserProviderAuthError("Profile is not available for current user", status=403, code="profile_forbidden")
    return record


def get_user_profile_record_by_name(user_id: str, profile_name: str) -> dict[str, Any]:
    user_id = _normalize_id(user_id)
    profile_name = str(profile_name or "").strip()
    if not profile_name:
        raise UserProviderAuthError("Missing profile", status=400, code="missing_profile")
    records = _nocobase_list(
        PROFILE_COLLECTION,
        user_id,
        {
            "filter[user_id]": user_id,
        },
    )
    for record in records:
        if _profile_matches(record, profile_name):
            return record
    raise UserProviderAuthError("Profile is not available for current user", status=403, code="profile_forbidden")


def resolve_user_profile_sync_name(record: dict[str, Any]) -> str:
    for key in ("name", "profile_name", "profile_key", "webui_profile_id"):
        value = str(record.get(key) or "").strip()
        if value:
            return value
    return ""


def get_user_profile_provider_id(record: dict[str, Any]) -> str:
    relation = record.get("hermes_providers")
    if isinstance(relation, dict) and relation.get("id") is not None:
        return str(relation.get("id")).strip()
    return str(record.get(PROFILE_PROVIDER_FOREIGN_KEY) or "").strip()


def set_user_profile_provider_id(user_id: str, profile_id: str, provider_id: str | None) -> dict[str, Any]:
    user_id = _normalize_id(user_id)
    profile_id = _normalize_id(profile_id)
    payload = {
        PROFILE_PROVIDER_FOREIGN_KEY: _normalize_id(provider_id) if provider_id else None,
    }
    response = _nocobase_mutation(
        PROFILE_COLLECTION,
        "update",
        user_id,
        params={"filterByTk": profile_id},
        body=payload,
    )
    return _nocobase_response_record(response)


def _profile_id_from_record(record: dict[str, Any]) -> str:
    return str(record.get("id") or record.get("profile_id") or record.get("profileId") or "").strip()


def list_global_user_ai_provider_records(user_id: str) -> list[dict[str, Any]]:
    user_id = _normalize_id(user_id)
    try:
        records = _nocobase_list(GLOBAL_PROVIDER_COLLECTION, user_id)
    except UserProviderLookupError:
        raise
    enabled_records: list[dict[str, Any]] = []
    for record in records:
        if _is_nocobase_false(record.get("is_enable")):
            continue
        enabled_records.append(record)
    return enabled_records


def list_global_ai_provider_records_for_service() -> list[dict[str, Any]]:
    base_url = _nocobase_base_url()
    url = f"{base_url}/api/{GLOBAL_PROVIDER_COLLECTION}:list?paginate=false"
    try:
        payload = _request_json(
            url,
            headers=_nocobase_service_headers(),
            timeout=NOCOBASE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise UserProviderLookupError(_redact_error(exc)) from exc
    records = _nocobase_list_records_from_payload(payload)
    enabled_records: list[dict[str, Any]] = []
    for record in records:
        if _is_nocobase_false(record.get("is_enable")):
            continue
        enabled_records.append(record)
    return enabled_records


def get_default_provider_record_for_user(user_id: str) -> dict[str, Any]:
    records = list_global_user_ai_provider_records(user_id)
    if not records:
        return {}
    default_record = next((record for record in records if _is_nocobase_true(record.get("is_default"))), None)
    return default_record or records[0]


def get_default_provider_id(user_id: str) -> str:
    return str(get_default_provider_record_for_user(user_id).get("id") or "").strip()


def list_user_ai_provider_records(
    user_id: str,
    *,
    selected_provider_id: str | None = "",
    use_default_when_empty: bool = False,
) -> list[dict[str, Any]]:
    user_id = _normalize_id(user_id)
    selected_provider_id = str(selected_provider_id or "").strip()
    records = list_global_user_ai_provider_records(user_id)
    if not selected_provider_id and use_default_when_empty:
        default_record = next((record for record in records if _is_nocobase_true(record.get("is_default"))), None)
        selected_provider_id = str((default_record or records[0] if records else {}).get("id") or "").strip()
    normalized_records: list[dict[str, Any]] = []
    for record in records:
        provider_id = str(record.get("id") or "").strip()
        is_selected = bool(selected_provider_id) and provider_id == selected_provider_id
        normalized_records.append(
            {
                **record,
                "status": "enabled" if is_selected else "disabled",
                "user_id": user_id,
                "selected": is_selected,
                "active": is_selected,
                "enabled": is_selected,
            }
        )
    normalized_records.sort(
        key=lambda item: (
            bool(item.get("selected")),
            bool(item.get("is_default")),
            str(item.get("updatedAt") or item.get("updated_at") or ""),
            str(item.get("id") or ""),
        ),
        reverse=True,
    )
    return normalized_records


def get_user_ai_provider_record(user_id: str, provider_id: str) -> dict[str, Any]:
    provider_id = _normalize_id(provider_id)
    for record in list_user_ai_provider_records(user_id):
        if str(record.get("id") or "").strip() == provider_id:
            return record
    raise UserProviderLookupError("provider not found")


def create_user_ai_provider_record(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    user_id = _normalize_id(user_id)
    payload = _provider_write_body(body, user_id=user_id, partial=False)
    payload["user_id"] = user_id
    payload["status"] = "disabled"
    if not payload.get("provider_slug"):
        payload["provider_slug"] = f"user-provider-{user_id[:12]}-{int(time.time() * 1000)}"
    response = _nocobase_mutation(USER_PROVIDER_COLLECTION, "create", user_id, body=payload)
    return _nocobase_response_record(response)


def update_user_ai_provider_record(
    user_id: str,
    provider_id: str,
    body: dict[str, Any],
    *,
    partial: bool = True,
) -> dict[str, Any]:
    user_id = _normalize_id(user_id)
    provider_id = _normalize_id(provider_id)
    payload = _provider_write_body(body, user_id=user_id, partial=partial)
    response = _nocobase_mutation(
        USER_PROVIDER_COLLECTION,
        "update",
        user_id,
        params={"filterByTk": provider_id},
        body=payload,
    )
    return _nocobase_response_record(response)


def delete_user_ai_provider_record(user_id: str, provider_id: str) -> None:
    user_id = _normalize_id(user_id)
    provider_id = _normalize_id(provider_id)
    _nocobase_mutation(
        USER_PROVIDER_COLLECTION,
        "destroy",
        user_id,
        params={"filterByTk": provider_id},
        body={},
    )


def _provider_write_body(body: dict[str, Any], *, user_id: str, partial: bool) -> dict[str, Any]:
    source = body if isinstance(body, dict) else {}
    payload: dict[str, Any] = {}

    def has_any(*names: str) -> bool:
        return any(name in source for name in names)

    def text(*names: str) -> str:
        for name in names:
            if name in source:
                return str(source.get(name) or "").strip()
        return ""

    field_map = {
        "name": ("name",),
        "provider_slug": ("provider_slug", "providerSlug"),
        "base_url": ("base_url", "baseUrl"),
        "model_name": ("model_name", "modelName"),
        "api_mode": ("api_mode", "apiMode"),
        "thinking_level": ("thinking_level", "thinkingLevel"),
        "api_key": ("api_key", "apiKey"),
        "status": ("status",),
    }
    for target, names in field_map.items():
        if not partial or has_any(*names):
            value = text(*names)
            if target == "api_key":
                value = str(source.get("api_key") if "api_key" in source else source.get("apiKey", ""))
            if target == "api_mode" and value and value not in SUPPORTED_API_MODES:
                raise UserProviderLookupError("unsupported api_mode")
            if target == "thinking_level" and value and value not in VALID_THINKING_LEVELS:
                raise UserProviderLookupError("unsupported thinking_level")
            if target == "status" and value and value not in {"enabled", "disabled"}:
                raise UserProviderLookupError("unsupported provider status")
            if value or (partial and target in {"thinking_level"}):
                payload[target] = value

    if not partial:
        missing = [
            field
            for field in ("name", "base_url", "model_name", "api_mode", "api_key")
            if not str(payload.get(field) or "").strip()
        ]
        if missing:
            raise UserProviderLookupError("missing_" + "_".join(missing))
    if payload.get("base_url"):
        payload["base_url"] = _validate_base_url(payload["base_url"])
    if payload.get("api_mode") != "codex_responses":
        payload["thinking_level"] = ""
    payload = {key: value for key, value in payload.items() if key in PROVIDER_WRITE_FIELDS}
    if user_id and not partial:
        payload["user_id"] = user_id
    return payload


def is_user_provider_untrusted_context_enabled() -> bool:
    """Return True when the X-User-Id context path is available.

    User AI Provider runtime is a product feature now, not an opt-in deployment
    switch. Runtime callers decide whether to use it based on whether a request
    actually carries X-User-Id context; requests without that context keep the
    normal Hermes fallback path.
    """

    return True


def is_user_provider_runtime_enabled() -> bool:
    return is_user_provider_untrusted_context_enabled()


def disabled_user_provider_resolution(user_id: str | None = None) -> UserProviderResolution:
    """Legacy helper for callers that still model an unavailable runtime."""

    return UserProviderResolution(
        status="disabled",
        reason="runtime_unavailable",
        user_id=str(user_id or "").strip(),
    )


def _record_user_id(record: dict[str, Any]) -> str:
    for key in ("user_id", "userId", "owner_id", "ownerId"):
        value = record.get(key)
        if value is not None:
            return str(value).strip()
    user_obj = record.get("user") or record.get("owner")
    if isinstance(user_obj, dict) and user_obj.get("id") is not None:
        return str(user_obj.get("id")).strip()
    return ""


def _provider_candidates_for_user(user_id: str, selected_provider_id: str | None = None) -> list[dict[str, Any]]:
    selected_provider_id = str(selected_provider_id or "").strip()
    if not selected_provider_id:
        return []
    records = list_global_user_ai_provider_records(user_id)
    return [
        {
            **record,
            "status": "enabled",
            "user_id": user_id,
            "selected": True,
            "active": True,
            "enabled": True,
        }
        for record in records
        if str(record.get("id") or "").strip() == selected_provider_id
    ]


def _profile_context_resolution(
    *,
    status: str,
    reason: str,
    user_id: str,
    profile: dict[str, Any] | None = None,
    provider: dict[str, Any] | None = None,
    error: str = "",
) -> UserProviderResolution:
    profile_record = profile or {}
    return UserProviderResolution(
        status=status,
        reason=reason,
        user_id=user_id,
        provider=provider,
        error=error,
        profile_id=_profile_id_from_record(profile_record),
        profile_name=resolve_user_profile_sync_name(profile_record),
    )


def _normalize_provider_record(record: dict[str, Any], user_id: str) -> tuple[dict[str, Any] | None, str]:
    provider_id = str(record.get("id") or "").strip()
    provider_name = str(record.get("provider_name") or record.get("name") or "").strip()
    provider_slug_source = record.get("provider_slug")
    if provider_slug_source:
        provider_slug = str(provider_slug_source).strip().lower()
    elif provider_name and provider_id:
        provider_slug = f"{provider_name}-{provider_id}".strip().lower()
    else:
        provider_slug = str(provider_name or provider_id or "").strip().lower()
    if provider_slug:
        provider_slug = "-".join(part for part in provider_slug.replace("_", "-").split() if part)
    if not provider_slug and provider_id:
        provider_slug = f"provider-{provider_id}"
    base_url = str(record.get("base_url") or "").strip().rstrip("/")
    model_name = str(record.get("model_name") or record.get("model") or "").strip()
    raw_api_mode = str(record.get("raw_api_mode") or record.get("api_mode") or "").strip().lower()
    api_mode = normalize_nocobase_provider_api_mode(raw_api_mode)
    api_key = str(record.get("api_key") or "").strip()
    model_level = str(record.get("model_level") or record.get("thinking_level") or "").strip().lower()
    missing = [
        name
        for name, value in (
            ("base_url", base_url),
            ("model_name", model_name),
            ("api_mode", api_mode),
            ("api_key", api_key),
        )
        if not value
    ]
    if missing:
        return None, "missing_" + "_".join(missing)
    if api_mode not in SUPPORTED_API_MODES:
        return None, "unsupported_api_mode"
    base_url = _normalize_runtime_base_url(base_url, api_mode)
    try:
        base_url = _validate_base_url(base_url)
    except ValueError:
        return None, "invalid_base_url"
    return {
        "id": provider_id,
        "user_id": str(user_id),
        "name": provider_name or provider_slug,
        "provider_name": provider_name or provider_slug,
        "provider_slug": provider_slug,
        "base_url": base_url,
        "model_name": model_name,
        "api_mode": api_mode,
        "raw_api_mode": raw_api_mode,
        # 模型强度只做展示，不参与运行时思考深度覆盖。
        "thinking_level": "",
        "model_level": model_level,
        "api_key": api_key,
        "status": str(record.get("status") or "").strip().lower(),
        "selected": bool(record.get("selected") or str(record.get("status") or "").strip().lower() == "enabled"),
        "is_default": bool(record.get("is_default")),
        "is_enable": bool(record.get("is_enable", True)),
        "updatedAt": str(record.get("updatedAt") or record.get("updated_at") or ""),
        "createdAt": str(record.get("createdAt") or record.get("created_at") or ""),
    }, ""


def resolve_user_profile_provider(
    user_id: str,
    *,
    profile_id: str | None = "",
    profile_name: str | None = "",
) -> UserProviderResolution:
    user_id = _normalize_id(user_id)
    normalized_profile_id = _normalize_id(profile_id)
    normalized_profile_name = str(profile_name or "").strip()
    if not normalized_profile_id and not normalized_profile_name:
        return UserProviderResolution(
            status="none",
            reason="missing_profile_context",
            user_id=user_id,
        )

    try:
        profile = (
            get_user_profile_record_by_id(user_id, normalized_profile_id)
            if normalized_profile_id
            else get_user_profile_record_by_name(user_id, normalized_profile_name)
        )
        selected_provider_id = get_user_profile_provider_id(profile)
        reason = "profile_provider"
        if not selected_provider_id:
            selected_provider_id = get_default_provider_id(user_id)
            reason = "system_default_provider"
        records = _provider_candidates_for_user(user_id, selected_provider_id)
    except UserProviderLookupError as exc:
        logger.warning("[webui] profile provider lookup failed for user=%s: %s", user_id, exc)
        return UserProviderResolution(
            status="lookup_failed",
            reason="nocobase_lookup_failed",
            user_id=user_id,
            error=str(exc),
            profile_id=normalized_profile_id,
            profile_name=normalized_profile_name,
        )

    if not selected_provider_id:
        return _profile_context_resolution(
            status="none",
            reason="no_default_provider",
            user_id=user_id,
            profile=profile,
        )
    if not records:
        return _profile_context_resolution(
            status="disabled",
            reason="selected_provider_unavailable" if reason == "profile_provider" else "default_provider_unavailable",
            user_id=user_id,
            profile=profile,
        )

    provider, failure_reason = _normalize_provider_record(records[0], user_id)
    if not provider:
        return _profile_context_resolution(
            status="incomplete",
            reason=failure_reason or "incomplete_provider",
            user_id=user_id,
            profile=profile,
        )
    return _profile_context_resolution(
        status="active",
        reason=reason,
        user_id=user_id,
        profile=profile,
        provider=provider,
    )


def public_provider_resolution(resolution: UserProviderResolution) -> dict[str, Any]:
    payload = {
        "status": resolution.status,
        "reason": resolution.reason,
        "fallback": not resolution.is_active,
    }
    if resolution.profile_id:
        payload["profile_id"] = resolution.profile_id
    if resolution.profile_name:
        payload["profile_name"] = resolution.profile_name
    if resolution.error:
        payload["detail"] = _redact_error(resolution.error)
    if resolution.provider:
        payload["provider"] = public_provider_metadata(resolution.provider)
    return payload


def public_provider_metadata(provider: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(provider.get("id") or ""),
        "name": str(provider.get("name") or provider.get("provider_name") or ""),
        "provider_name": str(provider.get("provider_name") or provider.get("name") or ""),
        "provider_slug": str(provider.get("provider_slug") or ""),
        "api_mode": str(provider.get("api_mode") or ""),
        "raw_api_mode": str(provider.get("raw_api_mode") or ""),
        "thinking_level": str(provider.get("thinking_level") or ""),
        "model_level": str(provider.get("model_level") or ""),
        "updatedAt": str(provider.get("updatedAt") or ""),
    }


def masked_provider_key(api_key: Any) -> str:
    key = str(api_key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:3]}****{key[-4:]}"


def public_user_ai_provider_record(record: dict[str, Any], sync_status: dict[str, Any] | None = None) -> dict[str, Any]:
    provider_id = str(record.get("id") or "").strip()
    status = str(record.get("status") or "disabled").strip().lower() or "disabled"
    api_mode = normalize_nocobase_provider_api_mode(record.get("api_mode"))
    raw_api_mode = str(record.get("raw_api_mode") or record.get("api_mode") or "").strip()
    provider_name = str(record.get("provider_name") or record.get("name") or "").strip()
    model_level = str(record.get("model_level") or record.get("thinking_level") or "").strip().lower()
    is_selected = bool(record.get("selected") or record.get("active") or record.get("enabled") or status == "enabled")
    provider_slug = str(record.get("provider_slug") or "").strip()
    if not provider_slug:
        provider_slug = f"{provider_name}-{provider_id}".strip("-") if provider_name and provider_id else provider_name or provider_id
    payload = {
        "id": provider_id,
        "user_id": _record_user_id(record),
        "name": provider_name,
        "provider_name": provider_name,
        "provider_slug": provider_slug.strip(),
        "base_url": str(record.get("base_url") or "").strip(),
        "model_name": str(record.get("model_name") or "").strip(),
        "api_mode": api_mode,
        "raw_api_mode": raw_api_mode,
        "thinking_level": str(record.get("thinking_level") or "").strip(),
        "model_level": model_level,
        "status": status,
        "enabled": is_selected,
        "active": is_selected,
        "selected": is_selected,
        "is_default": bool(record.get("is_default")),
        "is_enable": bool(record.get("is_enable", True)),
        "api_key": "",
        "api_key_masked": masked_provider_key(record.get("api_key")),
        "has_api_key": bool(str(record.get("api_key") or "").strip()),
        "createdAt": str(record.get("createdAt") or record.get("created_at") or ""),
        "updatedAt": str(record.get("updatedAt") or record.get("updated_at") or ""),
    }
    if sync_status:
        payload["sync"] = sync_status
    return payload


def provider_runtime_signature(resolution: UserProviderResolution | None) -> dict[str, Any]:
    if not resolution:
        return {"status": "not_requested"}
    provider = resolution.provider or {}
    return {
        "status": resolution.status,
        "reason": resolution.reason,
        "provider_id": str(provider.get("id") or ""),
        "provider_slug": str(provider.get("provider_slug") or ""),
        "updatedAt": str(provider.get("updatedAt") or ""),
        "base_url": str(provider.get("base_url") or ""),
        "model_name": str(provider.get("model_name") or ""),
        "api_mode": str(provider.get("api_mode") or ""),
        "thinking_level": str(provider.get("thinking_level") or ""),
    }


def _profile_matches(record: dict[str, Any], requested_profile: str) -> bool:
    requested = str(requested_profile or "").strip()
    if not requested:
        return False
    for key in ("name", "profile_name", "profile_key", "webui_profile_id"):
        if str(record.get(key) or "").strip() == requested:
            return True
    return False


def verify_user_profile_access(user_id: str, requested_profile: str | None) -> None:
    profile = str(requested_profile or "").strip()
    if not profile:
        raise UserProviderAuthError("Missing profile", status=400, code="missing_profile")
    try:
        records = _nocobase_list(
            PROFILE_COLLECTION,
            user_id,
            {
                "filter[user_id]": user_id,
            },
        )
    except UserProviderLookupError as exc:
        raise UserProviderAuthError(
            "Profile ownership verification unavailable",
            status=503,
            code="profile_lookup_failed",
        ) from exc
    if not any(_profile_matches(record, profile) for record in records):
        raise UserProviderAuthError("Profile is not available for current user", status=403, code="profile_forbidden")


def _normalize_provider_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(value)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


def _is_blocked_provider_ip(value: str) -> bool:
    try:
        ip = _normalize_provider_ip(value)
    except ValueError:
        return False
    return (
        ip in BLOCKED_PROVIDER_IPS
        or ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
        or not ip.is_global
    )


def _validate_provider_host(hostname: str, port: int) -> None:
    host = str(hostname or "").strip().rstrip(".").lower()
    if host in BLOCKED_PROVIDER_HOSTNAMES or host.endswith(".localhost"):
        raise ValueError("base_url host is not allowed")
    if _is_blocked_provider_ip(host):
        raise ValueError("base_url host resolves to a non-public address")
    try:
        addrinfos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise ValueError(f"could not resolve host '{hostname}'") from exc
    resolved_ips: set[str] = set()
    for _family, _type, _proto, _canonname, sockaddr in addrinfos:
        if not sockaddr:
            continue
        resolved_ip = str(sockaddr[0])
        resolved_ips.add(resolved_ip)
        if _is_blocked_provider_ip(resolved_ip):
            raise ValueError("base_url host resolves to a non-public address")
    if not resolved_ips:
        raise ValueError(f"could not resolve host '{hostname}'")


def _validate_base_url(base_url: str) -> str:
    normalized = str(base_url or "").strip().rstrip("/")
    if not normalized:
        raise ValueError("base_url is required")
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("base_url must start with http:// or https://")
    if not parsed.hostname:
        raise ValueError("base_url has no host")
    if parsed.username or parsed.password:
        raise ValueError("base_url must not include credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not include query or fragment")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("base_url has invalid port") from exc
    _validate_provider_host(parsed.hostname, port)
    return normalized


def _endpoint_candidates(base_url: str, resource: str) -> list[str]:
    base = base_url.rstrip("/")
    parsed = urllib.parse.urlparse(base)
    path = parsed.path.rstrip("/")
    candidates = [f"{base}/{resource}"]
    if not path.endswith("/v1"):
        candidates.insert(0, f"{base}/v1/{resource}")
    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _provider_auth_headers(api_key: str, api_mode: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if api_mode == "anthropic_messages":
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    return headers


def _models_from_payload(payload: Any) -> list[dict[str, str]]:
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    if isinstance(payload, dict) and not isinstance(data, list) and isinstance(payload.get("models"), list):
        data = payload["models"]
    models: list[dict[str, str]] = []
    if not isinstance(data, list):
        return models
    seen: set[str] = set()
    for item in data:
        model_id = ""
        if isinstance(item, dict):
            model_id = str(item.get("id") or item.get("name") or item.get("model") or "").strip()
        elif isinstance(item, str):
            model_id = item.strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        models.append({"id": model_id, "label": model_id})
    return models


def fetch_provider_models(provider: dict[str, Any]) -> tuple[list[dict[str, str]], str]:
    base_url = _validate_base_url(str(provider.get("base_url") or ""))
    api_key = str(provider.get("api_key") or "")
    api_mode = str(provider.get("api_mode") or "")
    last_error = ""
    for endpoint in _endpoint_candidates(base_url, "models"):
        try:
            payload = _request_json(
                endpoint,
                headers=_provider_auth_headers(api_key, api_mode),
                timeout=PROVIDER_MODELS_TIMEOUT_SECONDS,
                no_redirect=True,
            )
            models = _models_from_payload(payload)
            if models:
                return models, ""
            last_error = "models endpoint returned no models"
        except Exception as exc:
            last_error = _redact_error(exc, api_key)
    return [], last_error or "models lookup failed"


def _mode_test_request(provider: dict[str, Any]) -> tuple[bool, str]:
    base_url = _validate_base_url(str(provider.get("base_url") or ""))
    api_key = str(provider.get("api_key") or "")
    model_name = str(provider.get("model_name") or "")
    api_mode = str(provider.get("api_mode") or "")
    if api_mode == "anthropic_messages":
        resources = ["messages"]
        body = {
            "model": model_name,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
    elif api_mode == "chat_completions":
        resources = ["chat/completions"]
        body = {
            "model": model_name,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        }
    elif api_mode == "codex_responses":
        resources = ["responses"]
        body = {
            "model": model_name,
            "input": "ping",
            "max_output_tokens": 16,
        }
    else:
        return False, "unsupported api_mode"
    last_error = ""
    for resource in resources:
        for endpoint in _endpoint_candidates(base_url, resource):
            try:
                _request_json(
                    endpoint,
                    method="POST",
                    headers=_provider_auth_headers(api_key, api_mode),
                    body=body,
                    timeout=PROVIDER_TEST_TIMEOUT_SECONDS,
                    no_redirect=True,
                )
                return True, ""
            except Exception as exc:
                last_error = _redact_error(exc, api_key)
    return False, last_error or "provider test failed"


def _provider_from_test_body(body: dict[str, Any], user_id: str | None = None) -> tuple[dict[str, Any] | None, str]:
    normalized_user_id = str(user_id or "").strip()
    record = {
        "id": "test",
        "user_id": normalized_user_id,
        "provider_slug": "test",
        "name": str(body.get("name") or "test").strip() or "test",
        "base_url": body.get("base_url"),
        "api_key": body.get("api_key"),
        "model_name": body.get("model_name"),
        "api_mode": body.get("api_mode"),
        "status": "enabled",
        "updatedAt": "",
    }
    return _normalize_provider_record(record, normalized_user_id)


def test_user_provider_connection(user_id: str | None, body: dict[str, Any]) -> dict[str, Any]:
    provider, reason = _provider_from_test_body(body, user_id)
    if not provider:
        return {
            "ok": False,
            "error": reason or "invalid_provider",
            "message": "Provider configuration is incomplete or unsupported",
        }
    try:
        _validate_base_url(provider["base_url"])
    except ValueError as exc:
        return {"ok": False, "error": "invalid_url", "message": _redact_error(exc, provider.get("api_key"))}

    models, models_error = fetch_provider_models(provider)
    requested_model = provider["model_name"]
    matched_model = any(item.get("id") == requested_model for item in models)
    if models and not matched_model:
        return {
            "ok": False,
            "error": "model_not_found",
            "message": "model_name was not found in provider model list",
            "models": models[:50],
        }
    if matched_model:
        return {
            "ok": True,
            "status": "connected",
            "checked": "models",
            "matched_model": True,
            "models": models[:50],
        }

    ok, mode_error = _mode_test_request(provider)
    if ok:
        return {
            "ok": True,
            "status": "connected",
            "checked": provider["api_mode"],
            "matched_model": False,
            "models": [{"id": requested_model, "label": requested_model}],
        }
    return {
        "ok": False,
        "error": "test_failed",
        "message": _redact_error(mode_error or models_error, provider.get("api_key")),
        "models_error": _redact_error(models_error, provider.get("api_key")) if models_error else "",
    }


def _models_cache_key(user_id: str, resolution: UserProviderResolution) -> tuple[Any, ...]:
    provider = resolution.provider or {}
    return (
        user_id,
        resolution.profile_id,
        resolution.profile_name,
        resolution.status,
        resolution.reason,
        str(provider.get("id") or ""),
        str(provider.get("updatedAt") or ""),
        str(provider.get("provider_slug") or ""),
        str(provider.get("base_url") or ""),
        str(provider.get("model_name") or ""),
        _hash_text(provider.get("api_key")),
        str(provider.get("api_mode") or ""),
        str(provider.get("thinking_level") or ""),
    )


def _get_models_cache(key: tuple[Any, ...]) -> dict[str, Any] | None:
    cached = _USER_MODELS_CACHE.get(key)
    if not cached:
        return None
    ts, payload = cached
    if time.monotonic() - ts >= USER_MODELS_CACHE_TTL_SECONDS:
        _USER_MODELS_CACHE.pop(key, None)
        return None
    return copy.deepcopy(payload)


def _set_models_cache(key: tuple[Any, ...], payload: dict[str, Any]) -> None:
    _USER_MODELS_CACHE[key] = (time.monotonic(), copy.deepcopy(payload))
    if len(_USER_MODELS_CACHE) > 256:
        oldest = sorted(_USER_MODELS_CACHE.items(), key=lambda item: item[1][0])[:64]
        for old_key, _ in oldest:
            _USER_MODELS_CACHE.pop(old_key, None)


def clear_user_provider_models_cache() -> None:
    _USER_MODELS_CACHE.clear()


def build_user_provider_models_payload(
    user_id: str | None,
    fallback_factory: Callable[[], dict[str, Any]],
    *,
    profile_id: str | None = "",
    profile_name: str | None = "",
) -> dict[str, Any]:
    if not user_id:
        resolution = UserProviderResolution(
            status="disabled",
            reason="missing_user_context",
            user_id="",
        )
        payload = copy.deepcopy(fallback_factory())
        payload["provider_resolution"] = public_provider_resolution(resolution)
        return payload

    resolution = resolve_user_profile_provider(
        user_id,
        profile_id=profile_id,
        profile_name=profile_name,
    )
    key = _models_cache_key(user_id, resolution)
    cached = _get_models_cache(key)
    if cached is not None:
        return cached

    if not resolution.is_active:
        payload = copy.deepcopy(fallback_factory())
        payload["provider_resolution"] = public_provider_resolution(resolution)
        _set_models_cache(key, payload)
        return payload

    provider = resolution.provider or {}
    models, models_error = fetch_provider_models(provider)
    if not models:
        model_name = str(provider.get("model_name") or "")
        models = [{"id": model_name, "label": model_name}] if model_name else []
    provider_slug = str(provider.get("provider_slug") or provider.get("id") or "custom")
    payload = {
        "active_provider": provider_slug,
        "active_provider_meta": public_provider_metadata(provider),
        "default_model": str(provider.get("model_name") or ""),
        "groups": [
            {
                "provider": str(provider.get("name") or "Custom Provider"),
                "provider_id": provider_slug,
                "models": models,
            }
        ],
        "provider_resolution": public_provider_resolution(resolution),
    }
    if models_error:
        payload["provider_resolution"]["models_error"] = _redact_error(models_error, provider.get("api_key"))
    _set_models_cache(key, payload)
    return copy.deepcopy(payload)
