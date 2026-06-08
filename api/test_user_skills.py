import io
import zipfile
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


def _skill_markdown(name="邮箱助手", description="处理邮件草稿"):
    return f"""---
name: {name}
description: {description}
---

# {name}
"""


def _make_user_skill_record(
    *,
    user_id="user-1",
    skill_slug="mail-assistant",
    name="邮箱助手",
    description="处理邮件草稿",
    source="profile",
    source_filename="",
    source_type="profile",
    source_profile_name="default_367959913725953",
    source_skill_slug="email-assistant",
    record_id="record-1",
    status="active",
):
    return {
        "id": record_id,
        "user_id": user_id,
        "skill_slug": skill_slug,
        "name": name,
        "description": description,
        "source": source,
        "source_filename": source_filename,
        "source_type": source_type,
        "source_profile_name": source_profile_name,
        "source_skill_slug": source_skill_slug,
        "storage_path": f"{user_id}/my-skills/{skill_slug}",
        "skill_file_path": "SKILL.md",
        "file_count": 1,
        "size_bytes": 128,
        "status": status,
    }


@pytest.fixture(autouse=True)
def nocobase_user_skills(monkeypatch):
    import api.routes_handlers.skill as skill_handler

    state = SimpleNamespace(records=[], create_calls=[], update_calls=[])

    def fake_list_records(user_id, *, skill_slug=""):
        return [
            record
            for record in state.records
            if record.get("user_id") == user_id
            and (not skill_slug or record.get("skill_slug") == skill_slug)
        ]

    def fake_create_record(record):
        state.create_calls.append(record.copy())
        created = {
            **record,
            "id": f"record-{len(state.records) + 1}",
        }
        state.records.insert(0, created)
        return created

    def fake_update_record(user_id, original_skill_slug, patch):
        state.update_calls.append(
            {
                "user_id": user_id,
                "original_skill_slug": original_skill_slug,
                "patch": patch.copy(),
            }
        )
        for index, record in enumerate(state.records):
            if record.get("user_id") == user_id and record.get("skill_slug") == original_skill_slug:
                updated = {**record, **patch}
                state.records[index] = updated
                return updated
        raise skill_handler._NocobaseSkillError(
            "Skill record not found",
            status=404,
            code="skill_record_not_found",
        )

    monkeypatch.setattr(skill_handler, "_nocobase_list_user_skill_records", fake_list_records)
    monkeypatch.setattr(skill_handler, "_nocobase_create_user_skill_record", fake_create_record)
    monkeypatch.setattr(skill_handler, "_nocobase_update_user_skill_record", fake_update_record)
    return state


def _response(responses):
    assert responses
    return responses[-1]


def test_user_skills_list_reads_nocobase_records(tmp_path, monkeypatch, nocobase_user_skills):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    nocobase_user_skills.records.append(
        _make_user_skill_record(
            source="imported",
            source_filename="mail.md",
            source_type="markdown",
            source_profile_name="",
            source_skill_slug="",
        )
    )
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")

    result = skill_handler._handle_user_skills_list(object(), SimpleNamespace(query=""))

    assert result is True
    status, payload = _response(responses)
    assert status == 200
    assert payload["count"] == 1
    assert payload["skills"][0]["englishName"] == "mail-assistant"
    assert payload["skills"][0]["name"] == "邮箱助手"
    assert payload["skills"][0]["summary"] == "处理邮件草稿"
    assert payload["skills"][0]["source"] == "imported"
    assert payload["skills"][0]["sourceFilename"] == "mail.md"
    assert payload["skills"][0]["storagePath"] == "user-1/my-skills/mail-assistant"
    assert payload["skills"][0]["status"] == "fully_tested"


def test_import_markdown_writes_my_skills_and_creates_nocobase_record(
    tmp_path,
    monkeypatch,
    nocobase_user_skills,
):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    responses = []
    handler = SimpleNamespace(
        headers={
            "Content-Type": "multipart/form-data",
            "Content-Length": "100",
        },
        rfile=io.BytesIO(b""),
    )

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")
    monkeypatch.setattr(
        skill_handler,
        "parse_multipart",
        lambda _rfile, _content_type, _content_length: (
            {"english_name": "mail-assistant"},
            {"file": ("mail.md", _skill_markdown().encode("utf-8"))},
        ),
    )

    result = skill_handler._handle_user_skill_import(handler)

    destination = user_root / "user-1" / "my-skills" / "mail-assistant"
    assert result is True
    assert destination.is_dir()
    assert (destination / "SKILL.md").is_file()
    assert len(nocobase_user_skills.create_calls) == 1
    assert nocobase_user_skills.create_calls[0]["source"] == "imported"
    assert nocobase_user_skills.create_calls[0]["source_filename"] == "mail.md"
    assert nocobase_user_skills.create_calls[0]["source_type"] == "markdown"
    assert nocobase_user_skills.create_calls[0]["storage_path"] == "user-1/my-skills/mail-assistant"
    assert nocobase_user_skills.create_calls[0]["status"] == "draft"
    status, payload = _response(responses)
    assert status == 200
    assert payload["skill"]["englishName"] == "mail-assistant"
    assert payload["skill"]["source"] == "imported"


