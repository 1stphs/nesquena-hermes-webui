from pathlib import Path

import pytest
import yaml

import api.internal_provider_sync as internal_sync
import api.profiles as profiles
import api.user_provider as user_provider
import api.user_provider_config_sync as user_provider_config_sync


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


def _prepare_internal_sync_home(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    hermes_home = tmp_path / ".hermes"
    profiles_root = hermes_home / "profiles"
    _write_yaml(hermes_home / "config.yaml", {"model": {"default": "root-old"}, "custom": "keep-root"})

    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)
    monkeypatch.setattr(internal_sync, "_profiles_root", lambda: profiles_root)
    return hermes_home, profiles_root


def _patch_internal_provider(monkeypatch, *, provider_id: str = "provider-template", api_key: str = "secret-template") -> None:
    monkeypatch.setattr(user_provider, "_validate_provider_host", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        internal_sync,
        "list_global_ai_provider_records_for_service",
        lambda: [
            {
                "id": provider_id,
                "name": "Template Provider",
                "base_url": "https://api.openai.com/v1/responses",
                "model_name": "gpt-4.1",
                "api_mode": "codex_responses",
                "api_key": api_key,
                "is_enable": True,
            }
        ],
    )


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
        {"provider_id": "provider-1", "dry_run": True, "include_talent_market_templates": False}
    )

    assert payload["ok"] is True
    assert payload["profile_names"] == ["default", "alpha"]
    assert payload["sync"]["status"] == "dry_run"
    assert _read_yaml(hermes_home / "config.yaml")["model"]["default"] == "root-old"
    assert _read_yaml(profiles_root / "alpha" / "config.yaml")["model"]["default"] == "alpha-old"


def test_sync_internal_provider_root_profiles_payload_default_requires_talent_market_root(tmp_path, monkeypatch):
    hermes_home, _profiles_root = _prepare_internal_sync_home(tmp_path, monkeypatch)
    missing_talent_root = tmp_path / "missing_talent_market"

    monkeypatch.setenv("HERMES_TALENT_MARKET_DIR", str(missing_talent_root))
    _patch_internal_provider(monkeypatch, provider_id="provider-template-missing-root")

    with pytest.raises(internal_sync.InternalProviderSyncError) as exc:
        internal_sync.sync_internal_provider_root_profiles_payload({"provider_id": "provider-template-missing-root"})

    assert exc.value.status == 503
    assert exc.value.code == "talent_market_root_unavailable"
    assert _read_yaml(hermes_home / "config.yaml")["model"]["default"] == "root-old"


def test_sync_internal_provider_root_profiles_payload_default_updates_talent_market(tmp_path, monkeypatch):
    hermes_home, _profiles_root = _prepare_internal_sync_home(tmp_path, monkeypatch)
    talent_root = tmp_path / "talent_market"
    _write_yaml(talent_root / "assistant" / "config.yaml", {"model": {"default": "template-old"}})

    monkeypatch.setenv("HERMES_TALENT_MARKET_DIR", str(talent_root))
    _patch_internal_provider(monkeypatch, provider_id="provider-template-default")

    payload = internal_sync.sync_internal_provider_root_profiles_payload({"provider_id": "provider-template-default"})

    assert payload["ok"] is True
    assert payload["talent_market_templates"]["status"] == "synced"
    assert payload["talent_market_templates"]["targets"][0]["target"] == "talent_market:assistant"
    assert _read_yaml(hermes_home / "config.yaml")["model"]["default"] == "gpt-4.1"
    assert _read_yaml(talent_root / "assistant" / "config.yaml")["model"]["default"] == "gpt-4.1"


def test_sync_internal_provider_root_profiles_payload_talent_market_dry_run_plans_only(tmp_path, monkeypatch):
    hermes_home, _profiles_root = _prepare_internal_sync_home(tmp_path, monkeypatch)
    talent_root = tmp_path / "talent_market"
    template_config = talent_root / "assistant" / "config.yaml"
    _write_yaml(template_config, {"model": {"default": "template-old"}, "template": True})

    monkeypatch.setenv("HERMES_TALENT_MARKET_DIR", str(talent_root))
    _patch_internal_provider(monkeypatch, provider_id="provider-template-dry-run")

    payload = internal_sync.sync_internal_provider_root_profiles_payload(
        {
            "provider_id": "provider-template-dry-run",
            "dry_run": True,
            "include_talent_market_templates": True,
        }
    )

    assert payload["ok"] is True
    assert payload["sync"]["status"] == "dry_run"
    assert payload["talent_market_templates"]["status"] == "dry_run"
    assert payload["talent_market_templates"]["targets"][0]["target"] == "talent_market:assistant"
    assert payload["talent_market_templates"]["targets"][0]["status"] == "planned"
    assert "secret-template" not in str(payload)
    assert _read_yaml(hermes_home / "config.yaml")["model"]["default"] == "root-old"
    assert _read_yaml(template_config)["model"]["default"] == "template-old"


def test_sync_internal_provider_root_profiles_payload_talent_market_updates_templates(tmp_path, monkeypatch):
    _hermes_home, _profiles_root = _prepare_internal_sync_home(tmp_path, monkeypatch)
    talent_root = tmp_path / "talent_market"
    template_config = talent_root / "assistant" / "config.yaml"
    _write_yaml(template_config, {"model": {"default": "template-old"}, "template": True})

    monkeypatch.setenv("HERMES_TALENT_MARKET_DIR", str(talent_root))
    _patch_internal_provider(monkeypatch, provider_id="provider-template-write")

    payload = internal_sync.sync_internal_provider_root_profiles_payload(
        {"provider_id": "provider-template-write", "include_talent_market_templates": True}
    )

    assert payload["ok"] is True
    assert payload["talent_market_templates"]["status"] == "synced"
    assert payload["talent_market_templates"]["targets"][0]["target"] == "talent_market:assistant"

    template = _read_yaml(template_config)
    assert template["template"] is True
    assert template["model"]["default"] == "gpt-4.1"
    assert template["model"]["provider"] == "custom"
    assert template["model"]["base_url"] == "https://api.openai.com/v1"
    assert template["model"]["api_key"] == "secret-template"
    assert template["model"]["api_mode"] == "codex_responses"


