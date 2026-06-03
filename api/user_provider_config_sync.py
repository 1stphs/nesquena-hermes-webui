"""Controlled user Provider -> Hermes profile config sync.

This module only patches the model_v1 whitelist. It never accepts client
filesystem paths or script inputs.
"""

from __future__ import annotations

import copy
import os
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

import yaml

from api.profiles import get_hermes_home_for_profile
from api.user_provider import (
    CANONICAL_AGENT_PROVIDER,
    SUPPORTED_API_MODES,
    UserProviderLookupError,
    clear_user_provider_models_cache,
    force_redact_provider_secret,
    list_user_profile_records,
    masked_provider_key,
)

MODEL_V1_KEYS = ("default", "provider", "base_url", "api_key", "api_mode")
SYNC_MODE_ACTIVE_PROVIDER = "active_provider"
SYNC_MODE_ROOT_DEFAULT = "root_default"
_PROFILE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,149}$")
_USER_SYNC_LOCKS: dict[str, threading.RLock] = {}
_USER_SYNC_LOCKS_LOCK = threading.Lock()
_LAST_SYNC_STATUS: dict[tuple[str, str], dict[str, Any]] = {}
_PROFILE_CONFIG_SYNC_LOCK = threading.RLock()


class UserProviderConfigSyncError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "provider_config_sync_failed",
        status: int = 500,
        payload: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.payload = payload or {}


@contextmanager
def user_provider_sync_lock(user_id: str):
    normalized_user_id = _safe_text(user_id)
    if not normalized_user_id:
        raise UserProviderConfigSyncError(
            "Missing user context",
            code="missing_user_context",
            status=400,
        )
    with _USER_SYNC_LOCKS_LOCK:
        lock = _USER_SYNC_LOCKS.setdefault(normalized_user_id, threading.RLock())
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def get_last_sync_status(user_id: str, provider_id: str) -> dict[str, Any] | None:
    return copy.deepcopy(_LAST_SYNC_STATUS.get((_safe_text(user_id), _safe_text(provider_id))))


def set_last_sync_status(user_id: str, provider_id: str, status: dict[str, Any]) -> None:
    key = (_safe_text(user_id), _safe_text(provider_id))
    if key[0] and key[1]:
        _LAST_SYNC_STATUS[key] = _public_sync_status(status)


def sync_user_provider_model_config(
    *,
    user_id: str,
    mode: str,
    provider: dict[str, Any] | None = None,
    profile_names: Iterable[str] | None = None,
    profile_records: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
    request_id: str | None = None,
    use_lock: bool = True,
) -> dict[str, Any]:
    if use_lock:
        with user_provider_sync_lock(user_id):
            return _sync_user_provider_model_config(
                user_id=user_id,
                mode=mode,
                provider=provider,
                profile_names=profile_names,
                profile_records=profile_records,
                dry_run=dry_run,
                request_id=request_id,
            )
    return _sync_user_provider_model_config(
        user_id=user_id,
        mode=mode,
        provider=provider,
        profile_names=profile_names,
        profile_records=profile_records,
        dry_run=dry_run,
        request_id=request_id,
    )


def sync_single_profile_model_config(
    *,
    user_id: str,
    profile_name: str,
    mode: str = SYNC_MODE_ACTIVE_PROVIDER,
    provider: dict[str, Any] | None = None,
    dry_run: bool = False,
    request_id: str | None = None,
    use_lock: bool = True,
) -> dict[str, Any]:
    return sync_user_provider_model_config(
        user_id=user_id,
        mode=mode,
        provider=provider,
        profile_names=[profile_name],
        dry_run=dry_run,
        request_id=request_id,
        use_lock=use_lock,
    )


def _sync_user_provider_model_config(
    *,
    user_id: str,
    mode: str,
    provider: dict[str, Any] | None,
    profile_names: Iterable[str] | None,
    profile_records: list[dict[str, Any]] | None,
    dry_run: bool,
    request_id: str | None,
) -> dict[str, Any]:
    with _PROFILE_CONFIG_SYNC_LOCK:
        request_id = _safe_text(request_id) or uuid.uuid4().hex
        user_id = _safe_text(user_id)
        if not user_id:
            raise UserProviderConfigSyncError("Missing user context", code="missing_user_context", status=400)
        source = _source_model_v1(mode, provider)
        targets = _target_profiles(user_id, profile_names=profile_names, profile_records=profile_records)
        plan = _build_plan(user_id=user_id, mode=mode, source=source, targets=targets, request_id=request_id)
        if dry_run:
            return {
                "ok": True,
                "status": "dry_run",
                "request_id": request_id,
                "mode": mode,
                "profiles": [_public_profile_plan(item) for item in plan],
            }
        written: list[dict[str, Any]] = []
        try:
            for item in plan:
                failed_item = item
                result = _write_profile_plan(item)
                written.append(result)
                failed_item = None
        except Exception as exc:
            rollback_results = _rollback_written_profiles(written)
            profiles = [_public_write_result(item) for item in written]
            if failed_item is not None:
                profiles.append(_public_failed_write_result(failed_item, exc, _source_secret(source)))
            payload = {
                "ok": False,
                "status": "sync_failed",
                "request_id": request_id,
                "mode": mode,
                "profiles": profiles,
                "rollback": rollback_results,
                "error": _safe_error(exc, _source_secret(source)),
            }
            raise UserProviderConfigSyncError(
                "Provider config sync failed",
                code="sync_failed",
                status=500,
                payload=payload,
            ) from exc

        _invalidate_agent_caches()
        return {
            "ok": True,
            "status": "synced",
            "request_id": request_id,
            "mode": mode,
            "profiles": [_public_write_result(item) for item in written],
        }


