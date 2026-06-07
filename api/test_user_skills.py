from types import SimpleNamespace

import pytest


def _patch_skill_handler_bindings(monkeypatch, skill_handler, responses):
    def fake_routes_binding(name):
        if name == "j":
            def fake_json(_handler, payload, status=200, **_kwargs):
                responses.append((status, payload))
                return True

            return fake_json
        if name == "bad":
            def fake_bad(_handler, msg, status=400):
                responses.append((status, {"error": msg}))
                return True

            return fake_bad
        if name == "_sanitize_error":
            return str
        raise AttributeError(name)

    monkeypatch.setattr(skill_handler, "_routes_binding", fake_routes_binding)


def _patch_user_root(monkeypatch, skill_handler, root):
    monkeypatch.setattr(skill_handler, "USER_SKILLS_ROOT", root)


def _patch_profile_access(monkeypatch, *, home, user_id="user-1", forbidden=False, calls=None):
    import api.profiles as profiles
    import api.user_provider as user_provider

    monkeypatch.setattr(user_provider, "current_user_id_from_handler", lambda _handler: user_id)

    def fake_verify_user_profile_access(next_user_id, profile):
        if calls is not None:
            calls.append((next_user_id, profile))
        if forbidden:
            raise user_provider.UserProviderAuthError(
                "Profile is not available for current user",
                status=403,
                code="profile_forbidden",
            )

    monkeypatch.setattr(user_provider, "verify_user_profile_access", fake_verify_user_profile_access)
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda _profile: home)


def _write_skill(skill_dir, content=None):
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        content
        or """---
name: 原始助手
description: 原始简介
---

# 原始助手

正文第一段。
""",
        encoding="utf-8",
    )


def _response(responses):
    assert responses
    return responses[-1]


