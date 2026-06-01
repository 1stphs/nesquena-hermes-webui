"""User-scoped AI provider resolver for the external Hermes frontend.

This module treats NoCoBase providers as optional runtime overlays. The stored
provider resolver is disabled by default until NoCoBase bearer verification and
the backing schema / ACLs are verified. The temporary X-User-Id branch is kept
only for local diagnostics and must not be treated as a permission boundary.
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

from api.helpers import _redact_text

logger = logging.getLogger(__name__)

USER_PROVIDER_COLLECTION = "hermes_user_ai_providers"
PROFILE_COLLECTION = "hermes_profiles"
UNTRUSTED_CONTEXT_ENABLE_ENV = "HERMES_USER_PROVIDER_ENABLE_UNTRUSTED_CONTEXT"
NOCOBASE_AUTH_ENABLE_ENV = "HERMES_USER_PROVIDER_ENABLE_NOCOBASE_AUTH"
NOCOBASE_AUTH_HEADER = "X-NocoBase-Authorization"
SUPPORTED_API_MODES = {"anthropic_messages", "codex_responses"}
VALID_THINKING_LEVELS = {"minimal", "low", "medium", "high", "xhigh"}
CANONICAL_AGENT_PROVIDER = "custom"
DEFAULT_NOCOBASE_BASE_URL = "https://www.foxuai.com"
NOCOBASE_TIMEOUT_SECONDS = 6.0
PROVIDER_TEST_TIMEOUT_SECONDS = 8.0
PROVIDER_MODELS_TIMEOUT_SECONDS = 6.0
PROVIDER_RESPONSE_MAX_BYTES = 512 * 1024
USER_MODELS_CACHE_TTL_SECONDS = 60.0
MAX_AUTHORIZATION_HEADER_LENGTH = 8192
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


def current_user_id_from_handler(handler) -> str:
    """Read the temporary frontend-supplied user context.

    This is intentionally unsafe and only allowed behind
    HERMES_USER_PROVIDER_ENABLE_UNTRUSTED_CONTEXT=1.
    """

    header_user_id, cookie_user_id = _user_context_ids_from_handler(handler)
    if header_user_id and cookie_user_id and header_user_id != cookie_user_id:
        raise UserProviderAuthError("User context mismatch", status=400, code="user_context_mismatch")
    user_id = header_user_id or cookie_user_id
    if not user_id:
        raise UserProviderAuthError("Missing user context", status=400, code="missing_user_context")
    return user_id


def _normalize_bearer_authorization(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise UserProviderAuthError(
            "Missing NoCoBase authorization",
            status=401,
            code="missing_nocobase_auth",
        )
    if len(raw) > MAX_AUTHORIZATION_HEADER_LENGTH or any(ch in raw for ch in "\r\n\t"):
        raise UserProviderAuthError(
            "Invalid NoCoBase authorization",
            status=400,
            code="invalid_nocobase_auth",
        )
    if not raw.lower().startswith("bearer ") or not raw[7:].strip():
        raise UserProviderAuthError(
            "Invalid NoCoBase authorization",
            status=400,
            code="invalid_nocobase_auth",
        )
    return raw


def _user_id_from_auth_check_payload(payload: Any) -> str:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            user_id = _normalize_id(data.get("id"))
            if user_id:
                return user_id
        user_id = _normalize_id(payload.get("id"))
        if user_id:
            return user_id
    return ""


def verify_nocobase_authorization(authorization: str) -> str:
    bearer = _normalize_bearer_authorization(authorization)
    url = f"{_nocobase_base_url()}/api/auth:check"
    try:
        payload = _request_json(
            url,
            headers={"Authorization": bearer},
            timeout=NOCOBASE_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        raise UserProviderAuthError(
            "NoCoBase authorization verification failed",
            status=401,
            code="nocobase_auth_failed",
        ) from exc
    user_id = _user_id_from_auth_check_payload(payload)
    if not user_id:
        raise UserProviderAuthError(
            "NoCoBase authorization did not return a user",
            status=401,
            code="invalid_nocobase_auth",
        )
    return user_id


def verified_user_id_from_handler(handler) -> str:
    headers = getattr(handler, "headers", None)
    verified_user_id = verify_nocobase_authorization(_header_value(headers, NOCOBASE_AUTH_HEADER))
    header_user_id, cookie_user_id = _user_context_ids_from_handler(handler)
    for candidate in (header_user_id, cookie_user_id):
        if candidate and candidate != verified_user_id:
            raise UserProviderAuthError(
                "User context mismatch",
                status=403,
                code="user_context_mismatch",
            )
    return verified_user_id


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
    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict) and isinstance(data.get("data"), list):
        data = data["data"]
    if not isinstance(data, list):
        raise UserProviderLookupError("NoCoBase response data is not a list")
    return [item for item in data if isinstance(item, dict)]


def _is_truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}


def is_user_provider_untrusted_context_enabled() -> bool:
    """Temporary unsafe switch for the frontend-supplied user context path."""

    return os.getenv(UNTRUSTED_CONTEXT_ENABLE_ENV, "").strip() == "1"


def is_user_provider_nocobase_auth_enabled() -> bool:
    """Trusted switch for NoCoBase bearer-bound user context."""

    return os.getenv(NOCOBASE_AUTH_ENABLE_ENV, "").strip() == "1"


def is_user_provider_runtime_enabled() -> bool:
    return is_user_provider_nocobase_auth_enabled()


def disabled_user_provider_resolution(user_id: str | None = None) -> UserProviderResolution:
    return UserProviderResolution(
        status="disabled",
        reason="untrusted_context_disabled",
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


def _provider_candidates_for_user(user_id: str) -> list[dict[str, Any]]:
    records = _nocobase_list(
        USER_PROVIDER_COLLECTION,
        user_id,
        {
            "filter[user_id]": user_id,
        },
    )
    return [
        record
        for record in records
        if not _record_user_id(record) or _record_user_id(record) == str(user_id)
    ]


def _normalize_provider_record(record: dict[str, Any], user_id: str) -> tuple[dict[str, Any] | None, str]:
    provider_id = str(record.get("id") or "").strip()
    provider_slug = str(record.get("provider_slug") or provider_id or "").strip().lower()
    base_url = str(record.get("base_url") or "").strip().rstrip("/")
    model_name = str(record.get("model_name") or record.get("model") or "").strip()
    api_mode = str(record.get("api_mode") or "").strip().lower()
    api_key = str(record.get("api_key") or "").strip()
    thinking_level = str(record.get("thinking_level") or "").strip().lower()
    missing = [
        name
        for name, value in (
            ("provider_slug", provider_slug),
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
    if api_mode == "codex_responses" and thinking_level and thinking_level not in VALID_THINKING_LEVELS:
        return None, "unsupported_thinking_level"
    try:
        base_url = _validate_base_url(base_url)
    except ValueError:
        return None, "invalid_base_url"
    return {
        "id": provider_id,
        "user_id": str(user_id),
        "name": str(record.get("name") or provider_slug).strip(),
        "provider_slug": provider_slug,
        "base_url": base_url,
        "model_name": model_name,
        "api_mode": api_mode,
        "thinking_level": thinking_level,
        "api_key": api_key,
        "status": str(record.get("status") or "").strip().lower(),
        "is_default": _is_truthy(record.get("is_default")),
        "updatedAt": str(record.get("updatedAt") or record.get("updated_at") or ""),
    }, ""


def resolve_user_provider(user_id: str) -> UserProviderResolution:
    user_id = _normalize_id(user_id)
    try:
        records = _provider_candidates_for_user(user_id)
    except UserProviderLookupError as exc:
        logger.warning("[webui] user provider lookup failed for user=%s: %s", user_id, exc)
        return UserProviderResolution(
            status="lookup_failed",
            reason="nocobase_lookup_failed",
            user_id=user_id,
            error=str(exc),
        )

    if not records:
        return UserProviderResolution(status="none", reason="no_provider", user_id=user_id)

    active_records = [
        record
        for record in records
        if _is_truthy(record.get("is_default"))
        and str(record.get("status") or "").strip().lower() == "enabled"
    ]
    if not active_records:
        return UserProviderResolution(status="disabled", reason="no_enabled_default_provider", user_id=user_id)

    active_records.sort(key=lambda item: str(item.get("updatedAt") or item.get("updated_at") or ""), reverse=True)
    provider, reason = _normalize_provider_record(active_records[0], user_id)
    if not provider:
        return UserProviderResolution(status="incomplete", reason=reason or "incomplete_provider", user_id=user_id)
    return UserProviderResolution(status="active", reason="active_provider", user_id=user_id, provider=provider)


def public_provider_resolution(resolution: UserProviderResolution) -> dict[str, Any]:
    payload = {
        "status": resolution.status,
        "reason": resolution.reason,
        "fallback": not resolution.is_active,
    }
    if resolution.error:
        payload["detail"] = _redact_error(resolution.error)
    if resolution.provider:
        payload["provider"] = public_provider_metadata(resolution.provider)
    return payload


def public_provider_metadata(provider: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(provider.get("id") or ""),
        "name": str(provider.get("name") or ""),
        "provider_slug": str(provider.get("provider_slug") or ""),
        "api_mode": str(provider.get("api_mode") or ""),
        "thinking_level": str(provider.get("thinking_level") or ""),
        "updatedAt": str(provider.get("updatedAt") or ""),
    }


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
    thinking_level = str(provider.get("thinking_level") or "").strip().lower()
    if api_mode == "anthropic_messages":
        resources = ["messages"]
        body = {
            "model": model_name,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ping"}],
        }
    elif api_mode == "codex_responses":
        resources = ["responses"]
        body = {
            "model": model_name,
            "input": "ping",
            "max_output_tokens": 16,
        }
        if thinking_level:
            body["reasoning"] = {"effort": thinking_level}
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
        "thinking_level": body.get("thinking_level"),
        "status": "enabled",
        "is_default": True,
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
) -> dict[str, Any]:
    if not is_user_provider_runtime_enabled():
        resolution = disabled_user_provider_resolution(user_id)
        payload = copy.deepcopy(fallback_factory())
        payload["provider_resolution"] = public_provider_resolution(resolution)
        return payload

    if not user_id:
        resolution = UserProviderResolution(
            status="disabled",
            reason="missing_user_context",
            user_id="",
        )
        payload = copy.deepcopy(fallback_factory())
        payload["provider_resolution"] = public_provider_resolution(resolution)
        return payload

    resolution = resolve_user_provider(user_id)
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