def _source_model_v1(mode: str, provider: dict[str, Any] | None) -> dict[str, Any]:
    if mode == SYNC_MODE_ACTIVE_PROVIDER:
        return _active_provider_model_v1(provider or {})
    if mode == SYNC_MODE_ROOT_DEFAULT:
        root_home = get_hermes_home_for_profile("default")
        root_config = _read_yaml_config(root_home)["config"]
        return _extract_model_v1(root_config)
    raise UserProviderConfigSyncError("Unsupported sync mode", code="unsupported_sync_mode", status=400)


def _active_provider_model_v1(provider: dict[str, Any]) -> dict[str, Any]:
    api_mode = _safe_text(provider.get("api_mode")).lower()
    if api_mode not in SUPPORTED_API_MODES:
        raise UserProviderConfigSyncError("Unsupported Provider api_mode", code="unsupported_api_mode", status=400)
    missing = [
        key
        for key, value in (
            ("model_name", provider.get("model_name")),
            ("base_url", provider.get("base_url")),
            ("api_key", provider.get("api_key")),
        )
        if not _safe_text(value)
    ]
    if missing:
        raise UserProviderConfigSyncError(
            "Provider configuration is incomplete",
            code="incomplete_provider",
            status=400,
        )
    return {
        "default": _safe_text(provider.get("model_name")),
        "provider": CANONICAL_AGENT_PROVIDER,
        "base_url": _safe_text(provider.get("base_url")),
        "api_key": str(provider.get("api_key") or ""),
        "api_mode": api_mode,
    }


def _extract_model_v1(config: dict[str, Any]) -> dict[str, Any]:
    model = config.get("model") if isinstance(config, dict) else {}
    if isinstance(model, str):
        return {"default": model}
    if not isinstance(model, dict):
        return {}
    return {key: copy.deepcopy(model[key]) for key in MODEL_V1_KEYS if key in model}