def test_user_skills_list_reads_frontmatter_name_and_summary(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    my_skills_dir = user_root / "user-1" / "my-skills"
    _write_skill(
        my_skills_dir / "mail-assistant",
        """---
name: 邮箱助手
description: 处理邮件草稿
---

# 邮箱助手
""",
    )
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")

    result = skill_handler._handle_user_skills_list(object(), SimpleNamespace(query=""))

    assert result is True
    assert _response(responses) == (
        200,
        {
            "skills": [
                {
                    "id": "mail-assistant",
                    "englishName": "mail-assistant",
                    "title": "mail-assistant",
                    "name": "邮箱助手",
                    "title_cn": "邮箱助手",
                    "summary": "处理邮件草稿",
                    "description": "处理邮件草稿",
                    "path": "mail-assistant",
                    "skill_file": "mail-assistant/SKILL.md",
                    "source": "user",
                }
            ],
            "count": 1,
        },
    )


def test_publish_from_profile_copies_skill_and_updates_name(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "default_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(profile_home / "skills" / "email-assistant")
    responses = []
    calls = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=profile_home, calls=calls)

    result = skill_handler._handle_user_skill_publish_from_profile(
        object(),
        {
            "profile_name": profile_name,
            "skill_slug": "email-assistant",
            "english_name": "mail-assistant",
            "name": "邮箱助手",
        },
    )

    destination = user_root / "user-1" / "my-skills" / "mail-assistant"
    assert result is True
    assert calls == [("user-1", profile_name)]
    assert destination.is_dir()
    assert "name: 邮箱助手" in (destination / "SKILL.md").read_text(encoding="utf-8")
    status, payload = _response(responses)
    assert status == 200
    assert payload["ok"] is True
    assert payload["skill"]["englishName"] == "mail-assistant"
    assert payload["skill"]["name"] == "邮箱助手"


def test_publish_from_profile_rejects_existing_english_name(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "default_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(profile_home / "skills" / "email-assistant")
    _write_skill(user_root / "user-1" / "my-skills" / "mail-assistant")
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=profile_home)

    result = skill_handler._handle_user_skill_publish_from_profile(
        object(),
        {
            "profile_name": profile_name,
            "skill_slug": "email-assistant",
            "english_name": "mail-assistant",
            "name": "邮箱助手",
        },
    )

    assert result is True
    assert _response(responses)[0] == 409
    assert _response(responses)[1]["code"] == "skill_conflict"


def test_publish_from_profile_rejects_destination_lock(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "default_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(profile_home / "skills" / "email-assistant")
    (user_root / "user-1" / "my-skills" / ".mail-assistant.lock").mkdir(parents=True)
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=profile_home)

    result = skill_handler._handle_user_skill_publish_from_profile(
        object(),
        {
            "profile_name": profile_name,
            "skill_slug": "email-assistant",
            "english_name": "mail-assistant",
            "name": "邮箱助手",
        },
    )

    assert result is True
    assert not (user_root / "user-1" / "my-skills" / "mail-assistant").exists()
    assert _response(responses)[0] == 409
    assert _response(responses)[1]["code"] == "skill_conflict"


def test_install_user_skill_to_profile_copies_without_nocobase_binding(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "target_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(user_root / "user-1" / "my-skills" / "mail-assistant")
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=profile_home)

    result = skill_handler._handle_user_skill_install_to_profile(
        object(),
        {
            "profile_name": profile_name,
            "skill_slug": "mail-assistant",
        },
    )

    destination = profile_home / "skills" / "mail-assistant"
    assert result is True
    assert destination.is_dir()
    assert _response(responses)[0] == 200
    assert _response(responses)[1]["profile"] == profile_name
    assert _response(responses)[1]["skill"]["id"] == "mail-assistant"


def test_install_user_skill_to_profile_rejects_same_folder_name(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "target_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(user_root / "user-1" / "my-skills" / "mail-assistant")
    _write_skill(profile_home / "skills" / "mail-assistant")
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=profile_home)

    result = skill_handler._handle_user_skill_install_to_profile(
        object(),
        {
            "profile_name": profile_name,
            "skill_slug": "mail-assistant",
        },
    )

    assert result is True
    assert _response(responses)[0] == 409
    assert _response(responses)[1]["code"] == "skill_conflict"


def test_install_user_skill_to_profile_rejects_destination_lock(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "target_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(user_root / "user-1" / "my-skills" / "mail-assistant")
    (profile_home / "skills" / ".mail-assistant.lock").mkdir(parents=True)
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=profile_home)

    result = skill_handler._handle_user_skill_install_to_profile(
        object(),
        {
            "profile_name": profile_name,
            "skill_slug": "mail-assistant",
        },
    )

    assert result is True
    assert not (profile_home / "skills" / "mail-assistant").exists()
    assert _response(responses)[0] == 409
    assert _response(responses)[1]["code"] == "skill_conflict"


def test_update_user_skill_renames_folder_and_updates_name(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    source = user_root / "user-1" / "my-skills" / "mail-assistant"
    _write_skill(source)
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")

    result = skill_handler._handle_user_skill_update(
        object(),
        {
            "skill_slug": "mail-assistant",
            "english_name": "new-mail-assistant",
            "name": "新邮箱助手",
        },
    )

    destination = user_root / "user-1" / "my-skills" / "new-mail-assistant"
    assert result is True
    assert not source.exists()
    assert destination.is_dir()
    assert "name: 新邮箱助手" in (destination / "SKILL.md").read_text(encoding="utf-8")
    assert _response(responses)[0] == 200
    assert _response(responses)[1]["skill"]["englishName"] == "new-mail-assistant"


def test_update_user_skill_rejects_english_name_conflict(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    _write_skill(user_root / "user-1" / "my-skills" / "mail-assistant")
    _write_skill(user_root / "user-1" / "my-skills" / "used-name")
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")

    result = skill_handler._handle_user_skill_update(
        object(),
        {
            "skill_slug": "mail-assistant",
            "english_name": "used-name",
            "name": "邮箱助手",
        },
    )

    assert result is True
    assert _response(responses)[0] == 409
    assert _response(responses)[1]["code"] == "skill_conflict"


def test_update_user_skill_rejects_destination_lock(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    source = user_root / "user-1" / "my-skills" / "mail-assistant"
    _write_skill(source)
    (user_root / "user-1" / "my-skills" / ".new-mail-assistant.lock").mkdir(parents=True)
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")

    result = skill_handler._handle_user_skill_update(
        object(),
        {
            "skill_slug": "mail-assistant",
            "english_name": "new-mail-assistant",
            "name": "邮箱助手",
        },
    )

    assert result is True
    assert source.exists()
    assert not (user_root / "user-1" / "my-skills" / "new-mail-assistant").exists()
    assert _response(responses)[0] == 409
    assert _response(responses)[1]["code"] == "skill_conflict"


def test_user_skill_rejects_path_traversal_before_profile_scan(tmp_path, monkeypatch):
    import api.profiles as profiles
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda _profile: (_ for _ in ()).throw(AssertionError("should not resolve profile")),
    )

    result = skill_handler._handle_user_skill_publish_from_profile(
        object(),
        {
            "profile_name": "default_367959913725953",
            "skill_slug": "../email-assistant",
            "english_name": "mail-assistant",
            "name": "邮箱助手",
        },
    )

    assert result is True
    assert _response(responses)[0] == 400
    assert _response(responses)[1]["code"] == "invalid_skill_slug"


def test_user_skill_rejects_forbidden_profile_without_copy(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "default_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(profile_home / "skills" / "email-assistant")
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=profile_home, forbidden=True)

    result = skill_handler._handle_user_skill_publish_from_profile(
        object(),
        {
            "profile_name": profile_name,
            "skill_slug": "email-assistant",
            "english_name": "mail-assistant",
            "name": "邮箱助手",
        },
    )

    assert result is True
    assert not (user_root / "user-1" / "my-skills" / "mail-assistant").exists()
    assert _response(responses)[0] == 403
    assert _response(responses)[1]["code"] == "profile_forbidden"


def test_user_skill_rejects_symlink_in_source(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "default_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    source = profile_home / "skills" / "email-assistant"
    _write_skill(source)
    try:
        (source / "linked.txt").symlink_to(source / "SKILL.md")
    except (NotImplementedError, OSError):
        pytest.skip("symlink is not available on this filesystem")
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=profile_home)

    result = skill_handler._handle_user_skill_publish_from_profile(
        object(),
        {
            "profile_name": profile_name,
            "skill_slug": "email-assistant",
            "english_name": "mail-assistant",
            "name": "邮箱助手",
        },
    )

    assert result is True
    assert _response(responses)[0] == 400
    assert _response(responses)[1]["code"] == "skill_source_symlink"