def test_import_archive_writes_my_skills_and_creates_nocobase_record(
    tmp_path,
    monkeypatch,
    nocobase_user_skills,
):
    import api.routes_handlers.skill as skill_handler

    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w") as archive:
        archive.writestr("bundle/SKILL.md", _skill_markdown())
        archive.writestr("bundle/assets/readme.txt", "asset")

    user_root = tmp_path / "users"
    responses = []
    handler = SimpleNamespace(
        headers={
            "Content-Type": "multipart/form-data",
            "Content-Length": "200",
        },
        rfile=io.BytesIO(b""),
    )

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")
    monkeypatch.setattr(
        skill_handler,
        "parse_multipart",
        lambda _rfile, _content_type, _content_length: (
            {"skill_slug": "mail-assistant"},
            {"file": ("mail.zip", archive_bytes.getvalue())},
        ),
    )

    result = skill_handler._handle_user_skill_import(handler)

    destination = user_root / "user-1" / "my-skills" / "mail-assistant"
    assert result is True
    assert destination.is_dir()
    assert (destination / "SKILL.md").is_file()
    assert (destination / "assets" / "readme.txt").is_file()
    assert len(nocobase_user_skills.create_calls) == 1
    assert nocobase_user_skills.create_calls[0]["source_type"] == "archive"
    assert _response(responses)[0] == 200


def test_import_rejects_missing_english_name(tmp_path, monkeypatch, nocobase_user_skills):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    responses = []
    handler = SimpleNamespace(
        headers={
            "Content-Type": "multipart/form-data",
            "Content-Length": "100",
        },
        rfile=io.BytesIO(b""),
    )

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")
    monkeypatch.setattr(
        skill_handler,
        "parse_multipart",
        lambda _rfile, _content_type, _content_length: (
            {},
            {"file": ("mail.md", _skill_markdown().encode("utf-8"))},
        ),
    )

    result = skill_handler._handle_user_skill_import(handler)

    assert result is True
    assert not (user_root / "user-1" / "my-skills").exists()
    assert not nocobase_user_skills.create_calls
    assert _response(responses)[0] == 400
    assert _response(responses)[1]["code"] == "missing_english_name"


def test_import_rejects_invalid_english_name(tmp_path, monkeypatch, nocobase_user_skills):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    responses = []
    handler = SimpleNamespace(
        headers={
            "Content-Type": "multipart/form-data",
            "Content-Length": "100",
        },
        rfile=io.BytesIO(b""),
    )

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")
    monkeypatch.setattr(
        skill_handler,
        "parse_multipart",
        lambda _rfile, _content_type, _content_length: (
            {"english_name": "../mail"},
            {"file": ("mail.md", _skill_markdown().encode("utf-8"))},
        ),
    )

    result = skill_handler._handle_user_skill_import(handler)

    assert result is True
    assert not (user_root / "user-1" / "my-skills").exists()
    assert not nocobase_user_skills.create_calls
    assert _response(responses)[0] == 400
    assert _response(responses)[1]["code"] == "invalid_english_name"


