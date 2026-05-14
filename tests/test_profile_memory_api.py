import io
import json
from pathlib import Path
from urllib.parse import quote, urlparse

from api import routes


class _Headers(dict):
    def get(self, key, default=None):
        wanted = str(key).lower()
        for name, value in self.items():
            if str(name).lower() == wanted:
                return value
        return default


class _FakeHandler:
    command = "GET"
    path = "/api/profile/memory"
    client_address = ("127.0.0.1", 12345)

    def __init__(self, body=None, *, method="GET"):
        raw = json.dumps(body or {}).encode("utf-8")
        self.command = method
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


def _patch_profiles(monkeypatch, base: Path, profile: Path):
    import api.profiles as profiles

    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", base)
    monkeypatch.setattr(
        profiles,
        "list_profiles_api",
        lambda: [
            {"name": "default", "path": str(base), "is_default": True},
            {"name": profile.name, "path": str(profile), "is_default": False},
        ],
    )


def test_profile_memory_read_returns_requested_profile_content(monkeypatch, tmp_path):
    base = tmp_path / ".hermes"
    profile = base / "profiles" / "agent-c59d60cc"
    memory_file = profile / "memories" / "MEMORY.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("exact profile memory\n", encoding="utf-8")
    _patch_profiles(monkeypatch, base, profile)

    handler = _FakeHandler()
    routes.handle_get(
        handler,
        urlparse(f"/api/profile/memory?path={quote(str(profile))}"),
    )

    assert handler.status == 200
    payload = handler.json_body()
    assert payload["path"] == str(profile)
    assert payload["profile_path"] == str(profile.resolve())
    assert payload["content"] == "exact profile memory\n"


def test_profile_memory_write_overwrites_memory_file(monkeypatch, tmp_path):
    base = tmp_path / ".hermes"
    profile = base / "profiles" / "agent-c59d60cc"
    profile.mkdir(parents=True)
    _patch_profiles(monkeypatch, base, profile)

    handler = _FakeHandler(
        {"path": str(profile), "content": "new memory body\n"},
        method="POST",
    )
    routes.handle_post(handler, urlparse("/api/profile/memory"))

    assert handler.status == 200
    payload = handler.json_body()
    memory_file = profile / "memories" / "MEMORY.md"
    assert payload["ok"] is True
    assert payload["path"] == str(profile)
    assert payload["profile_path"] == str(profile.resolve())
    assert payload["memory_path"] == str(memory_file.resolve())
    assert payload["content"] == "new memory body\n"
    assert payload["bytes"] == len("new memory body\n".encode("utf-8"))
    assert memory_file.read_text(encoding="utf-8") == "new memory body\n"


def test_profile_memory_write_allows_empty_content(monkeypatch, tmp_path):
    base = tmp_path / ".hermes"
    profile = base / "profiles" / "agent-c59d60cc"
    memory_file = profile / "memories" / "MEMORY.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("old content\n", encoding="utf-8")
    _patch_profiles(monkeypatch, base, profile)

    handler = _FakeHandler({"path": str(profile), "content": ""}, method="POST")
    routes.handle_post(handler, urlparse("/api/profile/memory"))

    assert handler.status == 200
    assert memory_file.read_text(encoding="utf-8") == ""


def test_profile_memory_rejects_paths_outside_hermes_profiles(monkeypatch, tmp_path):
    base = tmp_path / ".hermes"
    profile = base / "profiles" / "agent-c59d60cc"
    profile.mkdir(parents=True)
    outside = tmp_path / "outside-profile"
    outside.mkdir()
    _patch_profiles(monkeypatch, base, profile)

    handler = _FakeHandler()
    routes.handle_get(
        handler,
        urlparse(f"/api/profile/memory?path={quote(str(outside))}"),
    )

    assert handler.status == 400
    assert "Hermes profile directory" in handler.json_body()["error"]


def test_profile_memory_accepts_hermes_root_shorthand(monkeypatch, tmp_path):
    base = tmp_path / ".hermes"
    profile = base / "profiles" / "agent-c59d60cc"
    memory_file = profile / "memories" / "MEMORY.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text("shorthand profile memory\n", encoding="utf-8")
    _patch_profiles(monkeypatch, base, profile)

    handler = _FakeHandler()
    routes.handle_get(
        handler,
        urlparse("/api/profile/memory?path=/.hermes/profiles/agent-c59d60cc"),
    )

    assert handler.status == 200
    payload = handler.json_body()
    assert payload["path"] == "/.hermes/profiles/agent-c59d60cc"
    assert payload["profile_path"] == str(profile.resolve())
    assert payload["content"] == "shorthand profile memory\n"


def test_profile_user_read_returns_requested_profile_user_content(monkeypatch, tmp_path):
    base = tmp_path / ".hermes"
    profile = base / "profiles" / "agent-c59d60cc"
    user_file = profile / "memories" / "USER.md"
    user_file.parent.mkdir(parents=True)
    user_file.write_text("exact profile user\n", encoding="utf-8")
    _patch_profiles(monkeypatch, base, profile)

    handler = _FakeHandler()
    routes.handle_get(
        handler,
        urlparse(f"/api/profile/user?path={quote(str(profile))}"),
    )

    assert handler.status == 200
    payload = handler.json_body()
    assert payload["path"] == str(profile)
    assert payload["profile_path"] == str(profile.resolve())
    assert payload["content"] == "exact profile user\n"


def test_profile_user_write_overwrites_user_file_only(monkeypatch, tmp_path):
    base = tmp_path / ".hermes"
    profile = base / "profiles" / "agent-c59d60cc"
    memory_file = profile / "memories" / "MEMORY.md"
    user_file = profile / "memories" / "USER.md"
    user_file.parent.mkdir(parents=True)
    memory_file.write_text("memory stays\n", encoding="utf-8")
    user_file.write_text("old user\n", encoding="utf-8")
    _patch_profiles(monkeypatch, base, profile)

    handler = _FakeHandler(
        {"path": "/.hermes/profiles/agent-c59d60cc", "content": "new user body\n"},
        method="POST",
    )
    routes.handle_post(handler, urlparse("/api/profile/user"))

    assert handler.status == 200
    payload = handler.json_body()
    assert payload["ok"] is True
    assert payload["path"] == "/.hermes/profiles/agent-c59d60cc"
    assert payload["profile_path"] == str(profile.resolve())
    assert payload["user_path"] == str(user_file.resolve())
    assert payload["content"] == "new user body\n"
    assert user_file.read_text(encoding="utf-8") == "new user body\n"
    assert memory_file.read_text(encoding="utf-8") == "memory stays\n"
