"""NoCoBase telemetry for completed Hermes chat turns."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python 3.8+ provides zoneinfo in this repo.
    ZoneInfo = None

logger = logging.getLogger(__name__)

USAGE_TELEMETRY_COLLECTION = "hermes_chat_usage_events"
USAGE_TELEMETRY_ENABLED_ENV = "HERMES_USAGE_TELEMETRY_ENABLED"
USAGE_TELEMETRY_TIMEZONE_ENV = "HERMES_USAGE_TIMEZONE"
USAGE_TELEMETRY_SOURCE = "hermes-webui"
DEFAULT_NOCOBASE_BASE_URL = "https://www.foxuai.com"
DEFAULT_NOCOBASE_TIMEOUT_SECONDS = 10.0


class UsageTelemetryError(RuntimeError):
    """Raised when a NoCoBase telemetry write fails."""


def is_usage_telemetry_enabled() -> bool:
    return os.getenv(USAGE_TELEMETRY_ENABLED_ENV, "").strip() == "1"


def _normalize_nocobase_api_base_url() -> str:
    raw_api_base_url = os.getenv("NOCOBASE_API_BASE_URL", "").strip()
    if raw_api_base_url:
        return raw_api_base_url.rstrip("/")

    raw_base_url = (
        os.getenv("HERMES_USER_PROVIDER_NOCOBASE_BASE_URL")
        or os.getenv("FOXUAI_NOCOBASE_BASE_URL")
        or os.getenv("NOCOBASE_BASE_URL")
        or DEFAULT_NOCOBASE_BASE_URL
    ).strip()
    base_url = raw_base_url.rstrip("/")
    if not base_url:
        raise UsageTelemetryError("NoCoBase API base URL is not configured")
    if base_url.endswith("/api"):
        return base_url
    return f"{base_url}/api"


def _nocobase_authorization_header() -> str:
    raw_authorization = (
        os.getenv("NOCOBASE_AUTHORIZATION")
        or os.getenv("HERMES_USER_PROVIDER_NOCOBASE_AUTHORIZATION")
        or os.getenv("FOXUAI_NOCOBASE_AUTHORIZATION")
        or ""
    ).strip()
    if not raw_authorization:
        raise UsageTelemetryError("NoCoBase authorization is not configured")
    if raw_authorization.lower().startswith("bearer "):
        return raw_authorization
    return f"Bearer {raw_authorization}"


def _nocobase_headers(*, has_body: bool = False) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Authorization": _nocobase_authorization_header(),
        "User-Agent": "hermes-webui-usage-telemetry",
        "X-Hostname": os.getenv("NOCOBASE_HOSTNAME", "www.foxuai.com").strip() or "www.foxuai.com",
        "X-Authenticator": os.getenv("NOCOBASE_AUTHENTICATOR", "basic").strip() or "basic",
    }
    if has_body:
        headers["Content-Type"] = "application/json"
    return headers


def _telemetry_timeout_seconds() -> float:
    raw = os.getenv("HERMES_USAGE_TELEMETRY_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_NOCOBASE_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_NOCOBASE_TIMEOUT_SECONDS
    return min(max(value, 1.0), 30.0)


def _redact_error_text(value: Any) -> str:
    text = str(value or "")
    secrets = [
        os.getenv("NOCOBASE_AUTHORIZATION"),
        os.getenv("HERMES_USER_PROVIDER_NOCOBASE_AUTHORIZATION"),
        os.getenv("FOXUAI_NOCOBASE_AUTHORIZATION"),
    ]
    for secret in secrets:
        raw = str(secret or "").strip()
        if not raw:
            continue
        candidates = [raw]
        if raw.lower().startswith("bearer "):
            candidates.append(raw.split(None, 1)[1])
        else:
            candidates.append(f"Bearer {raw}")
        for candidate in candidates:
            text = text.replace(candidate, "[REDACTED]")
    text = re.sub(
        r"(Authorization:\s*Bearer\s+)(\S+)",
        r"\1[REDACTED]",
        text,
        flags=re.IGNORECASE,
    )
    return text[:800]


def _parse_error_message(raw_text: str, fallback: str) -> str:
    if not raw_text:
        return fallback
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return raw_text
    if not isinstance(payload, dict):
        return fallback
    message = payload.get("message")
    if message:
        return str(message)
    errors = payload.get("errors")
    if isinstance(errors, list):
        joined = "; ".join(
            str(item.get("message"))
            for item in errors
            if isinstance(item, dict) and item.get("message")
        )
        if joined:
            return joined
    return fallback


def _nocobase_request(path: str, *, method: str = "GET", body: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_path = "/" + str(path or "").lstrip("/")
    url = f"{_normalize_nocobase_api_base_url()}{normalized_path}"
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=_nocobase_headers(has_body=body is not None),
    )
    try:
        with urllib.request.urlopen(request, timeout=_telemetry_timeout_seconds()) as response:  # nosec B310
            raw_text = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8")
        except Exception:
            error_body = ""
        message = _parse_error_message(
            error_body,
            f"NoCoBase request failed with status {exc.code}",
        )
        raise UsageTelemetryError(_redact_error_text(message)) from exc
    except (OSError, TimeoutError) as exc:
        raise UsageTelemetryError(_redact_error_text(f"NoCoBase request failed: {exc}")) from exc

    if not raw_text:
        return {}
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise UsageTelemetryError("NoCoBase returned invalid JSON") from exc
    if isinstance(payload, dict) and payload.get("errors"):
        errors = payload.get("errors") or []
        message = "; ".join(
            str(item.get("message"))
            for item in errors
            if isinstance(item, dict) and item.get("message")
        )
        raise UsageTelemetryError(_redact_error_text(message or "NoCoBase request failed"))
    return payload if isinstance(payload, dict) else {"data": payload}


def _coerce_non_negative_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return max(parsed, 0)


def _coerce_non_negative_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return max(parsed, 0.0)


def _usage_timezone():
    raw_timezone = os.getenv(USAGE_TELEMETRY_TIMEZONE_ENV, "Asia/Shanghai").strip() or "Asia/Shanghai"
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(raw_timezone)
    except Exception:
        logger.warning("Invalid %s=%r; falling back to UTC", USAGE_TELEMETRY_TIMEZONE_ENV, raw_timezone)
        return timezone.utc


def _coerce_datetime(value: Any | None) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
    elif isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        dt = datetime.fromisoformat(raw)
    else:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def build_chat_usage_done_event(
    *,
    session_id: Any,
    stream_id: Any,
    user_id: Any,
    profile_name: Any,
    model: Any,
    model_provider: Any,
    usage: dict[str, Any] | None,
    occurred_at: Any | None = None,
    status: str = "done",
    source: str = USAGE_TELEMETRY_SOURCE,
) -> dict[str, Any] | None:
    normalized_session_id = str(session_id or "").strip()
    normalized_stream_id = str(stream_id or "").strip()
    normalized_user_id = str(user_id or "").strip()
    if not normalized_session_id or not normalized_stream_id or not normalized_user_id:
        return None

    usage_payload = usage if isinstance(usage, dict) else {}
    input_tokens = _coerce_non_negative_int(usage_payload.get("input_tokens"))
    output_tokens = _coerce_non_negative_int(usage_payload.get("output_tokens"))
    total_tokens = input_tokens + output_tokens
    cost = _coerce_non_negative_float(usage_payload.get("estimated_cost"))
    duration_seconds = _coerce_non_negative_float(usage_payload.get("duration_seconds"))
    tps = _coerce_non_negative_float(usage_payload.get("tps"))
    occurred_dt = _coerce_datetime(occurred_at)
    business_dt = occurred_dt.astimezone(_usage_timezone())
    iso_year, iso_week, _iso_weekday = business_dt.isocalendar()

    event = {
        "event_key": f"{normalized_session_id}:{normalized_stream_id}",
        "stream_id": normalized_stream_id,
        "session_id": normalized_session_id,
        "user_id": normalized_user_id,
        "profile_name": str(profile_name or "").strip(),
        "model": str(model or "").strip(),
        "model_provider": str(model_provider or "").strip(),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost": cost,
        "duration_seconds": duration_seconds,
        "tps": tps,
        "business_date": business_dt.date().isoformat(),
        "business_week": f"{iso_year}-W{iso_week:02d}",
        "occurred_at": _iso_utc(occurred_dt),
        "status": str(status or "done").strip() or "done",
        "source": str(source or USAGE_TELEMETRY_SOURCE).strip() or USAGE_TELEMETRY_SOURCE,
        "gateway_routing": usage_payload.get("gateway_routing"),
    }
    return event


def _create_usage_event(event: dict[str, Any]) -> dict[str, Any]:
    return _nocobase_request(
        f"/{USAGE_TELEMETRY_COLLECTION}:create",
        method="POST",
        body=event,
    )


def record_chat_usage_done(
    *,
    session_id: Any,
    stream_id: Any,
    user_id: Any,
    profile_name: Any,
    model: Any,
    model_provider: Any,
    usage: dict[str, Any] | None,
    occurred_at: Any | None = None,
) -> bool:
    if not is_usage_telemetry_enabled():
        return False
    event = build_chat_usage_done_event(
        session_id=session_id,
        stream_id=stream_id,
        user_id=user_id,
        profile_name=profile_name,
        model=model,
        model_provider=model_provider,
        usage=usage,
        occurred_at=occurred_at,
    )
    if event is None:
        return False
    _create_usage_event(event)
    return True


def record_chat_usage_done_async(
    *,
    session_id: Any,
    stream_id: Any,
    user_id: Any,
    profile_name: Any,
    model: Any,
    model_provider: Any,
    usage: dict[str, Any] | None,
    occurred_at: Any | None = None,
) -> threading.Thread | None:
    if not is_usage_telemetry_enabled():
        return None
    event = build_chat_usage_done_event(
        session_id=session_id,
        stream_id=stream_id,
        user_id=user_id,
        profile_name=profile_name,
        model=model,
        model_provider=model_provider,
        usage=usage,
        occurred_at=occurred_at,
    )
    if event is None:
        return None

    def _worker() -> None:
        try:
            _create_usage_event(event)
        except Exception as exc:
            logger.warning(
                "Failed to write Hermes chat usage telemetry to NoCoBase: %s",
                _redact_error_text(exc),
            )

    thread = threading.Thread(
        target=_worker,
        daemon=True,
        name=f"usage-telemetry-{str(stream_id or '')[:8]}",
    )
    thread.start()
    return thread
