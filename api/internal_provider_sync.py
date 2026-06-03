"""Internal Provider sync endpoints for root config + named profiles."""

from __future__ import annotations

import hmac
import os
import re
from pathlib import Path
from typing import Any

from api.profiles import _profiles_root
from api.user_provider import (
    UserProviderLookupError,
    _normalize_provider_record,
    list_global_ai_provider_records_for_service,
)
from api.user_provider_config_sync import (
    SYNC_MODE_ACTIVE_PROVIDER,
    UserProviderConfigSyncError,
    sync_user_provider_model_config,
)

INTERNAL_PROVIDER_SYNC_PATH = "/api/internal/provider-sync/root-profiles"
INTERNAL_PROVIDER_SYNC_TOKEN_ENV = "HERMES_INTERNAL_PROVIDER_SYNC_TOKEN"
_INTERNAL_SYNC_SCOPE = "internal-root-profiles-sync"
_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,149}$")


class InternalProviderSyncError(RuntimeError):
    def __init__(self, message: str, *, code: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def verify_internal_provider_sync_token(handler) -> None:
    expected = os.getenv(INTERNAL_PROVIDER_SYNC_TOKEN_ENV, "").strip()
    if not expected:
        raise InternalProviderSyncError(
            "Internal Provider sync token is not configured",
            code="internal_provider_sync_token_missing",
            status=503,
        )

    raw_header = str(getattr(handler, "headers", {}).get("Authorization", "") or "").strip()
    if not raw_header.lower().startswith("bearer "):
        raise InternalProviderSyncError(
            "Missing internal Provider sync bearer token",
            code="internal_provider_sync_token_invalid",
            status=401,
        )

    provided = raw_header[7:].strip()
    if not provided or not hmac.compare_digest(provided, expected):
        raise InternalProviderSyncError(
            "Invalid internal Provider sync bearer token",
            code="internal_provider_sync_token_invalid",
            status=401,
        )


def sync_internal_provider_root_profiles_payload(body: dict[str, Any]) -> dict[str, Any]:
    provider_id = str(
        body.get("id")
        or body.get("provider_id")
        or body.get("providerId")
        or ""
    ).strip()
    if not provider_id:
        raise InternalProviderSyncError(
            "Missing provider id",
            code="missing_provider_id",
            status=400,
        )

    profile_names = list_internal_sync_profile_names()
    provider = _load_internal_runtime_provider(provider_id)
    dry_run = bool(body.get("dry_run"))
    sync = sync_user_provider_model_config(
        user_id=_INTERNAL_SYNC_SCOPE,
        mode=SYNC_MODE_ACTIVE_PROVIDER,
        provider=provider,
        profile_names=profile_names,
        dry_run=dry_run,
    )
    return {
        "ok": True,
        "provider_id": str(provider.get("id") or ""),
        "profile_names": profile_names,
        "sync": sync,
    }


def list_internal_sync_profile_names() -> list[str]:
    names = ["default"]
    profiles_root = Path(_profiles_root()).expanduser().resolve()
    if not profiles_root.exists() or not profiles_root.is_dir():
        return names

    for entry in sorted(profiles_root.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        name = entry.name.strip()
        if not name or name == "default":
            continue
        if not _PROFILE_NAME_RE.fullmatch(name):
            continue
        names.append(name)
    return names


def error_payload(error: Exception) -> tuple[dict[str, Any], int]:
    if isinstance(error, InternalProviderSyncError):
        return {"ok": False, "error": str(error), "code": error.code}, error.status
    if isinstance(error, UserProviderConfigSyncError):
        payload = {"ok": False, "error": str(error), "code": error.code}
        payload.update(error.payload)
        return payload, error.status
    if isinstance(error, UserProviderLookupError):
        return {"ok": False, "error": str(error), "code": "provider_lookup_failed"}, 503
    return {"ok": False, "error": str(error), "code": "internal_provider_sync_failed"}, 500


def _load_internal_runtime_provider(provider_id: str) -> dict[str, Any]:
    provider_id = str(provider_id or "").strip()
    if not provider_id:
        raise InternalProviderSyncError("Missing provider id", code="missing_provider_id", status=400)

    records = list_global_ai_provider_records_for_service()
    for record in records:
        if str(record.get("id") or "").strip() != provider_id:
            continue
        provider, reason = _normalize_provider_record(record, _INTERNAL_SYNC_SCOPE)
        if provider:
            return provider
        raise InternalProviderSyncError(
            "Provider configuration is incomplete",
            code=reason or "incomplete_provider",
            status=400,
        )

    raise InternalProviderSyncError(
        "Provider not found",
        code="provider_not_found",
        status=404,
    )
