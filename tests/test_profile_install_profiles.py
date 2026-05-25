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
    path = "/api/profile/install_profiles"

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
    routes.handle_post(handler, urlparse("/api/profile/install_profiles"))
    return handler.json_body(), handler.status


def test_install_market_profile_clones_entire_directory(monkeypatch, tmp_path):
    from api import profiles

    talent_root = tmp_path / "hermes_talent_market"
    source = talent_root / "market-analyst"
    (source / "skills" / "research").mkdir(parents=True)
    (source / "webui").mkdir()
    (source / "profiles").mkdir()
    (source / "SOUL.md").write_text("market soul\n", encoding="utf-8")
    (source / "config.yaml").write_text("model:\n  provider: custom\n", encoding="utf-8")
    (source / ".env").write_text("PROFILE_SECRET=kept\n", encoding="utf-8")
    (source / "skills" / "research" / "SKILL.md").write_text("# Research\n", encoding="utf-8")
    (source / "webui" / "agent.json").write_text('{"profile_name":"Market"}\n', encoding="utf-8")
    (source / "profiles" / "default.md").write_text("# Default\n", encoding="utf-8")

    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_TALENT_MARKET_DIR", str(talent_root))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)

    payload, status = _post_install({
        "profile_name": "market-analyst",
        "source_path": str(source),
    })

    installed = hermes_home / "profiles" / "market-analyst"
    assert status == 200
    assert payload["ok"] is True
    assert payload["profile"] == {
        "name": "market-analyst",
        "path": str(installed.resolve()),
    }
    assert payload["source_path"] == str(source.resolve())
    assert payload["installed_path"] == str(installed.resolve())
    assert payload["overwritten"] is False
    assert (installed / "SOUL.md").read_text(encoding="utf-8") == "market soul\n"
    assert (installed / "config.yaml").read_text(encoding="utf-8") == "model:\n  provider: custom\n"
    assert (installed / ".env").read_text(encoding="utf-8") == "PROFILE_SECRET=kept\n"
    assert (installed / "skills" / "research" / "SKILL.md").read_text(encoding="utf-8") == "# Research\n"
    assert (installed / "webui" / "agent.json").read_text(encoding="utf-8") == '{"profile_name":"Market"}\n'
    assert (installed / "profiles" / "default.md").read_text(encoding="utf-8") == "# Default\n"


def test_install_market_profile_rejects_source_outside_talent_root(monkeypatch, tmp_path):
    from api import profiles

    talent_root = tmp_path / "hermes_talent_market"
    talent_root.mkdir()
    outside = tmp_path / "outside" / "market-analyst"
    outside.mkdir(parents=True)
    monkeypatch.setenv("HERMES_TALENT_MARKET_DIR", str(talent_root))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", tmp_path / ".hermes")

    payload, status = _post_install({
        "profile_name": "market-analyst",
        "source_path": str(outside),
    })

    assert status == 400
    assert "talent market" in payload["error"]


def test_install_market_profile_conflict_requires_overwrite(monkeypatch, tmp_path):
    from api import profiles

    talent_root = tmp_path / "hermes_talent_market"
    source = talent_root / "market-analyst"
    source.mkdir(parents=True)
    (source / "SOUL.md").write_text("new soul\n", encoding="utf-8")
    hermes_home = tmp_path / ".hermes"
    installed = hermes_home / "profiles" / "market-analyst"
    installed.mkdir(parents=True)
    (installed / "SOUL.md").write_text("old soul\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_TALENT_MARKET_DIR", str(talent_root))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", hermes_home)

    payload, status = _post_install({
        "profile_name": "market-analyst",
        "source_path": str(source),
    })
    assert status == 409
    assert payload["error"] == "Profile already installed"
    assert (installed / "SOUL.md").read_text(encoding="utf-8") == "old soul\n"

    payload, status = _post_install({
        "profile_name": "market-analyst",
        "source_path": str(source),
        "overwrite": True,
    })
    assert status == 200
    assert payload["overwritten"] is True
    assert (installed / "SOUL.md").read_text(encoding="utf-8") == "new soul\n"


def test_install_market_profile_rejects_mismatched_profile_path(monkeypatch, tmp_path):
    from api import profiles

    talent_root = tmp_path / "hermes_talent_market"
    source = talent_root / "market-analyst"
    source.mkdir(parents=True)
    monkeypatch.setenv("HERMES_TALENT_MARKET_DIR", str(talent_root))
    monkeypatch.setattr(profiles, "_DEFAULT_HERMES_HOME", tmp_path / ".hermes")

    payload, status = _post_install({
        "profile_name": "market-analyst",
        "source_path": str(source),
        "profile_path": "/home/hermeswebui/.hermes/profiles/other-profile",
    })

    assert status == 400
    assert "profile_path must end with profile_name" in payload["error"]
