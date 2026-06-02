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


def validate_management_enable_rolls_back_status_on_sync_failure() -> None:
    records = {
        "old": {
            "id": "old",
            "user_id": "u1",
            "name": "Old",
            "provider_slug": "old",
            "base_url": "https://example.com/v1",
            "model_name": "old-model",
            "api_mode": "anthropic_messages",
            "api_key": "fake-old-secret",
            "status": "enabled",
        },
        "target": {
            "id": "target",
            "user_id": "u1",
            "name": "Target",
            "provider_slug": "target",
            "base_url": "https://example.com/v1",
            "model_name": "target-model",
            "api_mode": "anthropic_messages",
            "api_key": "fake-target-secret",
            "status": "disabled",
        },
    }
    original_list = management.list_user_ai_provider_records
    original_update = management.update_user_ai_provider_record
    original_test = management.test_user_provider_connection
    original_sync = management.sync_user_provider_model_config
    original_host_validator = provider_runtime._validate_provider_host
    try:
        management.list_user_ai_provider_records = lambda user_id: [
            copy.deepcopy(records["old"]),
            copy.deepcopy(records["target"]),
        ]

        def update_record(user_id, provider_id, body, *, partial=True):
            records[provider_id].update(body)
            return copy.deepcopy(records[provider_id])

        def sync_failure(**kwargs):
            if kwargs.get("dry_run"):
                return {"ok": True, "status": "dry_run", "profiles": []}
            raise sync.UserProviderConfigSyncError(
                "sync failed",
                code="sync_failed",
                payload={"status": "sync_failed", "error": "redacted"},
            )

        def fail_if_enable_requires_provider_test(user_id, provider):
            return {"ok": False, "error": "test_blocked", "message": "test should not block enable"}

        management.update_user_ai_provider_record = update_record
        management.test_user_provider_connection = fail_if_enable_requires_provider_test
        management.sync_user_provider_model_config = sync_failure
        provider_runtime._validate_provider_host = lambda hostname, port: None
        try:
            management.enable_user_ai_provider_payload("u1", "target")
        except sync.UserProviderConfigSyncError as exc:
            assert exc.code == "sync_failed"
            pass
        else:
            raise AssertionError("enable should fail when sync fails")
        assert records["old"]["status"] == "enabled"
        assert records["target"]["status"] == "disabled"
    finally:
        management.list_user_ai_provider_records = original_list
        management.update_user_ai_provider_record = original_update
        management.test_user_provider_connection = original_test
        management.sync_user_provider_model_config = original_sync
        provider_runtime._validate_provider_host = original_host_validator


def validate_single_profile_payload_uses_active_provider() -> None:
    original_resolver = management.resolve_user_provider
    original_sync = management.sync_single_profile_model_config
    original_verify = management.verify_user_profile_access
    calls = []
    verified = []

    class Resolution:
        is_active = True
        status = "enabled"
        reason = ""
        provider = _provider()

    try:
        management.resolve_user_provider = lambda user_id: Resolution()
        management.verify_user_profile_access = lambda user_id, profile: verified.append((user_id, profile))

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
        assert verified == [("u1", "p1")]
        assert calls[0]["user_id"] == "u1"
        assert calls[0]["profile_name"] == "p1"
        assert calls[0]["provider"]["id"] == "provider-a"
        assert "profile_home" not in calls[0]
    finally:
        management.resolve_user_provider = original_resolver
        management.sync_single_profile_model_config = original_sync
        management.verify_user_profile_access = original_verify


def validate_single_profile_ownership_failure_blocks_sync() -> None:
    original_resolver = management.resolve_user_provider
    original_sync = management.sync_single_profile_model_config
    original_verify = management.verify_user_profile_access
    sync_calls = []

    class Resolution:
        is_active = True
        status = "active"
        reason = "active_provider"
        provider = _provider()

    def verify_access(user_id, profile):
        if user_id != "u1" or profile != "p1":
            raise management.UserProviderAuthError(
                "Profile is not available for current user",
                status=403,
                code="profile_forbidden",
            )

    try:
        management.resolve_user_provider = lambda user_id: Resolution()
        management.verify_user_profile_access = verify_access
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
        management.resolve_user_provider = original_resolver
        management.sync_single_profile_model_config = original_sync
        management.verify_user_profile_access = original_verify


def validate_single_profile_lookup_failure_is_observable() -> None:
    original_resolver = management.resolve_user_provider
    original_sync = management.sync_single_profile_model_config
    original_verify = management.verify_user_profile_access
    sync_calls = []

    class Resolution:
        is_active = False
        status = "lookup_failed"
        reason = "nocobase_lookup_failed"
        provider = None

    try:
        management.resolve_user_provider = lambda user_id: Resolution()
        management.verify_user_profile_access = lambda user_id, profile: None
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
        management.resolve_user_provider = original_resolver
        management.sync_single_profile_model_config = original_sync
        management.verify_user_profile_access = original_verify


def validate_single_profile_no_active_provider_skips_without_write() -> None:
    original_resolver = management.resolve_user_provider
    original_sync = management.sync_single_profile_model_config
    original_verify = management.verify_user_profile_access
    sync_calls = []

    class Resolution:
        is_active = False
        status = "disabled"
        reason = "no_enabled_provider"
        provider = None

    try:
        management.resolve_user_provider = lambda user_id: Resolution()
        management.verify_user_profile_access = lambda user_id, profile: None
        management.sync_single_profile_model_config = lambda **kwargs: sync_calls.append(kwargs)
        result = management.sync_user_ai_provider_profile_payload("u1", {"profile_name": "p1"})
        assert result["ok"] is True
        assert result["sync"]["status"] == "skipped"
        assert result["sync"]["reason"] == "no_enabled_provider"
        assert sync_calls == []
    finally:
        management.resolve_user_provider = original_resolver
        management.sync_single_profile_model_config = original_sync
        management.verify_user_profile_access = original_verify


def main() -> None:
    validate_active_provider_sync_and_redaction()
    validate_root_default_exact_delete()
    validate_partial_failure_rolls_back_and_redacts()
    validate_management_enable_rolls_back_status_on_sync_failure()
    validate_single_profile_payload_uses_active_provider()
    validate_single_profile_ownership_failure_blocks_sync()
    validate_single_profile_lookup_failure_is_observable()
    validate_single_profile_no_active_provider_skips_without_write()
    print("user provider config sync validation passed")


if __name__ == "__main__":
    main()
