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
    path = "/api/profile/create-agent"

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


def _catalog():
    return [
        {
            "name": "web-search",
            "description": "Search webpages and summarize sources",
            "category": "research",
        },
        {
            "name": "doc-summary",
            "description": "Summarize files and documents",
            "category": "productivity",
        },
        {
            "name": "table-analysis",
            "description": "Analyze spreadsheets and tables",
            "category": "data",
        },
    ]


def test_profile_create_agent_skills_endpoint_filters_catalog(monkeypatch):
    monkeypatch.setattr(routes, "_load_profile_agent_skills_catalog", _catalog, raising=False)

    handler = _FakeHandler()
    routes.handle_get(handler, urlparse("/api/profile/create-agent/skills?q=doc"))

    assert handler.status == 200
    payload = handler.json_body()
    assert payload["query"] == "doc"
    assert [skill["name"] for skill in payload["skills"]] == ["doc-summary"]
    assert payload["recommended"][0]["name"] == "web-search"


def test_profile_create_agent_writes_agent_files(monkeypatch, tmp_path):
    created = {}

    def fake_create_profile_api(name, **kwargs):
        created["name"] = name
        created["kwargs"] = kwargs
        profile_path = tmp_path / "profiles" / name
        profile_path.mkdir(parents=True)
        return {
            "name": name,
            "path": str(profile_path),
            "is_default": False,
            "skill_count": 0,
        }

    import api.profiles as profiles

    monkeypatch.setattr(profiles, "create_profile_api", fake_create_profile_api)
    monkeypatch.setattr(routes, "_load_profile_agent_skills_catalog", _catalog, raising=False)

    body = {
        "profile_id": "market-analyst",
        "name": "市场分析助手",
        "description": "用简短的话描述智能体的核心能力或用途",
        "prompt": "你是一位专业的市场分析助手。",
        "avatar": "/uploads/market.png",
        "skills": ["web-search", "doc-summary"],
    }
    handler = _FakeHandler(body)
    routes.handle_post(handler, urlparse("/api/profile/create-agent"))

    assert handler.status == 200
    payload = handler.json_body()
    assert payload["ok"] is True
    assert payload["agent"]["profile_id"] == "market-analyst"
    assert payload["agent"]["profile_name"] == "市场分析助手"
    assert payload["agent"]["skills"] == ["web-search", "doc-summary"]
    assert created == {
        "name": "market-analyst",
        "kwargs": {
            "clone_from": None,
            "clone_config": False,
            "base_url": None,
            "api_key": None,
        },
    }

    profile_path = tmp_path / "profiles" / "market-analyst"
    assert (profile_path / "SOUL.md").read_text(encoding="utf-8") == body["prompt"] + "\n"

    agent_md = (profile_path / "profiles" / "default.md").read_text(encoding="utf-8")
    assert "profile_name: 市场分析助手" in agent_md
    assert "description: 用简短的话描述智能体的核心能力或用途" in agent_md
    assert "- web-search" in agent_md
    assert agent_md.rstrip().endswith(body["prompt"])

    agent_json = json.loads((profile_path / "webui" / "agent.json").read_text(encoding="utf-8"))
    assert agent_json["profile_id"] == "market-analyst"
    assert agent_json["avatar"] == "/uploads/market.png"
    assert agent_json["status"] == "active"


def test_profile_create_agent_rejects_unknown_skills(monkeypatch):
    called = False

    def fake_create_profile_api(name, **kwargs):
        nonlocal called
        called = True
        return {"name": name, "path": "/tmp/unused"}

    import api.profiles as profiles

    monkeypatch.setattr(profiles, "create_profile_api", fake_create_profile_api)
    monkeypatch.setattr(routes, "_load_profile_agent_skills_catalog", _catalog, raising=False)

    handler = _FakeHandler(
        {
            "profile_id": "market-analyst",
            "name": "市场分析助手",
            "description": "市场分析",
            "prompt": "你是一位专业的市场分析助手。",
            "avatar": "/uploads/market.png",
            "skills": ["missing-skill"],
        }
    )
    routes.handle_post(handler, urlparse("/api/profile/create-agent"))

    assert handler.status == 400
    assert "Unknown skill(s): missing-skill" in handler.json_body()["error"]
    assert called is False


def test_profile_update_agent_updates_active_profile_files(monkeypatch, tmp_path):
    profile_path = tmp_path / "profiles" / "market-analyst"
    profile_path.mkdir(parents=True)
    (profile_path / "SOUL.md").write_text("old prompt\n", encoding="utf-8")

    original_agent = {
        "profile_id": "market-analyst",
        "profile_name": "旧名称",
        "avatar": "/uploads/market.png",
        "description": "旧简介",
        "prompt": "old prompt",
        "skills": ["web-search"],
        "status": "active",
        "created_at": "2026-05-01T00:00:00Z",
        "updated_at": "2026-05-01T00:00:00Z",
    }
    (profile_path / "webui").mkdir()
    (profile_path / "webui" / "agent.json").write_text(
        json.dumps(original_agent, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    import api.profiles as profiles

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "market-analyst")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: profile_path)
    monkeypatch.setattr(routes, "_load_profile_agent_skills_catalog", _catalog, raising=False)

    body = {
        "name": "新名称",
        "description": "新的智能体简介",
        "prompt": "你是一位更新后的助手。",
        "skills": ["doc-summary", "web-search"],
    }
    handler = _FakeHandler(body)
    routes.handle_post(handler, urlparse("/api/profile/update-agent"))

    assert handler.status == 200
    payload = handler.json_body()
    assert payload["ok"] is True
    assert payload["agent"]["profile_id"] == "market-analyst"
    assert payload["agent"]["profile_name"] == "新名称"
    assert payload["agent"]["description"] == "新的智能体简介"
    assert payload["agent"]["skills"] == ["doc-summary", "web-search"]
    assert payload["agent"]["created_at"] == "2026-05-01T00:00:00Z"
    assert payload["agent"]["updated_at"] != "2026-05-01T00:00:00Z"

    assert (profile_path / "SOUL.md").read_text(encoding="utf-8") == body["prompt"] + "\n"
    agent_md = (profile_path / "profiles" / "default.md").read_text(encoding="utf-8")
    assert "profile_name: 新名称" in agent_md
    assert "description: 新的智能体简介" in agent_md
    assert "- doc-summary" in agent_md
    assert agent_md.rstrip().endswith(body["prompt"])

    agent_json = json.loads((profile_path / "webui" / "agent.json").read_text(encoding="utf-8"))
    assert agent_json["profile_name"] == "新名称"
    assert agent_json["avatar"] == "/uploads/market.png"
    assert agent_json["status"] == "active"


def test_profile_update_agent_rejects_unknown_skills_without_writing(monkeypatch, tmp_path):
    profile_path = tmp_path / "profiles" / "market-analyst"
    profile_path.mkdir(parents=True)
    (profile_path / "SOUL.md").write_text("old prompt\n", encoding="utf-8")

    import api.profiles as profiles

    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: profile_path)
    monkeypatch.setattr(routes, "_load_profile_agent_skills_catalog", _catalog, raising=False)

    handler = _FakeHandler(
        {
            "name": "市场分析助手",
            "description": "市场分析",
            "prompt": "新 prompt",
            "skills": ["missing-skill"],
        }
    )
    routes.handle_post(handler, urlparse("/api/profile/update-agent"))

    assert handler.status == 400
    assert "Unknown skill(s): missing-skill" in handler.json_body()["error"]
    assert (profile_path / "SOUL.md").read_text(encoding="utf-8") == "old prompt\n"
