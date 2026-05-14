import io
import json
import shutil
from pathlib import Path
from urllib.parse import urlparse

from api import routes


class _Headers(dict):
    def get(self, key, default=None):
        wanted = str(key).lower()
        for name, value in self.items():
            if str(name).lower() == wanted:
                return value
        return default


class _FakeHandler:
    command = "POST"
    path = "/api/skills/install-community"

    def __init__(self, body=None):
        raw = json.dumps(body or {}).encode("utf-8")
        self.headers = _Headers({"Content-Length": str(len(raw))})
        self.rfile = io.BytesIO(raw)
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = []

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent_headers.append((name, value))

    def end_headers(self):
        pass

    def json_body(self):
        self.wfile.seek(0)
        return json.loads(self.wfile.read().decode("utf-8"))


def _post_install(body):
    handler = _FakeHandler(body)
    routes.handle_post(handler, urlparse("/api/skills/install-community"))
    return handler.json_body(), handler.status


def _post_uninstall(body):
    handler = _FakeHandler(body)
    routes.handle_post(handler, urlparse("/api/skills/uninstall-profile"))
    return handler.json_body(), handler.status


def test_install_community_skill_copies_directory(monkeypatch, tmp_path):
    community_root = tmp_path / "hermes-community-skills"
    skill_dir = community_root / "git-commit-ai"
    (skill_dir / "assets").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Git Commit AI\n", encoding="utf-8")
    (skill_dir / "assets" / "prompt.txt").write_text("commit helper", encoding="utf-8")
    target_skills = tmp_path / "profiles" / "xiongmao" / "skills"
    monkeypatch.setenv("HERMES_COMMUNITY_SKILLS_DIR", str(community_root))

    payload, status = _post_install({
        "source_path": str(skill_dir),
        "profile_skills_path": str(target_skills),
    })

    installed = target_skills / "git-commit-ai"
    assert status == 200
    assert payload["ok"] is True
    assert payload["name"] == "git-commit-ai"
    assert payload["installed_path"] == str(installed.resolve())
    assert (installed / "SKILL.md").read_text(encoding="utf-8") == "# Git Commit AI\n"
    assert (installed / "assets" / "prompt.txt").read_text(encoding="utf-8") == "commit helper"


def test_install_community_skill_accepts_server_absolute_hermes_path(monkeypatch, tmp_path):
    from api import profiles

    hermes_home = tmp_path / ".hermes"
    community_root = tmp_path / "hermes-community-skills"
    skill_dir = community_root / "algorithmic-art"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Algorithmic Art\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_COMMUNITY_SKILLS_DIR", str(community_root))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)

    payload, status = _post_install({
        "source_path": str(skill_dir),
        "profile_skills_path": "/root/.hermes/profiles/312321/skills",
    })

    installed = hermes_home / "profiles" / "312321" / "skills" / "algorithmic-art"
    assert status == 200
    assert payload["ok"] is True
    assert payload["profile_skills_path"] == str(installed.parent.resolve())
    assert (installed / "SKILL.md").read_text(encoding="utf-8") == "# Algorithmic Art\n"


def test_install_community_skill_rejects_source_outside_root(monkeypatch, tmp_path):
    community_root = tmp_path / "hermes-community-skills"
    community_root.mkdir()
    outside_skill = tmp_path / "outside-skill"
    outside_skill.mkdir()
    (outside_skill / "SKILL.md").write_text("# Outside\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_COMMUNITY_SKILLS_DIR", str(community_root))

    payload, status = _post_install({
        "source_path": str(outside_skill),
        "profile_skills_path": str(tmp_path / "profiles" / "xiongmao" / "skills"),
    })

    assert status == 400
    assert "community skills directory" in payload["error"]


def test_install_community_skill_accepts_builtin_and_optional_roots(monkeypatch, tmp_path):
    copied_sources = []
    source_paths = {
        root_name: str(Path(f"/var/www/{root_name}/git-commit-ai").resolve())
        for root_name in ("hermes-built-in-skills", "hermes-optional-skills")
    }
    skill_md_paths = {
        root_name: str((Path(source_path) / "SKILL.md").resolve())
        for root_name, source_path in source_paths.items()
    }
    original_exists = Path.exists
    original_is_dir = Path.is_dir
    original_is_file = Path.is_file

    def is_allowed_test_source(path):
        return str(path) in source_paths.values()

    def is_allowed_test_skill_md(path):
        return str(path) in skill_md_paths.values()

    def fake_exists(path):
        if is_allowed_test_source(path) or is_allowed_test_skill_md(path):
            return True
        return original_exists(path)

    def fake_is_dir(path):
        if is_allowed_test_source(path):
            return True
        return original_is_dir(path)

    def fake_is_file(path):
        if is_allowed_test_skill_md(path):
            return True
        return original_is_file(path)

    def fake_copytree(source, destination, symlinks=False):
        copied_sources.append(str(source))
        destination.mkdir(parents=True)
        (destination / "SKILL.md").write_text("# Installed\n", encoding="utf-8")

    monkeypatch.setattr(Path, "exists", fake_exists)
    monkeypatch.setattr(Path, "is_dir", fake_is_dir)
    monkeypatch.setattr(Path, "is_file", fake_is_file)
    monkeypatch.setattr(shutil, "copytree", fake_copytree)

    for root_name in ("hermes-built-in-skills", "hermes-optional-skills"):
        skill_dir = source_paths[root_name]
        target_skills = tmp_path / root_name / "profiles" / "xiongmao" / "skills"

        payload, status = _post_install({
            "source_path": skill_dir,
            "profile_skills_path": str(target_skills),
        })

        installed = target_skills / "git-commit-ai"
        assert status == 200
        assert payload["ok"] is True
        assert payload["source_path"] == skill_dir
        assert payload["installed_path"] == str(installed.resolve())
        assert (installed / "SKILL.md").read_text(encoding="utf-8") == "# Installed\n"

    assert copied_sources == [
        source_paths["hermes-built-in-skills"],
        source_paths["hermes-optional-skills"],
    ]


