from pathlib import Path

import pytest
import yaml

import api.internal_provider_sync as internal_sync
import api.profiles as profiles
import api.user_provider as user_provider


class _DummyHandler:
    def __init__(self, authorization: str = "") -> None:
        self.headers = {}
        if authorization:
            self.headers["Authorization"] = authorization


def _write_yaml(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_verify_internal_provider_sync_token_requires_matching_bearer(monkeypatch):
    monkeypatch.setenv(internal_sync.INTERNAL_PROVIDER_SYNC_TOKEN_ENV, "fixed-token")

    with pytest.raises(internal_sync.InternalProviderSyncError) as missing_exc:
        internal_sync.verify_internal_provider_sync_token(_DummyHandler())
    assert missing_exc.value.status == 401
    assert missing_exc.value.code == "internal_provider_sync_token_invalid"

    with pytest.raises(internal_sync.InternalProviderSyncError) as invalid_exc:
        internal_sync.verify_internal_provider_sync_token(_DummyHandler("Bearer wrong-token"))
    assert invalid_exc.value.status == 401
    assert invalid_exc.value.code == "internal_provider_sync_token_invalid"

    internal_sync.verify_internal_provider_sync_token(_DummyHandler("Bearer fixed-token"))


def test_sync_internal_provider_root_profiles_payload_supports_dry_run(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    profiles_root = hermes_home / "profiles"
    _write_yaml(hermes_home / "config.yaml", {"model": {"default": "root-old"}, "custom": "keep-root"})
    _write_yaml(profiles_root / "alpha" / "config.yaml", {"model": {"default": "alpha-old"}, "alpha": True})

    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)
    monkeypatch.setattr(internal_sync, "_profiles_root", lambda: profiles_root)
    monkeypatch.setattr(user_provider, "_validate_provider_host", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        internal_sync,
        "list_global_ai_provider_records_for_service",
        lambda: [
            {
                "id": "provider-1",
                "name": "Provider One",
                "base_url": "https://api.anthropic.com/v1/messages",
                "model_name": "claude-sonnet-4-20250514",
                "api_mode": "anthropic_messages",
                "api_key": "secret-key",
                "is_enable": True,
            }
        ],
    )

    payload = internal_sync.sync_internal_provider_root_profiles_payload(
        {"provider_id": "provider-1", "dry_run": True}
    )

    assert payload["ok"] is True
    assert payload["profile_names"] == ["default", "alpha"]
    assert payload["sync"]["status"] == "dry_run"
    assert _read_yaml(hermes_home / "config.yaml")["model"]["default"] == "root-old"
    assert _read_yaml(profiles_root / "alpha" / "config.yaml")["model"]["default"] == "alpha-old"


def test_sync_internal_provider_root_profiles_payload_updates_root_and_named_profiles(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    profiles_root = hermes_home / "profiles"
    _write_yaml(hermes_home / "config.yaml", {"model": {"default": "root-old"}, "custom": "keep-root"})
    _write_yaml(profiles_root / "alpha" / "config.yaml", {"model": {"default": "alpha-old"}, "alpha": True})
    _write_yaml(profiles_root / "zeta" / "config.yaml", {"other": 1})
    _write_yaml(profiles_root / "UPPER" / "config.yaml", {"model": {"default": "skip-me"}})

    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)
    monkeypatch.setattr(internal_sync, "_profiles_root", lambda: profiles_root)
    monkeypatch.setattr(user_provider, "_validate_provider_host", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        internal_sync,
        "list_global_ai_provider_records_for_service",
        lambda: [
            {
                "id": "provider-2",
                "name": "Provider Two",
                "base_url": "https://api.openai.com/v1/responses",
                "model_name": "gpt-4.1",
                "api_mode": "codex_responses",
                "api_key": "secret-key-2",
                "is_enable": True,
            }
        ],
    )

    payload = internal_sync.sync_internal_provider_root_profiles_payload({"provider_id": "provider-2"})

    assert payload["ok"] is True
    assert payload["profile_names"] == ["default", "alpha", "zeta"]
    assert payload["sync"]["status"] == "synced"

    root_config = _read_yaml(hermes_home / "config.yaml")
    alpha_config = _read_yaml(profiles_root / "alpha" / "config.yaml")
    zeta_config = _read_yaml(profiles_root / "zeta" / "config.yaml")
    skipped_config = _read_yaml(profiles_root / "UPPER" / "config.yaml")

    for config in (root_config, alpha_config, zeta_config):
        assert config["model"]["default"] == "gpt-4.1"
        assert config["model"]["provider"] == "custom"
        assert config["model"]["base_url"] == "https://api.openai.com/v1"
        assert config["model"]["api_key"] == "secret-key-2"
        assert config["model"]["api_mode"] == "codex_responses"

    assert root_config["custom"] == "keep-root"
    assert alpha_config["alpha"] is True
    assert zeta_config["other"] == 1
    assert skipped_config["model"]["default"] == "skip-me"
