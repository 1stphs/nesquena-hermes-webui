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
    sync_user_provider_model_config_groups,
    sync_user_provider_model_config,
    user_provider_sync_lock,
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
    include_talent_market_templates = body.get("include_talent_market_templates") is not False

    template_targets: list[dict[str, Any]] = []
    template_skipped: list[dict[str, Any]] = []
    talent_root = _talent_market_templates_root()
    if include_talent_market_templates:
        if not talent_root.exists() or not talent_root.is_dir():
            raise InternalProviderSyncError(
                "Talent market template root is unavailable",
                code="talent_market_root_unavailable",
                status=503,
            )
        template_targets, template_skipped = _list_talent_market_template_targets(talent_root)

    with user_provider_sync_lock(_INTERNAL_SYNC_SCOPE):
        try:
            if include_talent_market_templates:
                grouped_sync = sync_user_provider_model_config_groups(
                    user_id=_INTERNAL_SYNC_SCOPE,
                    mode=SYNC_MODE_ACTIVE_PROVIDER,
                    provider=provider,
                    profile_names=profile_names,
                    targets=template_targets,
                    dry_run=dry_run,
                    use_lock=False,
                )
                sync = _profile_sync_payload(grouped_sync)
                talent_market_templates = _template_sync_payload(grouped_sync, skipped=template_skipped)
            else:
                sync = sync_user_provider_model_config(
                    user_id=_INTERNAL_SYNC_SCOPE,
                    mode=SYNC_MODE_ACTIVE_PROVIDER,
                    provider=provider,
                    profile_names=profile_names,
                    dry_run=dry_run,
                    use_lock=False,
                )
                talent_market_templates = None
        except UserProviderConfigSyncError as exc:
            if include_talent_market_templates:
                payload = dict(exc.payload or {})
                payload["sync"] = _profile_sync_payload(payload)
                payload["talent_market_templates"] = _template_sync_payload(payload, skipped=template_skipped)
                raise UserProviderConfigSyncError(
                    str(exc),
                    code=exc.code,
                    status=exc.status,
                    payload=payload,
                ) from exc
            raise

    payload = {
        "ok": True,
        "provider_id": str(provider.get("id") or ""),
        "profile_names": profile_names,
        "sync": sync,
    }
    if include_talent_market_templates:
        payload["talent_market_templates"] = talent_market_templates
    return payload


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


def _profile_sync_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": bool(payload.get("ok", True)),
        "status": str(payload.get("status") or "unknown"),
        "request_id": str(payload.get("request_id") or ""),
        "mode": str(payload.get("mode") or ""),
        "profiles": list(payload.get("profiles") or []),
    }


def _template_sync_payload(payload: dict[str, Any], *, skipped: list[dict[str, Any]]) -> dict[str, Any]:
    next_payload = {
        "ok": bool(payload.get("ok", True)),
        "status": str(payload.get("status") or "unknown"),
        "request_id": str(payload.get("request_id") or ""),
        "mode": str(payload.get("mode") or ""),
        "targets": list(payload.get("targets") or []),
        "skipped": skipped,
    }
    reason = str(payload.get("reason") or "").strip()
    if reason:
        next_payload["reason"] = reason
    return next_payload


def _talent_market_templates_root() -> Path:
    raw = os.getenv("HERMES_TALENT_MARKET_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()

    hub_root = os.getenv("HERMES_SKILLS_HUB_DIR", "").strip()
    if hub_root:
        return (Path(hub_root).expanduser() / "hermes_talent_market").resolve()

    return Path("/var/www/hermes_talent_market").expanduser().resolve()


def _list_talent_market_template_targets(talent_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    talent_root = Path(talent_root).expanduser().resolve()
    skipped: list[dict[str, Any]] = []
    targets: list[dict[str, Any]] = []
    if not talent_root.exists() or not talent_root.is_dir():
        return targets, skipped

    seen_paths: set[Path] = set()
    for config_path in sorted(talent_root.rglob("config.yaml"), key=lambda item: item.as_posix()):
        label = _talent_market_template_label(config_path, talent_root)
        try:
            resolved_config_path = config_path.resolve(strict=True)
        except OSError:
            skipped.append({"target": label, "status": "skipped", "reason": "unresolvable_config_path"})
            continue
        try:
            resolved_config_path.relative_to(talent_root)
        except ValueError:
            skipped.append({"target": label, "status": "skipped", "reason": "path_escape"})
            continue
        if not resolved_config_path.is_file():
            skipped.append({"target": label, "status": "skipped", "reason": "not_a_file"})
            continue
        if resolved_config_path in seen_paths:
            skipped.append({"target": label, "status": "skipped", "reason": "duplicate_config_path"})
            continue
        seen_paths.add(resolved_config_path)
        targets.append({"label": label, "config_path": resolved_config_path, "root_path": talent_root})
    return targets, skipped


def _talent_market_template_label(config_path: Path, talent_root: Path) -> str:
    try:
        relative_dir = Path(config_path).parent.relative_to(talent_root)
    except ValueError:
        return "talent_market:unknown"
    relative = relative_dir.as_posix()
    return f"talent_market:{relative or '.'}"


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
