import json

import pytest

from api.profiles import _validate_profile_name


def _patch_profile_handler_bindings(monkeypatch, profile_handler, responses, *, catalog=None):
    def fake_routes_binding(name):
        if name == "j":
            return lambda _handler, payload, status=200, **_kwargs: responses.append((status, payload)) or True
        if name == "bad":
            return lambda _handler, msg, status=400: responses.append((status, {"error": msg})) or True
        if name == "_sanitize_error":
            return str
        if name == "_load_profile_agent_skills_catalog":
            return lambda: list(catalog or [])
        raise AttributeError(name)

    monkeypatch.setattr(profile_handler, "_routes_binding", fake_routes_binding)


def _write_agent_metadata(profile_path, payload):
    metadata_path = profile_path / "webui" / "agent.json"
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return metadata_path


def test_webui_profile_name_accepts_150_characters():
    _validate_profile_name("a" * 150)


def test_webui_profile_name_rejects_151_characters():
    with pytest.raises(ValueError):
        _validate_profile_name("a" * 151)


def test_install_profiles_accepts_150_character_profile_name(tmp_path, monkeypatch):
    import api.profiles as profiles
    import api.routes_handlers.profile as profile_handler

    long_name = "a" * 150
    talent_root = tmp_path / "talent"
    source_dir = talent_root / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "SOUL.md").write_text("profile", encoding="utf-8")
    profiles_root = tmp_path / ".hermes" / "profiles"
    profiles_root.mkdir(parents=True)

    monkeypatch.setattr(profile_handler, "_talent_market_profiles_root", lambda: talent_root)
    monkeypatch.setattr(profiles, "_profiles_root", lambda: profiles_root)

    responses = []

    def fake_routes_binding(name):
        if name == "j":
            return lambda _handler, payload, status=200, **_kwargs: responses.append((status, payload)) or True
        if name == "bad":
            return lambda _handler, msg, status=400: responses.append((status, {"error": msg})) or True
        if name == "_sanitize_error":
            return str
        raise AttributeError(name)

    monkeypatch.setattr(profile_handler, "_routes_binding", fake_routes_binding)

    result = profile_handler._handle_profile_install_profiles(
        object(),
        {"profile_name": long_name, "source_path": str(source_dir)},
    )

    assert result is True
    assert responses[0][0] == 200
    assert responses[0][1]["profile"]["name"] == long_name
    assert (profiles_root / long_name / "SOUL.md").read_text(encoding="utf-8") == "profile"


def test_update_profile_agent_preserves_skills_when_omitted(tmp_path, monkeypatch):
    import api.routes_handlers.profile as profile_handler

    profile_path = tmp_path / "profiles" / "assistant"
    profile_path.mkdir(parents=True)
    metadata_path = _write_agent_metadata(
        profile_path,
        {
            "profile_id": "assistant",
            "profile_name": "旧助理",
            "avatar": "old.png",
            "description": "旧简介",
            "prompt": "旧 Prompt",
            "skills": ["web-search", "doc-summary"],
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )
    responses = []

    _patch_profile_handler_bindings(monkeypatch, profile_handler, responses)
    monkeypatch.setattr(
        profile_handler,
        "_resolve_profile_agent_update_target",
        lambda _body: ("assistant", profile_path),
    )

    result = profile_handler._handle_profile_agent_update(
        object(),
        {
            "profile_id": "assistant",
            "display_name": "新助理",
            "description": "新简介",
            "prompt": "新 Prompt",
            "avatar": "new.png",
        },
    )

    assert result is True
    assert responses[0][0] == 200
    assert responses[0][1]["agent"]["skills"] == ["web-search", "doc-summary"]

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["profile_name"] == "新助理"
    assert metadata["description"] == "新简介"
    assert metadata["prompt"] == "新 Prompt"
    assert metadata["skills"] == ["web-search", "doc-summary"]
    assert (profile_path / "SOUL.md").read_text(encoding="utf-8") == "新 Prompt\n"


def test_update_profile_agent_allows_explicit_empty_skills(tmp_path, monkeypatch):
    import api.routes_handlers.profile as profile_handler

    profile_path = tmp_path / "profiles" / "assistant"
    profile_path.mkdir(parents=True)
    metadata_path = _write_agent_metadata(
        profile_path,
        {
            "profile_id": "assistant",
            "profile_name": "旧助理",
            "avatar": "old.png",
            "description": "旧简介",
            "prompt": "旧 Prompt",
            "skills": ["web-search"],
            "status": "active",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        },
    )
    responses = []

    _patch_profile_handler_bindings(monkeypatch, profile_handler, responses)
    monkeypatch.setattr(
        profile_handler,
        "_resolve_profile_agent_update_target",
        lambda _body: ("assistant", profile_path),
    )

    result = profile_handler._handle_profile_agent_update(
        object(),
        {
            "profile_id": "assistant",
            "display_name": "新助理",
            "description": "新简介",
            "prompt": "新 Prompt",
            "skills": [],
        },
    )

    assert result is True
    assert responses[0][0] == 200
    assert responses[0][1]["agent"]["skills"] == []

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["skills"] == []
