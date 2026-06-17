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


def test_copy_cloned_profile_skills_preserves_template_skill_entries(tmp_path, monkeypatch):
    import api.profiles as profiles

    hermes_home = tmp_path / ".hermes"
    builtin_skill = tmp_path / "builtin" / "builtin-skill"
    builtin_skill.mkdir(parents=True)
    (builtin_skill / "SKILL.md").write_text(
        "---\nname: builtin-skill\ndescription: Built in\n---\n",
        encoding="utf-8",
    )
    template_skills = hermes_home / "profiles" / "template_profile" / "skills"
    template_skills.mkdir(parents=True)
    (template_skills / "builtin-skill").symlink_to(builtin_skill)
    second_skill = template_skills / "second-skill"
    second_skill.mkdir()
    (second_skill / "SKILL.md").write_text(
        "---\nname: second-skill\ndescription: Another built in\n---\n",
        encoding="utf-8",
    )
    target_profile = hermes_home / "profiles" / "new-agent"
    (target_profile / "skills").mkdir(parents=True)
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)

    copied = profiles._copy_cloned_profile_skills("template_profile", target_profile)

    copied_skill_dir = target_profile / "skills" / "builtin-skill"
    copied_skill = copied_skill_dir / "SKILL.md"
    assert copied == 2
    assert copied_skill_dir.is_symlink()
    assert copied_skill.read_text(encoding="utf-8").startswith("---\nname: builtin-skill")
    assert (target_profile / "skills" / "second-skill" / "SKILL.md").is_file()
    assert profiles._count_profile_skill_dirs(target_profile) == 2


def test_create_profile_agent_clones_template_skills(monkeypatch):
    import api.profiles as profiles
    import api.routes_handlers.profile as profile_handler

    responses = []
    create_calls = []

    _patch_profile_handler_bindings(monkeypatch, profile_handler, responses)

    def fake_create_profile_api(name, **options):
        create_calls.append((name, options))
        return {"name": name, "path": "/tmp/new-agent"}

    monkeypatch.setattr(profiles, "create_profile_api", fake_create_profile_api)
    monkeypatch.setattr(profile_handler, "_write_profile_agent_files", lambda _path, _agent: {})

    result = profile_handler._handle_profile_agent_create(
        object(),
        {
            "display_name": "New Agent",
            "description": "desc",
            "prompt": "prompt",
        },
    )

    assert result is True
    assert responses[0][0] == 200
    assert create_calls[0] == (
        "new-agent",
        {
            "clone_from": "template_profile",
            "clone_config": True,
            "base_url": None,
            "api_key": None,
            "clone_skills": True,
        },
    )


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


@pytest.mark.parametrize(
    ("field", "max_length", "error_label"),
    [
        ("description", 2000, "description"),
        ("prompt", 5000, "prompt"),
    ],
)
def test_update_profile_agent_accepts_description_and_prompt_length_limits(
    tmp_path,
    monkeypatch,
    field,
    max_length,
    error_label,
):
    import api.routes_handlers.profile as profile_handler

    profile_path = tmp_path / "profiles" / "assistant"
    profile_path.mkdir(parents=True)
    _write_agent_metadata(
        profile_path,
        {
            "profile_id": "assistant",
            "profile_name": "旧助理",
            "avatar": "old.png",
            "description": "旧简介",
            "prompt": "旧 Prompt",
            "skills": [],
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

    body = {
        "profile_id": "assistant",
        "display_name": "新助理",
        "description": "新简介",
        "prompt": "新 Prompt",
    }
    body[field] = "a" * max_length

    result = profile_handler._handle_profile_agent_update(object(), body)

    assert result is True
    assert responses[0][0] == 200
    assert len(responses[0][1]["agent"][error_label]) == max_length


@pytest.mark.parametrize(
    ("field", "max_length", "error_label"),
    [
        ("description", 2000, "description"),
        ("prompt", 5000, "prompt"),
    ],
)
def test_update_profile_agent_rejects_description_and_prompt_over_length_limits(
    tmp_path,
    monkeypatch,
    field,
    max_length,
    error_label,
):
    import api.routes_handlers.profile as profile_handler

    profile_path = tmp_path / "profiles" / "assistant"
    profile_path.mkdir(parents=True)
    _write_agent_metadata(
        profile_path,
        {
            "profile_id": "assistant",
            "profile_name": "旧助理",
            "avatar": "old.png",
            "description": "旧简介",
            "prompt": "旧 Prompt",
            "skills": [],
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

    body = {
        "profile_id": "assistant",
        "display_name": "新助理",
        "description": "新简介",
        "prompt": "新 Prompt",
    }
    body[field] = "a" * (max_length + 1)

    result = profile_handler._handle_profile_agent_update(object(), body)

    assert result is True
    assert responses[0][0] == 400
    assert responses[0][1]["error"] == f"{error_label} must be at most {max_length} characters"
