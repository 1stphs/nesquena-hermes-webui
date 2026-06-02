"""WebUI orchestration endpoints for user AI Providers."""

from __future__ import annotations

import copy
import time
from typing import Any

from api.user_provider import (
    UserProviderAuthError,
    UserProviderLookupError,
    create_user_ai_provider_record,
    delete_user_ai_provider_record,
    get_user_ai_provider_record,
    list_user_ai_provider_records,
    public_user_ai_provider_record,
    resolve_user_provider,
    test_user_provider_connection,
    update_user_ai_provider_record,
    verify_user_profile_access,
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


def list_user_ai_providers_payload(user_id: str) -> dict[str, Any]:
    records = list_user_ai_provider_records(user_id)
    providers = [
        public_user_ai_provider_record(record, get_last_sync_status(user_id, str(record.get("id") or "")))
        for record in records
    ]
    return {"ok": True, "providers": providers}


def save_user_ai_provider_payload(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    provider_id = _provider_id_from_body(body)
    with user_provider_sync_lock(user_id):
        if provider_id:
            old_record = get_user_ai_provider_record(user_id, provider_id)
            candidate = _merged_provider_candidate(old_record, body)
            was_enabled = _is_enabled(old_record)
            if was_enabled:
                _assert_provider_test_passes(user_id, candidate)
                provider, _reason = _runtime_provider_or_raise(candidate, user_id)
                sync_user_provider_model_config(
                    user_id=user_id,
                    mode=SYNC_MODE_ACTIVE_PROVIDER,
                    provider=provider,
                    dry_run=True,
                    use_lock=False,
                )
            updated = update_user_ai_provider_record(user_id, provider_id, body, partial=True)
            provider_record = {**old_record, **updated, **_provider_public_fields_from_body(body)}
            if was_enabled:
                provider, _reason = _runtime_provider_or_raise(provider_record, user_id)
                try:
                    sync = sync_user_provider_model_config(
                        user_id=user_id,
                        mode=SYNC_MODE_ACTIVE_PROVIDER,
                        provider=provider,
                        use_lock=False,
                    )
                except UserProviderConfigSyncError:
                    update_user_ai_provider_record(user_id, provider_id, _rollback_body(old_record), partial=True)
                    raise
                _remember_sync(user_id, provider_id, sync)
            else:
                sync = None
            return {
                "ok": True,
                "provider": public_user_ai_provider_record(provider_record, get_last_sync_status(user_id, provider_id)),
                "sync": sync,
            }

        created = create_user_ai_provider_record(user_id, body)
        created_id = str(created.get("id") or "")
        return {
            "ok": True,
            "provider": public_user_ai_provider_record(created, get_last_sync_status(user_id, created_id)),
            "sync": None,
        }


def enable_user_ai_provider_payload(user_id: str, provider_id: str) -> dict[str, Any]:
    provider_id = _required_provider_id(provider_id)
    with user_provider_sync_lock(user_id):
        records = list_user_ai_provider_records(user_id)
        target = _find_provider(records, provider_id)
        _assert_provider_test_passes(user_id, target)
        provider, _reason = _runtime_provider_or_raise(target, user_id)
        sync_user_provider_model_config(
            user_id=user_id,
            mode=SYNC_MODE_ACTIVE_PROVIDER,
            provider=provider,
            dry_run=True,
            use_lock=False,
        )
        previous_enabled_ids = [str(item.get("id") or "") for item in records if _is_enabled(item)]
        try:
            for record in records:
                record_id = str(record.get("id") or "")
                next_status = "enabled" if record_id == provider_id else "disabled"
                if str(record.get("status") or "").lower() != next_status:
                    update_user_ai_provider_record(user_id, record_id, {"status": next_status}, partial=True)
            sync = sync_user_provider_model_config(
                user_id=user_id,
                mode=SYNC_MODE_ACTIVE_PROVIDER,
                provider=provider,
                use_lock=False,
            )
        except Exception:
            _restore_enabled_statuses(user_id, records, previous_enabled_ids)
            raise
        _remember_sync(user_id, provider_id, sync)
        updated = get_user_ai_provider_record(user_id, provider_id)
        return {
            "ok": True,
            "provider": public_user_ai_provider_record(updated, get_last_sync_status(user_id, provider_id)),
            "sync": sync,
        }


def disable_user_ai_provider_payload(user_id: str, provider_id: str) -> dict[str, Any]:
    provider_id = _required_provider_id(provider_id)
    with user_provider_sync_lock(user_id):
        target = get_user_ai_provider_record(user_id, provider_id)
        if not _is_enabled(target):
            updated = update_user_ai_provider_record(user_id, provider_id, {"status": "disabled"}, partial=True)
            return {"ok": True, "provider": public_user_ai_provider_record(updated), "sync": None}
        sync_user_provider_model_config(
            user_id=user_id,
            mode=SYNC_MODE_ROOT_DEFAULT,
            dry_run=True,
            use_lock=False,
        )
        try:
            updated = update_user_ai_provider_record(user_id, provider_id, {"status": "disabled"}, partial=True)
            sync = sync_user_provider_model_config(
                user_id=user_id,
                mode=SYNC_MODE_ROOT_DEFAULT,
                use_lock=False,
            )
        except Exception:
            update_user_ai_provider_record(user_id, provider_id, {"status": "enabled"}, partial=True)
            raise
        _remember_sync(user_id, provider_id, sync)
        return {
            "ok": True,
            "provider": public_user_ai_provider_record(updated, get_last_sync_status(user_id, provider_id)),
            "sync": sync,
        }


def delete_user_ai_provider_payload(user_id: str, provider_id: str) -> dict[str, Any]:
    provider_id = _required_provider_id(provider_id)
    with user_provider_sync_lock(user_id):
        target = get_user_ai_provider_record(user_id, provider_id)
        was_enabled = _is_enabled(target)
        sync = None
        if was_enabled:
            sync_user_provider_model_config(
                user_id=user_id,
                mode=SYNC_MODE_ROOT_DEFAULT,
                dry_run=True,
                use_lock=False,
            )
            try:
                update_user_ai_provider_record(user_id, provider_id, {"status": "disabled"}, partial=True)
                sync = sync_user_provider_model_config(
                    user_id=user_id,
                    mode=SYNC_MODE_ROOT_DEFAULT,
                    use_lock=False,
                )
            except Exception:
                update_user_ai_provider_record(user_id, provider_id, {"status": "enabled"}, partial=True)
                raise
        delete_user_ai_provider_record(user_id, provider_id)
        if sync:
            _remember_sync(user_id, provider_id, sync)
        return {"ok": True, "provider_id": provider_id, "sync": sync}


def sync_user_ai_provider_payload(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    mode = str(body.get("mode") or SYNC_MODE_ACTIVE_PROVIDER).strip()
    dry_run = bool(body.get("dry_run"))
    with user_provider_sync_lock(user_id):
        provider = None
        provider_id = ""
        if mode == SYNC_MODE_ACTIVE_PROVIDER:
            resolution = resolve_user_provider(user_id)
            if not resolution.is_active:
                raise UserProviderConfigSyncError(
                    "Active Provider is unavailable",
                    code=resolution.reason or "active_provider_unavailable",
                    status=503 if resolution.status == "lookup_failed" else 400,
                    payload={"provider_resolution": {"status": resolution.status, "reason": resolution.reason}},
                )
            provider = resolution.provider
            provider_id = str(provider.get("id") or "")
        sync = sync_user_provider_model_config(
            user_id=user_id,
            mode=mode,
            provider=provider,
            dry_run=dry_run,
            use_lock=False,
        )
        if provider_id and not dry_run:
            _remember_sync(user_id, provider_id, sync)
        return {"ok": True, "sync": sync}


def sync_user_ai_provider_profile_payload(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    profile_name = str(body.get("profile_name") or body.get("profileName") or body.get("profile") or "").strip()
    if not profile_name:
        raise UserProviderConfigSyncError("Missing profile name", code="missing_profile_name", status=400)
    dry_run = bool(body.get("dry_run"))
    with user_provider_sync_lock(user_id):
        verify_user_profile_access(user_id, profile_name)
        resolution = resolve_user_provider(user_id)
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
        resolution = resolve_user_provider(user_id)
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


def test_user_ai_provider_payload(user_id: str, body: dict[str, Any]) -> dict[str, Any]:
    provider_id = _provider_id_from_body(body)
    if provider_id and not (body.get("api_key") or body.get("apiKey")):
        record = get_user_ai_provider_record(user_id, provider_id)
        test_body = {
            "name": record.get("name"),
            "base_url": record.get("base_url"),
            "model_name": record.get("model_name"),
            "api_mode": record.get("api_mode"),
            "thinking_level": record.get("thinking_level"),
            "api_key": record.get("api_key"),
        }
        return test_user_provider_connection(user_id, test_body)
    return test_user_provider_connection(user_id, body)


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


def _assert_provider_test_passes(user_id: str, provider: dict[str, Any]) -> None:
    result = test_user_provider_connection(user_id, provider)
    if not result.get("ok"):
        raise UserProviderConfigSyncError(
            result.get("message") or result.get("error") or "Provider test failed",
            code=str(result.get("error") or "provider_test_failed"),
            status=400,
        )


def _runtime_provider_or_raise(record: dict[str, Any], user_id: str) -> tuple[dict[str, Any], str]:
    provider, reason = _normalize_provider_record({**record, "status": "enabled"}, user_id)
    if not provider:
        raise UserProviderConfigSyncError(
            "Provider configuration is incomplete or unsupported",
            code=reason or "incomplete_provider",
            status=400,
        )
    return provider, reason


def _merged_provider_candidate(old_record: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(old_record)
    candidate.update(_provider_public_fields_from_body(body))
    if "api_key" not in body and "apiKey" not in body:
        candidate["api_key"] = old_record.get("api_key")
    return candidate


def _provider_public_fields_from_body(body: dict[str, Any]) -> dict[str, Any]:
    source = body if isinstance(body, dict) else {}
    mapping = {
        "name": ("name",),
        "provider_slug": ("provider_slug", "providerSlug"),
        "base_url": ("base_url", "baseUrl"),
        "model_name": ("model_name", "modelName"),
        "api_mode": ("api_mode", "apiMode"),
        "thinking_level": ("thinking_level", "thinkingLevel"),
        "api_key": ("api_key", "apiKey"),
        "status": ("status",),
    }
    result: dict[str, Any] = {}
    for target, names in mapping.items():
        for name in names:
            if name in source:
                result[target] = source.get(name)
                break
    return result


def _rollback_body(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record.get(key)
        for key in (
            "name",
            "provider_slug",
            "base_url",
            "model_name",
            "api_mode",
            "thinking_level",
            "api_key",
            "status",
        )
        if key in record
    }


def _restore_enabled_statuses(user_id: str, records: list[dict[str, Any]], enabled_ids: list[str]) -> None:
    enabled_set = set(enabled_ids)
    for record in records:
        record_id = str(record.get("id") or "")
        if not record_id:
            continue
        status = "enabled" if record_id in enabled_set else "disabled"
        try:
            update_user_ai_provider_record(user_id, record_id, {"status": status}, partial=True)
        except Exception:
            pass


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
            "updated_at": str(int(time.time())),
        },
    )


def _find_provider(records: list[dict[str, Any]], provider_id: str) -> dict[str, Any]:
    for record in records:
        if str(record.get("id") or "") == provider_id:
            return record
    raise UserProviderLookupError("provider not found")


def _is_enabled(record: dict[str, Any]) -> bool:
    return str(record.get("status") or "").strip().lower() == "enabled"


def _provider_id_from_body(body: dict[str, Any]) -> str:
    return str(body.get("id") or body.get("provider_id") or body.get("providerId") or "").strip()


def _required_provider_id(provider_id: str) -> str:
    normalized = str(provider_id or "").strip()
    if not normalized:
        raise UserProviderConfigSyncError("Missing Provider id", code="missing_provider_id", status=400)
    return normalized
