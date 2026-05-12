import io
import json
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


def test_install_community_skill_copies_directory(monkeypatch, tmp_path):
    community_root = tmp_path / "hermes-community-skills"
    skill_dir = community_root / "git-commit-ai"
    (skill_dir / "assets").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# Git Commit AI\n", encoding="utf-8")
    (skill_dir / "assets" / "prompt.txt").write_text("commit helper", encoding="utf-8")
    target_skills = tmp_path / ".hermes" / "profiles" / "xiongmao" / "skills"
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


def test_install_community_skill_rejects_source_outside_root(monkeypatch, tmp_path):
    community_root = tmp_path / "hermes-community-skills"
    community_root.mkdir()
    outside_skill = tmp_path / "outside-skill"
    outside_skill.mkdir()
    (outside_skill / "SKILL.md").write_text("# Outside\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_COMMUNITY_SKILLS_DIR", str(community_root))

    payload, status = _post_install({
        "source_path": str(outside_skill),
        "profile_skills_path": str(tmp_path / ".hermes" / "profiles" / "xiongmao" / "skills"),
    })

    assert status == 400
    assert "community skills directory" in payload["error"]


def test_install_community_skill_conflict_requires_overwrite(monkeypatch, tmp_path):
    community_root = tmp_path / "hermes-community-skills"
    skill_dir = community_root / "git-commit-ai"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# New\n", encoding="utf-8")
    target_skills = tmp_path / ".hermes" / "profiles" / "xiongmao" / "skills"
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
        "profile_skills_path": str(tmp_path / ".hermes" / "profiles" / "xiongmao" / "skills"),
    })

    assert status == 400
    assert payload["error"] == "Skill source must contain SKILL.md"