def test_import_cleans_up_files_when_nocobase_create_fails(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    responses = []
    handler = SimpleNamespace(
        headers={
            "Content-Type": "multipart/form-data",
            "Content-Length": "100",
        },
        rfile=io.BytesIO(b""),
    )

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")
    monkeypatch.setattr(
        skill_handler,
        "parse_multipart",
        lambda _rfile, _content_type, _content_length: (
            {"english_name": "mail-assistant"},
            {"file": ("mail.md", _skill_markdown().encode("utf-8"))},
        ),
    )
    monkeypatch.setattr(
        skill_handler,
        "_nocobase_create_user_skill_record",
        lambda _record: (_ for _ in ()).throw(
            skill_handler._NocobaseSkillError(
                "NoCoBase create failed",
                status=502,
                code="nocobase_request_failed",
            )
        ),
    )

    result = skill_handler._handle_user_skill_import(handler)

    assert result is True
    assert not (user_root / "user-1" / "my-skills" / "mail-assistant").exists()
    assert _response(responses)[0] == 502
    assert _response(responses)[1]["code"] == "nocobase_request_failed"


def test_publish_from_profile_copies_skill_updates_name_and_creates_nocobase_record(
    tmp_path,
    monkeypatch,
    nocobase_user_skills,
):
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
    metadata, _body = skill_handler._split_skill_frontmatter(
        (destination / "SKILL.md").read_text(encoding="utf-8")
    )
    assert metadata["name"] == "邮箱助手"
    assert len(nocobase_user_skills.create_calls) == 1
    assert nocobase_user_skills.create_calls[0]["user_id"] == "user-1"
    assert nocobase_user_skills.create_calls[0]["skill_slug"] == "mail-assistant"
    assert nocobase_user_skills.create_calls[0]["source"] == "profile"
    assert nocobase_user_skills.create_calls[0]["source_type"] == "profile"
    assert nocobase_user_skills.create_calls[0]["source_profile_name"] == profile_name
    assert nocobase_user_skills.create_calls[0]["source_skill_slug"] == "email-assistant"
    assert nocobase_user_skills.create_calls[0]["storage_path"] == "user-1/my-skills/mail-assistant"
    assert nocobase_user_skills.create_calls[0]["status"] == "draft"
    status, payload = _response(responses)
    assert status == 200
    assert payload["ok"] is True
    assert payload["skill"]["englishName"] == "mail-assistant"
    assert payload["skill"]["name"] == "邮箱助手"
    assert payload["skill"]["source"] == "profile"


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


def test_publish_from_profile_cleans_up_when_nocobase_create_fails(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    profile_name = "default_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(profile_home / "skills" / "email-assistant")
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=profile_home)
    monkeypatch.setattr(
        skill_handler,
        "_nocobase_create_user_skill_record",
        lambda _record: (_ for _ in ()).throw(
            skill_handler._NocobaseSkillError(
                "NoCoBase create failed",
                status=502,
                code="nocobase_request_failed",
            )
        ),
    )

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
    assert _response(responses)[0] == 502
    assert _response(responses)[1]["code"] == "nocobase_request_failed"


def test_install_user_skill_to_profile_copies_without_nocobase_binding(
    tmp_path,
    monkeypatch,
    nocobase_user_skills,
):
    import api.routes_handlers.skill as skill_handler

    profile_name = "target_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(user_root / "user-1" / "my-skills" / "mail-assistant")
    nocobase_user_skills.records.append(_make_user_skill_record(status="availability_tested"))
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
    assert not nocobase_user_skills.create_calls
    assert not nocobase_user_skills.update_calls


def test_install_user_skill_to_profile_rejects_draft_status(
    tmp_path,
    monkeypatch,
    nocobase_user_skills,
):
    import api.routes_handlers.skill as skill_handler

    profile_name = "target_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(user_root / "user-1" / "my-skills" / "mail-assistant")
    nocobase_user_skills.records.append(_make_user_skill_record(status="draft"))
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
    assert _response(responses)[0] == 400
    assert _response(responses)[1]["code"] == "user_skill_not_tested"


def test_install_user_skill_to_profile_rejects_same_folder_name(
    tmp_path,
    monkeypatch,
    nocobase_user_skills,
):
    import api.routes_handlers.skill as skill_handler

    profile_name = "target_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(user_root / "user-1" / "my-skills" / "mail-assistant")
    _write_skill(profile_home / "skills" / "mail-assistant")
    nocobase_user_skills.records.append(_make_user_skill_record(status="security_tested"))
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


def test_install_user_skill_to_profile_rejects_destination_lock(
    tmp_path,
    monkeypatch,
    nocobase_user_skills,
):
    import api.routes_handlers.skill as skill_handler

    profile_name = "target_367959913725953"
    profile_home = tmp_path / "profiles" / profile_name
    user_root = tmp_path / "users"
    _write_skill(user_root / "user-1" / "my-skills" / "mail-assistant")
    (profile_home / "skills" / ".mail-assistant.lock").mkdir(parents=True)
    nocobase_user_skills.records.append(_make_user_skill_record(status="fully_tested"))
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


def test_update_user_skill_renames_folder_updates_name_and_updates_nocobase(
    tmp_path,
    monkeypatch,
    nocobase_user_skills,
):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    source = user_root / "user-1" / "my-skills" / "mail-assistant"
    _write_skill(source)
    nocobase_user_skills.records.append(_make_user_skill_record())
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
    metadata, _body = skill_handler._split_skill_frontmatter(
        (destination / "SKILL.md").read_text(encoding="utf-8")
    )
    assert metadata["name"] == "新邮箱助手"
    assert len(nocobase_user_skills.update_calls) == 1
    update_call = nocobase_user_skills.update_calls[0]
    assert update_call["user_id"] == "user-1"
    assert update_call["original_skill_slug"] == "mail-assistant"
    assert update_call["patch"]["skill_slug"] == "new-mail-assistant"
    assert update_call["patch"]["name"] == "新邮箱助手"
    assert update_call["patch"]["storage_path"] == "user-1/my-skills/new-mail-assistant"
    assert "status" not in update_call["patch"]
    assert _response(responses)[0] == 200
    assert _response(responses)[1]["skill"]["englishName"] == "new-mail-assistant"


def test_update_user_skill_same_slug_updates_frontmatter_and_nocobase(
    tmp_path,
    monkeypatch,
    nocobase_user_skills,
):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    source = user_root / "user-1" / "my-skills" / "mail-assistant"
    _write_skill(source)
    nocobase_user_skills.records.append(_make_user_skill_record())
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")

    result = skill_handler._handle_user_skill_update(
        object(),
        {
            "skill_slug": "mail-assistant",
            "english_name": "mail-assistant",
            "name": "新邮箱助手",
        },
    )

    assert result is True
    assert source.is_dir()
    metadata, _body = skill_handler._split_skill_frontmatter(
        (source / "SKILL.md").read_text(encoding="utf-8")
    )
    assert metadata["name"] == "新邮箱助手"
    assert len(nocobase_user_skills.update_calls) == 1
    assert nocobase_user_skills.update_calls[0]["patch"]["skill_slug"] == "mail-assistant"
    assert nocobase_user_skills.update_calls[0]["patch"]["name"] == "新邮箱助手"
    assert "status" not in nocobase_user_skills.update_calls[0]["patch"]
    assert _response(responses)[0] == 200


def test_update_user_skill_status_only_updates_nocobase_without_touching_files(
    tmp_path,
    monkeypatch,
    nocobase_user_skills,
):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    source = user_root / "user-1" / "my-skills" / "mail-assistant"
    _write_skill(source)
    original_content = (source / "SKILL.md").read_text(encoding="utf-8")
    nocobase_user_skills.records.append(_make_user_skill_record(status="draft"))
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")

    result = skill_handler._handle_user_skill_update(
        object(),
        {
            "skill_slug": "mail-assistant",
            "status": "availability_tested",
        },
    )

    assert result is True
    assert (source / "SKILL.md").read_text(encoding="utf-8") == original_content
    assert len(nocobase_user_skills.update_calls) == 1
    update_call = nocobase_user_skills.update_calls[0]
    assert update_call["original_skill_slug"] == "mail-assistant"
    assert update_call["patch"] == {"status": "availability_tested"}
    assert _response(responses)[0] == 200
    assert _response(responses)[1]["skill"]["status"] == "availability_tested"


def test_update_user_skill_status_rejects_invalid_status(
    tmp_path,
    monkeypatch,
    nocobase_user_skills,
):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    _write_skill(user_root / "user-1" / "my-skills" / "mail-assistant")
    nocobase_user_skills.records.append(_make_user_skill_record(status="draft"))
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")

    result = skill_handler._handle_user_skill_update(
        object(),
        {
            "skill_slug": "mail-assistant",
            "status": "active",
        },
    )

    assert result is True
    assert not nocobase_user_skills.update_calls
    assert _response(responses)[0] == 400
    assert _response(responses)[1]["code"] == "invalid_status"


def test_update_user_skill_rolls_back_files_when_nocobase_update_fails(tmp_path, monkeypatch):
    import api.routes_handlers.skill as skill_handler

    user_root = tmp_path / "users"
    source = user_root / "user-1" / "my-skills" / "mail-assistant"
    _write_skill(source)
    responses = []

    _patch_skill_handler_bindings(monkeypatch, skill_handler, responses)
    _patch_user_root(monkeypatch, skill_handler, user_root)
    _patch_profile_access(monkeypatch, home=tmp_path / "profiles" / "default_1")
    monkeypatch.setattr(
        skill_handler,
        "_nocobase_update_user_skill_record",
        lambda _user_id, _original_skill_slug, _patch: (_ for _ in ()).throw(
            skill_handler._NocobaseSkillError(
                "NoCoBase update failed",
                status=502,
                code="nocobase_request_failed",
            )
        ),
    )

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
    assert source.is_dir()
    assert not destination.exists()
    assert "name: 原始助手" in (source / "SKILL.md").read_text(encoding="utf-8")
    assert _response(responses)[0] == 502
    assert _response(responses)[1]["code"] == "nocobase_request_failed"


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
