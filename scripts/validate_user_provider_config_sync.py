"""Focused validation for user Provider profile config sync."""

from __future__ import annotations

import copy
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import api.config as webui_config
import api.user_provider as provider_runtime
import api.user_provider_management as management
import api.user_provider_config_sync as sync


SECRET = "fake-config-sync-secret-123456"


def _write_config(home: Path, config: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _read_config(home: Path) -> dict:
    return yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8")) or {}


def _provider() -> dict:
    return {
        "id": "provider-a",
        "name": "Provider A",
        "provider_slug": "provider-a",
        "base_url": "https://provider.example/v1",
        "model_name": "provider-model",
        "api_mode": "codex_responses",
        "api_key": SECRET,
        "status": "enabled",
    }


def _with_fake_profiles(base: Path):
    homes = {
        "default": base,
        "p1": base / "profiles" / "p1",
        "p2": base / "profiles" / "p2",
    }
    original_resolver = sync.get_hermes_home_for_profile
    sync.get_hermes_home_for_profile = lambda name: homes.get(str(name or "default"), homes["default"])
    return homes, original_resolver


def validate_active_provider_sync_and_redaction() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        homes, original_resolver = _with_fake_profiles(base)
        try:
            _write_config(
                homes["default"],
                {
                    "model": {"default": "root-model", "api_mode": "anthropic_messages"},
                },
            )
            original_profile_config = {
                "model": {
                    "default": "old-model",
                    "provider": "custom",
                    "base_url": "https://old.example/v1",
                    "api_key": "fake-old-config-secret",
                    "api_mode": "anthropic_messages",
                    "other": "keep",
                },
                "auxiliary": {"title_generation": {"model": "keep-aux"}},
                "terminal": {"cwd": "/workspace"},
                "workspace": "/workspace",
            }
            _write_config(homes["p1"], copy.deepcopy(original_profile_config))
            _write_config(homes["p2"], copy.deepcopy(original_profile_config))
            dry_run = sync.sync_user_provider_model_config(
                user_id="u1",
                mode=sync.SYNC_MODE_ACTIVE_PROVIDER,
                provider=_provider(),
                profile_records=[{"name": "p1"}, {"name": "p2"}],
                dry_run=True,
            )
            dry_run_text = str(dry_run)
            assert SECRET not in dry_run_text
            assert "****" in dry_run_text

            webui_config.SESSION_AGENT_CACHE["session-a"] = object()
            result = sync.sync_user_provider_model_config(
                user_id="u1",
                mode=sync.SYNC_MODE_ACTIVE_PROVIDER,
                provider=_provider(),
                profile_records=[{"name": "p1"}, {"name": "p2"}],
            )
            assert result["status"] == "synced"
            assert not webui_config.SESSION_AGENT_CACHE

            updated = _read_config(homes["p1"])
            assert updated["model"]["default"] == "provider-model"
            assert updated["model"]["provider"] == "custom"
            assert updated["model"]["base_url"] == "https://provider.example/v1"
            assert updated["model"]["api_key"] == SECRET
            assert updated["model"]["api_mode"] == "codex_responses"
            assert updated["model"]["other"] == "keep"
            assert updated["auxiliary"] == original_profile_config["auxiliary"]
            assert updated["terminal"] == original_profile_config["terminal"]
            assert updated["workspace"] == original_profile_config["workspace"]
            assert "providers" not in updated
            assert "custom_providers" not in updated
        finally:
            sync.get_hermes_home_for_profile = original_resolver


def validate_root_default_exact_delete() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        homes, original_resolver = _with_fake_profiles(base)
        try:
            _write_config(
                homes["default"],
                {
                    "model": {
                        "default": "root-model",
                        "api_mode": "anthropic_messages",
                    },
                    "security": {"redact_secrets": True},
                },
            )
            _write_config(
                homes["p1"],
                {
                    "model": {
                        "default": "provider-model",
                        "provider": "custom",
                        "base_url": "https://provider.example/v1",
                        "api_key": SECRET,
                        "api_mode": "codex_responses",
                        "other": "keep",
                    },
                    "memory": {"enabled": True},
                },
            )
            result = sync.sync_user_provider_model_config(
                user_id="u1",
                mode=sync.SYNC_MODE_ROOT_DEFAULT,
                profile_records=[{"name": "p1"}],
            )
            assert result["status"] == "synced"
            updated = _read_config(homes["p1"])
            assert updated["model"]["default"] == "root-model"
            assert updated["model"]["api_mode"] == "anthropic_messages"
            assert "provider" not in updated["model"]
            assert "base_url" not in updated["model"]
            assert "api_key" not in updated["model"]
            assert updated["model"]["other"] == "keep"
            assert updated["memory"] == {"enabled": True}
        finally:
            sync.get_hermes_home_for_profile = original_resolver


def validate_partial_failure_rolls_back_and_redacts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        homes, original_resolver = _with_fake_profiles(base)
        original_writer = sync._write_profile_plan
        try:
            _write_config(homes["default"], {"model": {"default": "root-model"}})
            before = {"model": {"default": "before"}, "cron": {"enabled": True}}
            _write_config(homes["p1"], copy.deepcopy(before))
            _write_config(homes["p2"], copy.deepcopy(before))

            def fail_second(item):
                if item["profile"] == "p2":
                    raise RuntimeError(f"failed with {SECRET} at /tmp/private/config.yaml")
                return original_writer(item)

            sync._write_profile_plan = fail_second
            try:
                sync.sync_user_provider_model_config(
                    user_id="u1",
                    mode=sync.SYNC_MODE_ACTIVE_PROVIDER,
                    provider=_provider(),
                    profile_records=[{"name": "p1"}, {"name": "p2"}],
                )
            except sync.UserProviderConfigSyncError as exc:
                payload_text = str(exc.payload)
                assert SECRET not in payload_text
                assert "/tmp/private" not in payload_text
                assert exc.payload["status"] == "sync_failed"
                failed_profiles = [
                    item
                    for item in exc.payload.get("profiles", [])
                    if item.get("profile") == "p2" and item.get("status") == "failed"
                ]
                assert failed_profiles
                assert "error" in failed_profiles[0]
            else:
                raise AssertionError("sync failure was expected")

            assert _read_config(homes["p1"]) == before
            assert _read_config(homes["p2"]) == before
        finally:
            sync._write_profile_plan = original_writer
            sync.get_hermes_home_for_profile = original_resolver


def validate_management_enable_updates_single_profile_and_uses_global_provider() -> None:
    original_get_profile = management.get_user_profile_record_by_id
    original_set_profile_provider = management.set_user_profile_provider_id
    original_provider_list = management.list_global_ai_provider_records_for_service
    original_sync = management.sync_single_profile_model_config
    original_host_validator = provider_runtime._validate_provider_host
    profile_state = {
        "record": {
            "id": "profile-1",
            "user_id": "u1",
            "profile_name": "p1",
            "display_name": "Profile 1",
            "hermes_providers_id": "old-provider",
        }
    }
    sync_calls = []
    profile_updates = []
    provider_record = {
        "id": "provider-a",
        "name": "Provider A",
        "provider_name": "Provider A",
        "provider_slug": "provider-a",
        "base_url": "https://provider.example/v1",
        "model_name": "provider-model",
        "api_mode": "codex_responses",
        "api_key": SECRET,
        "status": "enabled",
        "is_enable": True,
        "updatedAt": "2026-06-01T00:00:00Z",
    }

    def sync_stub(**kwargs):
        sync_calls.append(kwargs)
        if kwargs.get("dry_run"):
            return {"ok": True, "status": "dry_run", "profiles": []}
        return {
            "ok": True,
            "status": "synced",
            "request_id": "single-profile-request",
            "mode": kwargs.get("mode"),
            "profiles": [{"profile": kwargs.get("profile_name"), "status": "synced"}],
        }

    try:
        management.get_user_profile_record_by_id = lambda user_id, profile_id: copy.deepcopy(profile_state["record"])
        management.set_user_profile_provider_id = (
            lambda user_id, profile_id, provider_id: profile_updates.append((profile_id, provider_id or ""))
            or profile_state["record"].__setitem__("hermes_providers_id", provider_id or None)
            or {"id": profile_id, "hermes_providers_id": provider_id or None}
        )
        management.list_global_ai_provider_records_for_service = lambda: [copy.deepcopy(provider_record)]
        management.sync_single_profile_model_config = sync_stub
        provider_runtime._validate_provider_host = lambda hostname, port: None
        result = management.enable_user_ai_provider_payload("u1", "profile-1", "provider-a")
        assert result["ok"] is True
        assert result["profile"]["id"] == "profile-1"
        assert result["profile"]["hermes_providers_id"] == "provider-a"
        assert result["provider"]["id"] == "provider-a"
        assert result["provider"]["status"] == "enabled"
        assert result["sync"]["status"] == "synced"
        assert profile_updates == [("profile-1", "provider-a")]
        assert profile_state["record"]["hermes_providers_id"] == "provider-a"
        assert len(sync_calls) == 2
        assert sync_calls[0].get("dry_run") is True
        assert sync_calls[0]["profile_name"] == "p1"
        assert sync_calls[0]["provider"]["id"] == "provider-a"
        assert sync_calls[1].get("dry_run") in (None, False)
        assert sync_calls[1]["profile_name"] == "p1"
        assert sync_calls[1]["provider"]["id"] == "provider-a"
    finally:
        management.get_user_profile_record_by_id = original_get_profile
        management.set_user_profile_provider_id = original_set_profile_provider
        management.list_global_ai_provider_records_for_service = original_provider_list
        management.sync_single_profile_model_config = original_sync
        provider_runtime._validate_provider_host = original_host_validator


def validate_management_enable_rolls_back_user_selection_on_sync_failure() -> None:
    original_get_profile = management.get_user_profile_record_by_id
    original_set_profile_provider = management.set_user_profile_provider_id
    original_provider_list = management.list_global_ai_provider_records_for_service
    original_sync = management.sync_single_profile_model_config
    original_host_validator = provider_runtime._validate_provider_host
    profile_state = {
        "record": {
            "id": "profile-1",
            "user_id": "u1",
            "profile_name": "p1",
            "display_name": "Profile 1",
            "hermes_providers_id": "old-provider",
        }
    }
    sync_calls = []
    profile_updates = []
    provider_record = {
        "id": "provider-a",
        "name": "Provider A",
        "provider_name": "Provider A",
        "provider_slug": "provider-a",
        "base_url": "https://provider.example/v1",
        "model_name": "provider-model",
        "api_mode": "codex_responses",
        "api_key": SECRET,
        "status": "enabled",
        "is_enable": True,
        "updatedAt": "2026-06-01T00:00:00Z",
    }

    def sync_failure(**kwargs):
        sync_calls.append(kwargs)
        if kwargs.get("dry_run"):
            return {"ok": True, "status": "dry_run", "profiles": []}
        raise sync.UserProviderConfigSyncError(
            "sync failed",
            code="sync_failed",
            payload={"status": "sync_failed", "error": "redacted"},
        )

    try:
        management.get_user_profile_record_by_id = lambda user_id, profile_id: copy.deepcopy(profile_state["record"])
        management.set_user_profile_provider_id = (
            lambda user_id, profile_id, provider_id: profile_updates.append((profile_id, provider_id or ""))
            or profile_state["record"].__setitem__("hermes_providers_id", provider_id or None)
            or {"id": profile_id, "hermes_providers_id": provider_id or None}
        )
        management.list_global_ai_provider_records_for_service = lambda: [copy.deepcopy(provider_record)]
        management.sync_single_profile_model_config = sync_failure
        provider_runtime._validate_provider_host = lambda hostname, port: None
        try:
            management.enable_user_ai_provider_payload("u1", "profile-1", "provider-a")
        except sync.UserProviderConfigSyncError as exc:
            assert exc.code == "sync_failed"
        else:
            raise AssertionError("enable should fail when sync fails")
        assert profile_updates == [("profile-1", "provider-a"), ("profile-1", "old-provider")]
        assert profile_state["record"]["hermes_providers_id"] == "old-provider"
        assert len(sync_calls) == 2
        assert sync_calls[0].get("dry_run") is True
        assert sync_calls[1].get("dry_run") in (None, False)
    finally:
        management.get_user_profile_record_by_id = original_get_profile
        management.set_user_profile_provider_id = original_set_profile_provider
        management.list_global_ai_provider_records_for_service = original_provider_list
        management.sync_single_profile_model_config = original_sync
        provider_runtime._validate_provider_host = original_host_validator


def validate_management_enable_rejects_foreign_profile_id() -> None:
    original_get_profile = management.get_user_profile_record_by_id
    original_provider_list = management.list_global_ai_provider_records_for_service
    original_sync = management.sync_single_profile_model_config
    profile_state = {
        "record": {
            "id": "profile-foreign",
            "user_id": "other-user",
            "profile_name": "foreign",
            "display_name": "Foreign",
            "hermes_providers_id": None,
        }
    }
    sync_calls = []

    try:
        management.get_user_profile_record_by_id = lambda user_id, profile_id: copy.deepcopy(profile_state["record"])
        management.list_global_ai_provider_records_for_service = lambda: (_ for _ in ()).throw(
            AssertionError("provider lookup must not run for a foreign profile")
        )
        management.sync_single_profile_model_config = lambda **kwargs: sync_calls.append(kwargs)
        try:
            management.enable_user_ai_provider_payload("u1", "profile-foreign", "provider-a")
        except management.UserProviderAuthError as exc:
            assert exc.code == "profile_forbidden"
            assert exc.status == 403
        else:
            raise AssertionError("foreign profile id should be rejected")
        assert sync_calls == []
    finally:
        management.get_user_profile_record_by_id = original_get_profile
        management.list_global_ai_provider_records_for_service = original_provider_list
        management.sync_single_profile_model_config = original_sync


def validate_single_profile_payload_uses_active_provider() -> None:
    original_resolver = management.resolve_user_profile_provider
    original_get_profile = management.get_user_profile_record_by_name
    original_sync = management.sync_single_profile_model_config
    calls = []

    class Resolution:
        is_active = True
        status = "enabled"
        reason = ""
        provider = _provider()

    try:
        management.get_user_profile_record_by_name = (
            lambda user_id, profile_name: {"id": "profile-1", "user_id": user_id, "profile_name": profile_name}
        )
        management.resolve_user_profile_provider = lambda user_id, **_kwargs: Resolution()

        def single_profile_sync(**kwargs):
            calls.append(kwargs)
            return {
                "ok": True,
                "status": "synced",
                "request_id": "single-profile-request",
                "mode": sync.SYNC_MODE_ACTIVE_PROVIDER,
                "profiles": [{"profile": kwargs.get("profile_name"), "status": "synced"}],
            }

        management.sync_single_profile_model_config = single_profile_sync
        result = management.sync_user_ai_provider_profile_payload("u1", {"profile_name": "p1"})
        assert result["sync"]["status"] == "synced"
        assert calls[0]["user_id"] == "u1"
        assert calls[0]["profile_name"] == "p1"
        assert calls[0]["provider"]["id"] == "provider-a"
        assert "profile_home" not in calls[0]
    finally:
        management.resolve_user_profile_provider = original_resolver
        management.get_user_profile_record_by_name = original_get_profile
        management.sync_single_profile_model_config = original_sync


def validate_single_profile_ownership_failure_blocks_sync() -> None:
    original_get_profile = management.get_user_profile_record_by_name
    original_sync = management.sync_single_profile_model_config
    sync_calls = []

    def get_profile(user_id, profile):
        if user_id != "u1" or profile != "p1":
            raise management.UserProviderAuthError(
                "Profile is not available for current user",
                status=403,
                code="profile_forbidden",
            )
        return {"id": "profile-1", "user_id": user_id, "profile_name": profile}

    try:
        management.get_user_profile_record_by_name = get_profile
        management.sync_single_profile_model_config = lambda **kwargs: sync_calls.append(kwargs)
        for profile_name in ("other-user-profile", "missing-profile"):
            try:
                management.sync_user_ai_provider_profile_payload("u1", {"profile_name": profile_name})
            except management.UserProviderAuthError as exc:
                assert exc.code == "profile_forbidden"
                assert exc.status == 403
            else:
                raise AssertionError("profile ownership failure was expected")
        assert sync_calls == []
    finally:
        management.get_user_profile_record_by_name = original_get_profile
        management.sync_single_profile_model_config = original_sync


def validate_single_profile_lookup_failure_is_observable() -> None:
    original_resolver = management.resolve_user_profile_provider
    original_get_profile = management.get_user_profile_record_by_name
    original_sync = management.sync_single_profile_model_config
    sync_calls = []

    class Resolution:
        is_active = False
        status = "lookup_failed"
        reason = "nocobase_lookup_failed"
        provider = None

    try:
        management.get_user_profile_record_by_name = (
            lambda user_id, profile_name: {"id": "profile-1", "user_id": user_id, "profile_name": profile_name}
        )
        management.resolve_user_profile_provider = lambda user_id, **_kwargs: Resolution()
        management.sync_single_profile_model_config = lambda **kwargs: sync_calls.append(kwargs)
        try:
            management.sync_user_ai_provider_profile_payload("u1", {"profile_name": "p1"})
        except sync.UserProviderConfigSyncError as exc:
            assert exc.status == 503
            assert exc.code == "nocobase_lookup_failed"
            assert exc.payload["provider_resolution"]["status"] == "lookup_failed"
        else:
            raise AssertionError("lookup_failed should be observable failure")
        assert sync_calls == []
    finally:
        management.resolve_user_profile_provider = original_resolver
        management.get_user_profile_record_by_name = original_get_profile
        management.sync_single_profile_model_config = original_sync


def validate_single_profile_no_active_provider_skips_without_write() -> None:
    original_resolver = management.resolve_user_profile_provider
    original_get_profile = management.get_user_profile_record_by_name
    original_sync = management.sync_single_profile_model_config
    sync_calls = []

    class Resolution:
        is_active = False
        status = "disabled"
        reason = "no_provider"
        provider = None

    try:
        management.get_user_profile_record_by_name = (
            lambda user_id, profile_name: {"id": "profile-1", "user_id": user_id, "profile_name": profile_name}
        )
        management.resolve_user_profile_provider = lambda user_id, **_kwargs: Resolution()
        management.sync_single_profile_model_config = lambda **kwargs: sync_calls.append(kwargs)
        result = management.sync_user_ai_provider_profile_payload("u1", {"profile_name": "p1"})
        assert result["ok"] is True
        assert result["sync"]["status"] == "skipped"
        assert result["sync"]["reason"] == "no_provider"
        assert sync_calls == []
    finally:
        management.resolve_user_profile_provider = original_resolver
        management.get_user_profile_record_by_name = original_get_profile
        management.sync_single_profile_model_config = original_sync


def main() -> None:
    validate_active_provider_sync_and_redaction()
    validate_root_default_exact_delete()
    validate_partial_failure_rolls_back_and_redacts()
    validate_management_enable_updates_single_profile_and_uses_global_provider()
    validate_management_enable_rolls_back_user_selection_on_sync_failure()
    validate_management_enable_rejects_foreign_profile_id()
    validate_single_profile_payload_uses_active_provider()
    validate_single_profile_ownership_failure_blocks_sync()
    validate_single_profile_lookup_failure_is_observable()
    validate_single_profile_no_active_provider_skips_without_write()
    print("user provider config sync validation passed")


if __name__ == "__main__":
    main()