def test_install_community_skill_conflict_requires_overwrite(monkeypatch, tmp_path):
    community_root = tmp_path / "hermes-community-skills"
    skill_dir = community_root / "git-commit-ai"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# New\n", encoding="utf-8")
    target_skills = tmp_path / "profiles" / "xiongmao" / "skills"
    installed = target_skills / "git-commit-ai"
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text("# Old\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_COMMUNITY_SKILLS_DIR", str(community_root))

    payload, status = _post_install({
        "source_path": str(skill_dir),
        "profile_skills_path": str(target_skills),
    })
    assert status == 409
    assert payload["error"] == "Skill already installed"

    payload, status = _post_install({
        "source_path": str(skill_dir),
        "profile_skills_path": str(target_skills),
        "overwrite": True,
    })
    assert status == 200
    assert payload["overwritten"] is True
    assert (installed / "SKILL.md").read_text(encoding="utf-8") == "# New\n"


def test_install_community_skill_requires_skill_md(monkeypatch, tmp_path):
    community_root = tmp_path / "hermes-community-skills"
    skill_dir = community_root / "git-commit-ai"
    skill_dir.mkdir(parents=True)
    monkeypatch.setenv("HERMES_COMMUNITY_SKILLS_DIR", str(community_root))

    payload, status = _post_install({
        "source_path": str(skill_dir),
        "profile_skills_path": str(tmp_path / "profiles" / "xiongmao" / "skills"),
    })

    assert status == 400
    assert payload["error"] == "Skill source must contain SKILL.md"


def test_uninstall_profile_skill_removes_directory(tmp_path):
    target_skills = tmp_path / "profiles" / "xiongmao" / "skills"
    installed = target_skills / "git-commit-ai"
    (installed / "assets").mkdir(parents=True)
    (installed / "SKILL.md").write_text("# Git Commit AI\n", encoding="utf-8")
    (installed / "assets" / "prompt.txt").write_text("commit helper", encoding="utf-8")

    payload, status = _post_uninstall({
        "profile_skills_path": str(target_skills),
        "name": "git-commit-ai",
    })

    assert status == 200
    assert payload["ok"] is True
    assert payload["name"] == "git-commit-ai"
    assert payload["removed_path"] == str(installed.resolve())
    assert not installed.exists()


def test_uninstall_profile_skill_accepts_server_absolute_hermes_path(monkeypatch, tmp_path):
    from api import profiles

    hermes_home = tmp_path / ".hermes"
    target_skills = hermes_home / "profiles" / "312321" / "skills"
    installed = target_skills / "algorithmic-art"
    installed.mkdir(parents=True)
    (installed / "SKILL.md").write_text("# Algorithmic Art\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)

    payload, status = _post_uninstall({
        "profile_skills_path": "/root/.hermes/profiles/312321/skills",
        "name": "algorithmic-art",
    })

    assert status == 200
    assert payload["ok"] is True
    assert payload["profile_skills_path"] == str(target_skills.resolve())
    assert not installed.exists()


def test_uninstall_profile_skill_rejects_invalid_name(tmp_path):
    target_skills = tmp_path / "profiles" / "xiongmao" / "skills"
    target_skills.mkdir(parents=True)

    payload, status = _post_uninstall({
        "profile_skills_path": str(target_skills),
        "name": "../outside",
    })

    assert status == 400
    assert payload["error"] == "Invalid skill name"


def test_uninstall_profile_skill_requires_installed_skill(tmp_path):
    target_skills = tmp_path / "profiles" / "xiongmao" / "skills"
    target_skills.mkdir(parents=True)

    payload, status = _post_uninstall({
        "profile_skills_path": str(target_skills),
        "name": "missing-skill",
    })

    assert status == 404
    assert payload["error"] == "Skill not found"
