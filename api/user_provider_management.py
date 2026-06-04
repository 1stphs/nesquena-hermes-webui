"""WebUI orchestration endpoints for user AI Providers."""

from __future__ import annotations

from typing import Any

from api.user_provider import (
    UserProviderAuthError,
    UserProviderLookupError,
    get_default_provider_id,
    get_default_provider_record_for_user,
    get_user_profile_record_by_id,
    get_user_profile_record_by_name,
    get_user_profile_provider_id,
    list_user_ai_provider_records,
    list_user_profile_records,
    list_global_ai_provider_records_for_service,
    public_user_ai_provider_record,
    resolve_user_profile_provider,
    resolve_user_profile_sync_name,
    set_user_profile_provider_id,
    _normalize_provider_record,
)
from api.user_provider_config_sync import (
    SYNC_MODE_ACTIVE_PROVIDER,
    SYNC_MODE_ROOT_DEFAULT,
    UserProviderConfigSyncError,
    get_last_sync_status,
    set_last_sync_status,
    sync_single_profile_model_config,
    sync_user_provider_model_config,
    user_provider_sync_lock,
)


def list_user_ai_providers_payload(user_id: str, profile_id: str = "") -> dict[str, Any]:
    profile = None
    profile_provider_id = ""
    selected_provider_id = ""
    if str(profile_id or "").strip():
        profile = get_user_profile_record_by_id(user_id, profile_id)
        profile_provider_id = get_user_profile_provider_id(profile)
        selected_provider_id = profile_provider_id or get_default_provider_id(user_id)
    records = list_user_ai_provider_records(
        user_id,
        selected_provider_id=selected_provider_id,
        use_default_when_empty=bool(profile is not None and not profile_provider_id),
    )
    providers = [
        public_user_ai_provider_record(record, get_last_sync_status(user_id, str(record.get("id") or "")))
        for record in records
    ]
    payload = {"ok": True, "providers": providers}
    if profile is not None:
        payload["profile"] = {
            **profile,
            "hermes_providers_id": profile_provider_id or None,
        }
        payload["profile_provider_id"] = profile_provider_id
        payload["selected_provider_id"] = selected_provider_id
        payload["uses_system_default"] = not bool(profile_provider_id)
    return payload