def test_sync_internal_provider_root_profiles_payload_talent_market_skips_symlink_escape(tmp_path, monkeypatch):
    _hermes_home, _profiles_root = _prepare_internal_sync_home(tmp_path, monkeypatch)
    talent_root = tmp_path / "talent_market"
    escape_dir = talent_root / "escaped"
    outside_config = tmp_path / "outside" / "config.yaml"
    _write_yaml(outside_config, {"model": {"default": "outside-old"}})
    escape_dir.mkdir(parents=True, exist_ok=True)
    try:
        (escape_dir / "config.yaml").symlink_to(outside_config)
    except OSError as exc:
        pytest.skip(f"symlink unavailable: {exc}")

    monkeypatch.setenv("HERMES_TALENT_MARKET_DIR", str(talent_root))
    _patch_internal_provider(monkeypatch, provider_id="provider-template-symlink")

    payload = internal_sync.sync_internal_provider_root_profiles_payload(
        {"provider_id": "provider-template-symlink", "include_talent_market_templates": True}
    )

    assert payload["ok"] is True
    assert payload["talent_market_templates"]["targets"] == []
    assert payload["talent_market_templates"]["skipped"] == [
        {"target": "talent_market:escaped", "status": "skipped", "reason": "path_escape"}
    ]
    assert _read_yaml(outside_config)["model"]["default"] == "outside-old"


def test_sync_internal_provider_root_profiles_payload_talent_market_invalid_yaml_does_not_mutate_runtime_profiles(
    tmp_path,
    monkeypatch,
):
    hermes_home, profiles_root = _prepare_internal_sync_home(tmp_path, monkeypatch)
    _write_yaml(profiles_root / "alpha" / "config.yaml", {"model": {"default": "alpha-old"}, "alpha": True})
    talent_root = tmp_path / "talent_market"
    bad_config = talent_root / "assistant" / "config.yaml"
    bad_config.parent.mkdir(parents=True, exist_ok=True)
    bad_config.write_text("- invalid-yaml-object\n", encoding="utf-8")

    monkeypatch.setenv("HERMES_TALENT_MARKET_DIR", str(talent_root))
    _patch_internal_provider(monkeypatch, provider_id="provider-template-invalid-yaml")

    with pytest.raises(user_provider_config_sync.UserProviderConfigSyncError) as exc:
        internal_sync.sync_internal_provider_root_profiles_payload(
            {"provider_id": "provider-template-invalid-yaml", "include_talent_market_templates": True}
        )

    assert exc.value.code == "invalid_profile_config"
    assert exc.value.payload["sync"]["ok"] is False
    assert exc.value.payload["sync"]["status"] == "sync_failed"
    assert exc.value.payload["talent_market_templates"]["ok"] is False
    assert exc.value.payload["talent_market_templates"]["status"] == "sync_failed"
    assert _read_yaml(hermes_home / "config.yaml")["model"]["default"] == "root-old"
    assert _read_yaml(profiles_root / "alpha" / "config.yaml")["model"]["default"] == "alpha-old"


def test_sync_internal_provider_root_profiles_payload_talent_market_rechecks_root_before_write(
    tmp_path,
    monkeypatch,
):
    hermes_home, profiles_root = _prepare_internal_sync_home(tmp_path, monkeypatch)
    _write_yaml(profiles_root / "alpha" / "config.yaml", {"model": {"default": "alpha-old"}, "alpha": True})
    talent_root = tmp_path / "talent_market"
    template_config = talent_root / "assistant" / "config.yaml"
    _write_yaml(template_config, {"model": {"default": "template-old"}, "template": True})
    outside_config = tmp_path / "outside" / "config.yaml"
    _write_yaml(outside_config, {"model": {"default": "outside-old"}})

    monkeypatch.setenv("HERMES_TALENT_MARKET_DIR", str(talent_root))
    _patch_internal_provider(monkeypatch, provider_id="provider-template-race")

    original_write_profile_plan = user_provider_config_sync._write_profile_plan
    state = {"swapped": False}

    def _write_with_symlink_swap(item: dict):
        if item.get("label_key") == "target" and not state["swapped"]:
            state["swapped"] = True
            target_path = Path(item["config_path"])
            if target_path.exists():
                target_path.unlink()
            target_path.symlink_to(outside_config)
        return original_write_profile_plan(item)

    monkeypatch.setattr(user_provider_config_sync, "_write_profile_plan", _write_with_symlink_swap)

    with pytest.raises(user_provider_config_sync.UserProviderConfigSyncError) as exc:
        internal_sync.sync_internal_provider_root_profiles_payload(
            {"provider_id": "provider-template-race", "include_talent_market_templates": True}
        )

    assert exc.value.code == "sync_failed"
    assert exc.value.payload["talent_market_templates"]["status"] == "sync_failed"
    assert _read_yaml(hermes_home / "config.yaml")["model"]["default"] == "root-old"
    assert _read_yaml(profiles_root / "alpha" / "config.yaml")["model"]["default"] == "alpha-old"
    assert _read_yaml(outside_config)["model"]["default"] == "outside-old"


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

    payload = internal_sync.sync_internal_provider_root_profiles_payload(
        {"provider_id": "provider-2", "include_talent_market_templates": False}
    )

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
