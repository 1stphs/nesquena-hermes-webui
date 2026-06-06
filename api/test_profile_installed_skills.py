from types import SimpleNamespace


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


def _patch_profile_access(monkeypatch, *, home, calls=None):
    import api.profiles as profiles
    import api.user_provider as user_provider

    monkeypatch.setattr(user_provider, "current_user_id_from_handler", lambda _handler: "user-1")

    def fake_verify_user_profile_access(user_id, profile):
        if calls is not None:
            calls.append((user_id, profile))

    monkeypatch.setattr(
        user_provider,
        "verify_user_profile_access",
        fake_verify_user_profile_access,
    )
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda _profile: home)


def _write_skill(skill_dir, content):
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")


def test_profile_installed_skills_returns_direct_skill_summary(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "default_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    _write_skill(
        profile_home / "skills" / "test-skill",
        """---
name: runtime-test
title: Runtime Test
description: Runtime installed skill
---

# Runtime Test

Body text.
""",
    )
    responses = []
    calls = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_profile_access(monkeypatch, home=profile_home, calls=calls)

    result = skill_handler._handle_profile_installed_skills(
        object(),
        SimpleNamespace(query=f"profile={profile_name}"),
    )

    assert result is True
    assert calls == [("user-1", profile_name)]
    assert responses[0][0] == 200
    assert responses[0][1]["profile"] == profile_name
    assert responses[0][1]["skills_path"] == str(profile_home / "skills")
    assert responses[0][1]["count"] == 1
    assert responses[0][1]["skills"] == [
        {
            "id": "test-skill",
            "name": "runtime-test",
            "title": "Runtime Test",
            "description": "Runtime installed skill",
            "summary": "Runtime installed skill",
            "path": "test-skill",
            "skill_file": "test-skill/SKILL.md",
        }
    ]


def test_profile_installed_skills_missing_profile_returns_code(monkeypatch):
    import api.routes_handlers.skill as skill_handler

    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)

    result = skill_handler._handle_profile_installed_skills(object(), SimpleNamespace(query=""))

    assert result is True
    assert responses == [
        (400, {"error": "Missing profile", "code": "missing_profile"}),
    ]


def test_profile_installed_skills_invalid_profile_returns_code(monkeypatch):
    import api.profiles as profiles
    import api.routes_handlers.skill as skill_handler
    import api.user_provider as user_provider

    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    monkeypatch.setattr(
        user_provider,
        "current_user_id_from_handler",
        lambda _handler: (_ for _ in ()).throw(AssertionError("should not read user context")),
    )
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda _profile: (_ for _ in ()).throw(AssertionError("should not scan profile home")),
    )

    result = skill_handler._handle_profile_installed_skills(
        object(),
        SimpleNamespace(query="profile=%E4%B8%AD%E6%96%87%20profile"),
    )

    assert result is True
    assert responses == [
        (400, {"error": "Invalid profile", "code": "invalid_profile"}),
    ]


def test_profile_installed_skills_forbidden_profile_does_not_scan(tmp_path, monkeypatch):
    import api.profiles as profiles
    import api.routes_handlers.skill as skill_handler
    import api.user_provider as user_provider

    responses = []
    calls = []
    profile_name = "default_367959913725953"

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    monkeypatch.setattr(user_provider, "current_user_id_from_handler", lambda _handler: "user-1")

    def fake_verify_user_profile_access(user_id, profile):
        calls.append((user_id, profile))
        raise user_provider.UserProviderAuthError(
            "Profile is not available for current user",
            status=403,
            code="profile_forbidden",
        )

    monkeypatch.setattr(
        user_provider,
        "verify_user_profile_access",
        fake_verify_user_profile_access,
    )
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda _profile: (_ for _ in ()).throw(AssertionError("should not scan profile home")),
    )

    result = skill_handler._handle_profile_installed_skills(
        object(),
        SimpleNamespace(query=f"profile={profile_name}"),
    )

    assert result is True
    assert calls == [("user-1", profile_name)]
    assert responses == [
        (
            403,
            {
                "error": "Profile is not available for current user",
                "code": "profile_forbidden",
            },
        )
    ]


def test_profile_installed_skills_missing_skills_dir_returns_empty(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "default_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_profile_access(monkeypatch, home=profile_home)

    result = skill_handler._handle_profile_installed_skills(
        object(),
        SimpleNamespace(query=f"profile={profile_name}"),
    )

    assert result is True
    assert responses[0] == (
        200,
        {
            "profile": profile_name,
            "skills_path": str(profile_home / "skills"),
            "skills": [],
            "count": 0,
        },
    )


def test_profile_installed_skills_ignores_nested_and_missing_skill_files(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "default_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    _write_skill(
        profile_home / "skills" / "direct-skill",
        """---
name: direct-skill
---

# Direct Skill

Direct body summary.
""",
    )
    _write_skill(
        profile_home / "skills" / "category" / "nested-skill",
        """---
name: nested-skill
description: Nested should not be returned
---
""",
    )
    (profile_home / "skills" / "no-skill-md").mkdir(parents=True)
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_profile_access(monkeypatch, home=profile_home)

    result = skill_handler._handle_profile_installed_skills(
        object(),
        SimpleNamespace(query=f"profile={profile_name}"),
    )

    assert result is True
    assert responses[0][0] == 200
    assert responses[0][1]["count"] == 1
    assert responses[0][1]["skills"][0]["id"] == "direct-skill"
    assert responses[0][1]["skills"][0]["description"] == "Direct body summary."


def test_profile_installed_skills_skips_bad_frontmatter(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "default_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    _write_skill(
        profile_home / "skills" / "bad-skill",
        """---
name: [
---

Broken.
""",
    )
    _write_skill(
        profile_home / "skills" / "good-skill",
        """---
name: good-skill
description: Good summary
---
""",
    )
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_profile_access(monkeypatch, home=profile_home)

    result = skill_handler._handle_profile_installed_skills(
        object(),
        SimpleNamespace(query=f"profile={profile_name}"),
    )

    assert result is True
    assert responses[0][0] == 200
    assert responses[0][1]["count"] == 1
    assert responses[0][1]["skills"][0]["id"] == "good-skill"