def save_user_ai_provider_payload(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise UserProviderConfigSyncError(
        "User custom Provider upload is disabled",
        code="provider_write_disabled",
        status=405,
    )


def enable_user_ai_provider_payload(user_id: str, profile_id: str, provider_id: str) -> dict[str, Any]:
    profile_id = _required_profile_id(profile_id)
    provider_id = _required_provider_id(provider_id)
    with user_provider_sync_lock(user_id):
        profile = get_user_profile_record_by_id(user_id, profile_id)
        profile_owner_id = str(profile.get("user_id") or profile.get("userId") or "").strip()
        if profile_owner_id != user_id:
            raise UserProviderAuthError("Profile is not available for current user", status=403, code="profile_forbidden")
        profile_name = resolve_user_profile_sync_name(profile)
        if not profile_name:
            raise UserProviderConfigSyncError("Profile name is missing", code="missing_profile_name", status=400)
        provider_records = list_global_ai_provider_records_for_service()
        target = next((record for record in provider_records if str(record.get("id") or "").strip() == provider_id), None)
        if not target:
            raise UserProviderConfigSyncError("Provider not found", code="provider_not_found", status=404)
        provider, _reason = _runtime_provider_or_raise(target, user_id)
        previous_provider_id = get_user_profile_provider_id(profile)
        sync_single_profile_model_config(
            user_id=user_id,
            profile_name=profile_name,
            provider=provider,
            dry_run=True,
            use_lock=False,
        )
        try:
            updated_profile = set_user_profile_provider_id(user_id, profile_id, provider_id)
            sync = sync_single_profile_model_config(
                user_id=user_id,
                profile_name=profile_name,
                provider=provider,
                use_lock=False,
            )
        except Exception:
            set_user_profile_provider_id(user_id, profile_id, previous_provider_id or None)
            raise
        _remember_sync(user_id, provider_id, sync)
        return {
            "ok": True,
            "profile": {
                **profile,
                **updated_profile,
                "hermes_providers_id": provider_id,
            },
            "provider": public_user_ai_provider_record(
                {
                    **target,
                    "user_id": user_id,
                    "status": "enabled",
                    "selected": True,
                    "active": True,
                    "enabled": True,
                },
                get_last_sync_status(user_id, provider_id),
            ),
            "sync": sync,
        }


def disable_user_ai_provider_payload(user_id: str, profile_id: str) -> dict[str, Any]:
    profile_id = _required_profile_id(profile_id)
    with user_provider_sync_lock(user_id):
        profile = get_user_profile_record_by_id(user_id, profile_id)
        profile_owner_id = str(profile.get("user_id") or profile.get("userId") or "").strip()
        if profile_owner_id != user_id:
            raise UserProviderAuthError("Profile is not available for current user", status=403, code="profile_forbidden")
        profile_name = resolve_user_profile_sync_name(profile)
        if not profile_name:
            raise UserProviderConfigSyncError("Profile name is missing", code="missing_profile_name", status=400)
        previous_provider_id = get_user_profile_provider_id(profile)
        default_record = get_default_provider_record_for_user(user_id)
        if not default_record:
            raise UserProviderConfigSyncError(
                "System default Provider is unavailable",
                code="no_default_provider",
                status=400,
            )
        provider, _reason = _runtime_provider_or_raise(default_record, user_id)
        provider_id = str(provider.get("id") or "")
        sync_single_profile_model_config(
            user_id=user_id,
            profile_name=profile_name,
            provider=provider,
            dry_run=True,
            use_lock=False,
        )
        try:
            updated_profile = set_user_profile_provider_id(user_id, profile_id, None)
            sync = sync_single_profile_model_config(
                user_id=user_id,
                profile_name=profile_name,
                provider=provider,
                use_lock=False,
            )
        except Exception:
            set_user_profile_provider_id(user_id, profile_id, previous_provider_id or None)
            raise
        _remember_sync(user_id, provider_id, sync)
        updated = {
            **provider,
            "status": "enabled",
            "selected": True,
            "active": True,
            "enabled": True,
        }
        return {
            "ok": True,
            "profile": {
                **profile,
                **updated_profile,
                "hermes_providers_id": None,
            },
            "provider": public_user_ai_provider_record(updated, get_last_sync_status(user_id, provider_id)),
            "sync": sync,
        }


def delete_user_ai_provider_payload(user_id: str, provider_id: str) -> dict[str, Any]:
    raise UserProviderConfigSyncError(
        "User custom Provider upload is disabled",
        code="provider_write_disabled",
        status=405,
    )


def sync_user_ai_provider_payload(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    mode = str(body.get("mode") or SYNC_MODE_ACTIVE_PROVIDER).strip()
    dry_run = bool(body.get("dry_run"))
    with user_provider_sync_lock(user_id):
        if mode == SYNC_MODE_ACTIVE_PROVIDER:
            sync = _sync_all_profile_providers(user_id, dry_run=dry_run)
            if not dry_run:
                for item in sync.get("profiles") or []:
                    provider_id = str(item.get("provider_id") or "")
                    if provider_id:
                        _remember_sync(user_id, provider_id, sync)
            return {"ok": True, "sync": sync}
        sync = sync_user_provider_model_config(
            user_id=user_id,
            mode=mode,
            dry_run=dry_run,
            use_lock=False,
        )
        return {"ok": True, "sync": sync}


def sync_user_ai_provider_profile_payload(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    profile_id = str(body.get("profile_id") or body.get("profileId") or "").strip()
    profile_name = str(body.get("profile_name") or body.get("profileName") or body.get("profile") or "").strip()
    if not profile_id and not profile_name:
        raise UserProviderConfigSyncError("Missing profile", code="missing_profile", status=400)
    dry_run = bool(body.get("dry_run"))
    with user_provider_sync_lock(user_id):
        profile = (
            get_user_profile_record_by_id(user_id, profile_id)
            if profile_id
            else get_user_profile_record_by_name(user_id, profile_name)
        )
        profile_name = resolve_user_profile_sync_name(profile)
        if not profile_name:
            raise UserProviderConfigSyncError("Profile name is missing", code="missing_profile_name", status=400)
        resolution = resolve_user_profile_provider(
            user_id,
            profile_id=str(profile.get("id") or profile_id),
            profile_name=profile_name,
        )
        if not resolution.is_active:
            if resolution.status == "lookup_failed":
                raise UserProviderConfigSyncError(
                    "Active Provider lookup failed",
                    code=resolution.reason or "provider_lookup_failed",
                    status=503,
                    payload={"provider_resolution": {"status": resolution.status, "reason": resolution.reason}},
                )
            return {
                "ok": True,
                "sync": {
                    "status": "skipped",
                    "reason": resolution.reason,
                    "provider_resolution": resolution.status,
                },
            }
        provider = resolution.provider or {}
        sync = sync_single_profile_model_config(
            user_id=user_id,
            profile_name=profile_name,
            provider=provider,
            dry_run=dry_run,
            use_lock=False,
        )
        if not dry_run:
            _remember_sync(user_id, str(provider.get("id") or ""), sync)
        return {"ok": True, "sync": sync}


def sync_new_profile_if_enabled(user_id: str, profile_name: str) -> dict[str, Any] | None:
    with user_provider_sync_lock(user_id):
        resolution = resolve_user_profile_provider(user_id, profile_name=profile_name)
        if not resolution.is_active:
            if resolution.status == "lookup_failed":
                raise UserProviderConfigSyncError(
                    "Active Provider lookup failed",
                    code=resolution.reason or "provider_lookup_failed",
                    status=503,
                    payload={"provider_resolution": {"status": resolution.status, "reason": resolution.reason}},
                )
            return {"status": "skipped", "reason": resolution.reason, "provider_resolution": resolution.status}
        provider = resolution.provider or {}
        sync = sync_single_profile_model_config(
            user_id=user_id,
            profile_name=profile_name,
            provider=provider,
            use_lock=False,
        )
        _remember_sync(user_id, str(provider.get("id") or ""), sync)
        return sync


def _sync_all_profile_providers(user_id: str, *, dry_run: bool) -> dict[str, Any]:
    profiles = list_user_profile_records(user_id)
    results: list[dict[str, Any]] = []
    failures = 0
    synced = 0
    skipped = 0
    for profile in profiles:
        profile_id = str(profile.get("id") or "")
        profile_name = resolve_user_profile_sync_name(profile)
        base_payload = {
            "profile_id": profile_id,
            "profile_name": profile_name,
        }
        if not profile_name:
            skipped += 1
            results.append(
                {
                    **base_payload,
                    "status": "skipped",
                    "reason": "missing_profile_name",
                }
            )
            continue
        resolution = resolve_user_profile_provider(user_id, profile_id=profile_id, profile_name=profile_name)
        if not resolution.is_active:
            status_code = 503 if resolution.status == "lookup_failed" else 400
            if status_code >= 500:
                failures += 1
            else:
                skipped += 1
            results.append(
                {
                    **base_payload,
                    "status": "failed" if status_code >= 500 else "skipped",
                    "reason": resolution.reason,
                    "provider_resolution": {"status": resolution.status, "reason": resolution.reason},
                }
            )
            continue
        provider = resolution.provider or {}
        try:
            sync = sync_single_profile_model_config(
                user_id=user_id,
                profile_name=profile_name,
                provider=provider,
                dry_run=dry_run,
                use_lock=False,
            )
            synced += 1
            results.append(
                {
                    **base_payload,
                    "status": "planned" if dry_run else "synced",
                    "provider_id": str(provider.get("id") or ""),
                    "provider_resolution": {"status": resolution.status, "reason": resolution.reason},
                    "sync": sync,
                }
            )
        except Exception as exc:
            failures += 1
            results.append(
                {
                    **base_payload,
                    "status": "failed",
                    "provider_id": str(provider.get("id") or ""),
                    "error": str(exc),
                }
            )
    if failures:
        status = "partial_failed"
    elif dry_run:
        status = "planned"
    else:
        status = "synced"
    return {
        "status": status,
        "mode": SYNC_MODE_ACTIVE_PROVIDER,
        "dry_run": dry_run,
        "profiles": results,
        "summary": {
            "total": len(results),
            "synced": synced,
            "skipped": skipped,
            "failed": failures,
        },
    }


def test_user_ai_provider_payload(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    raise UserProviderConfigSyncError(
        "User custom Provider upload is disabled",
        code="provider_write_disabled",
        status=405,
    )


def error_payload(error: Exception) -> tuple[dict[str, Any], int]:
    if isinstance(error, UserProviderConfigSyncError):
        payload = {"ok": False, "error": str(error), "code": error.code}
        payload.update(error.payload)
        return payload, error.status
    if isinstance(error, UserProviderAuthError):
        return {"ok": False, "error": str(error), "code": error.code}, error.status
    if isinstance(error, UserProviderLookupError):
        return {"ok": False, "error": str(error), "code": "provider_lookup_failed"}, 503
    return {"ok": False, "error": str(error), "code": "provider_request_failed"}, 500


def _runtime_provider_or_raise(record: dict[str, Any], user_id: str) -> tuple[dict[str, Any], str]:
    provider, reason = _normalize_provider_record(
        {
            **record,
            "status": "enabled",
            "selected": True,
            "active": True,
            "enabled": True,
        },
        user_id,
    )
    if not provider:
        raise UserProviderConfigSyncError(
            "Provider configuration is incomplete or unsupported",
            code=reason or "incomplete_provider",
            status=400,
        )
    return provider, reason


def _remember_sync(user_id: str, provider_id: str, sync: dict[str, Any] | None) -> None:
    if not sync:
        return
    set_last_sync_status(
        user_id,
        provider_id,
        {
            "status": sync.get("status") or "synced",
            "request_id": sync.get("request_id") or "",
            "mode": sync.get("mode") or "",
        },
    )


def _find_provider(records: list[dict[str, Any]], provider_id: str) -> dict[str, Any]:
    for record in records:
        if str(record.get("id") or "") == provider_id:
            return record
    raise UserProviderLookupError("provider not found")


def _required_provider_id(provider_id: str) -> str:
    normalized = str(provider_id or "").strip()
    if not normalized:
        raise UserProviderConfigSyncError("Missing Provider id", code="missing_provider_id", status=400)
    return normalized


def _required_profile_id(profile_id: str) -> str:
    normalized = str(profile_id or "").strip()
    if not normalized:
        raise UserProviderConfigSyncError("Missing Profile id", code="missing_profile_id", status=400)
    return normalized