def _target_profiles(
    user_id: str,
    *,
    profile_names: Iterable[str] | None,
    profile_records: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    if profile_names is not None:
        records = [{"name": name} for name in profile_names]
    else:
        try:
            records = profile_records if profile_records is not None else list_user_profile_records(user_id)
        except UserProviderLookupError as exc:
            raise UserProviderConfigSyncError(
                "Profile lookup failed",
                code="profile_lookup_failed",
                status=503,
                payload={"error": _safe_error(exc)},
            ) from exc
    targets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        name = _profile_name_from_record(record)
        if not name or name in seen:
            continue
        seen.add(name)
        if name != "default" and not _PROFILE_NAME_RE.match(name):
            raise UserProviderConfigSyncError(
                "Invalid profile name",
                code="invalid_profile_name",
                status=400,
                payload={"profile": _safe_profile_label(name)},
            )
        home = get_hermes_home_for_profile(name)
        if not home.exists():
            raise UserProviderConfigSyncError(
                "Profile home is unavailable",
                code="profile_home_unavailable",
                status=404,
                payload={"profile": _safe_profile_label(name)},
            )
        targets.append({"name": name, "home": home})
    return targets


def _build_plan(
    *,
    user_id: str,
    mode: str,
    source: dict[str, Any],
    targets: list[dict[str, Any]],
    request_id: str,
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    for target in targets:
        current = _read_yaml_config(target["home"])
        next_config, diff = _apply_model_v1_patch(current["config"], source)
        plan.append(
            {
                "user_id": user_id,
                "request_id": request_id,
                "mode": mode,
                "profile": target["name"],
                "home": target["home"],
                "config_path": current["path"],
                "original_config": current["config"],
                "original_text": current["text"],
                "next_config": next_config,
                "diff": diff,
                "source_secret": _source_secret(source),
            }
        )
    return plan


def _apply_model_v1_patch(config: dict[str, Any], source: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    next_config = copy.deepcopy(config if isinstance(config, dict) else {})
    model = next_config.get("model")
    if isinstance(model, str):
        model = {"default": model}
    elif not isinstance(model, dict):
        model = {}
    else:
        model = copy.deepcopy(model)

    diff: list[dict[str, Any]] = []
    for key in MODEL_V1_KEYS:
        before_exists = key in model
        before = model.get(key)
        if key in source:
            after = source.get(key)
            action = "add" if not before_exists else "change"
            if before != after:
                diff.append(_diff_entry(key, action, before, after, source))
            model[key] = copy.deepcopy(after)
        elif before_exists:
            diff.append(_diff_entry(key, "delete", before, None, source))
            model.pop(key, None)
    next_config["model"] = model
    return next_config, diff


def _write_profile_plan(item: dict[str, Any]) -> dict[str, Any]:
    config_path = Path(item["config_path"])
    config_path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if item["original_text"] is not None:
        backup_path = config_path.with_name(f"{config_path.name}.bak.{int(time.time() * 1000)}")
        backup_path.write_text(item["original_text"], encoding="utf-8")
    _atomic_write_yaml(config_path, item["next_config"])
    return {
        **item,
        "ok": True,
        "status": "synced",
        "backup_path": backup_path,
    }


def _rollback_written_profiles(written: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for item in reversed(written):
        profile = _safe_profile_label(item.get("profile"))
        try:
            original_text = item.get("original_text")
            config_path = Path(item["config_path"])
            if original_text is None:
                if config_path.exists():
                    config_path.unlink()
            else:
                _atomic_write_text(config_path, original_text)
            results.append({"profile": profile, "status": "rolled_back"})
        except Exception as exc:
            results.append({"profile": profile, "status": "rollback_failed", "error": _safe_error(exc)})
    return results


def _read_yaml_config(home: Path) -> dict[str, Any]:
    config_path = Path(home).expanduser() / "config.yaml"
    text = None
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8")
        loaded = yaml.safe_load(text) or {}
    else:
        loaded = {}
    if not isinstance(loaded, dict):
        raise UserProviderConfigSyncError(
            "Profile config must be a YAML object",
            code="invalid_profile_config",
            status=400,
        )
    return {"path": config_path, "text": text, "config": loaded}


def _atomic_write_yaml(path: Path, config: dict[str, Any]) -> None:
    text = yaml.safe_dump(config, sort_keys=False, allow_unicode=True)
    _atomic_write_text(path, text)


def _atomic_write_text(path: Path, text: str) -> None:
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    os.replace(tmp_path, path)


def _diff_entry(
    key: str,
    action: str,
    before: Any,
    after: Any,
    source: dict[str, Any],
) -> dict[str, Any]:
    return {
        "path": f"model.{key}",
        "action": action,
        "before": _redacted_value(key, before, source),
        "after": _redacted_value(key, after, source),
    }


def _redacted_value(key: str, value: Any, source: dict[str, Any]) -> Any:
    if key == "api_key":
        return masked_provider_key(value)
    secret = _source_secret(source)
    if isinstance(value, str):
        return force_redact_provider_secret(value, secret)
    return value


def _source_secret(source: dict[str, Any]) -> str:
    return str(source.get("api_key") or "").strip()


def _profile_name_from_record(record: dict[str, Any]) -> str:
    for key in ("name", "profile_name", "profile_key", "webui_profile_id"):
        value = _safe_text(record.get(key))
        if value:
            return value
    return ""


def _public_profile_plan(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": _safe_profile_label(item.get("profile")),
        "status": "planned",
        "changed": bool(item.get("diff")),
        "diff": item.get("diff") or [],
    }


def _public_write_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": _safe_profile_label(item.get("profile")),
        "status": item.get("status") or ("synced" if item.get("ok") else "failed"),
        "changed": bool(item.get("diff")),
        "diff": item.get("diff") or [],
    }


def _public_failed_write_result(item: dict[str, Any], error: Exception, secret: str | None = None) -> dict[str, Any]:
    payload = _public_write_result({**item, "ok": False, "status": "failed"})
    payload["error"] = _safe_error(error, secret)
    return payload


def _public_sync_status(status: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "status": _safe_text(status.get("status")) or "unknown",
        "request_id": _safe_text(status.get("request_id")),
        "mode": _safe_text(status.get("mode")),
        "updated_at": _safe_text(status.get("updated_at")) or str(int(time.time())),
    }
    if status.get("error"):
        payload["error"] = _safe_error(status.get("error"))
    return payload


def _safe_error(error: Any, secret: str | None = None) -> str:
    text = force_redact_provider_secret(str(error or ""), secret)
    text = re.sub(r"(/[A-Za-z0-9._@%+=:,~-]+)+", "[path]", text)
    return text[:500]


def _safe_profile_label(value: Any) -> str:
    text = _safe_text(value)
    return text if _PROFILE_NAME_RE.match(text) or text == "default" else "invalid-profile"


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _invalidate_agent_caches() -> None:
    clear_user_provider_models_cache()
    try:
        from api.config import SESSION_AGENT_CACHE, SESSION_AGENT_CACHE_LOCK

        with SESSION_AGENT_CACHE_LOCK:
            SESSION_AGENT_CACHE.clear()
    except Exception:
        pass
