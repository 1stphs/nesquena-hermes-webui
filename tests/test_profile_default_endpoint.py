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
    command = "GET"
    path = "/api/profile/default"
    headers = _Headers()

    def __init__(self):
        self.status = None
        self.sent_headers = []
        self.body = bytearray()
        self.wfile = self

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.sent_headers.append((name, value))

    def end_headers(self):
        pass

    def write(self, data):
        self.body.extend(data)

    def json_body(self):
        return json.loads(bytes(self.body).decode("utf-8"))


def test_profile_default_endpoint_maps_default_profile(monkeypatch, tmp_path):
    default_home = tmp_path / ".hermes"
    other_home = tmp_path / ".hermes" / "profiles" / "work"

    import api.profiles as profiles

    monkeypatch.setattr(
        profiles,
        "list_profiles_api",
        lambda: [
            {
                "name": "work",
                "path": str(other_home),
                "is_default": False,
            },
            {
                "name": "default",
                "path": str(default_home),
                "is_default": True,
            },
        ],
    )

    handler = _FakeHandler()
    handled = routes.handle_get(handler, urlparse("/api/profile/default"))

    assert handled is None
    assert handler.status == 200
    assert handler.json_body() == {
        "path": str(default_home.resolve()),
        "avatar": "",
        "profile_key": "default",
        "profile_name": "default",
        "webui_profile_id": "default",
    }


def test_profile_default_endpoint_prefers_explicit_profile_fields(monkeypatch, tmp_path):
    import api.profiles as profiles

    monkeypatch.setattr(
        profiles,
        "list_profiles_api",
        lambda: [
            {
                "id": "profile-001",
                "name": "kinni",
                "path": str(tmp_path / "root"),
                "avatar": "https://example.test/avatar.png",
                "profile_key": "root-profile",
                "profile_name": "Root Profile",
                "webui_profile_id": "webui-root",
                "is_default": True,
            },
        ],
    )

    handler = _FakeHandler()
    routes.handle_get(handler, urlparse("/api/profile/default"))

    assert handler.json_body() == {
        "path": str((tmp_path / "root").resolve()),
        "avatar": "https://example.test/avatar.png",
        "profile_key": "root-profile",
        "profile_name": "Root Profile",
        "webui_profile_id": "webui-root",
    }


def test_profile_file_endpoint_returns_profile_record_fields(monkeypatch, tmp_path):
    profile_home = tmp_path / ".hermes"
    profile_file = profile_home / "profiles" / "default.md"
    profile_file.parent.mkdir(parents=True)
    profile_file.write_text("hello\nprofile\n", encoding="utf-8")

    import api.profiles as profiles

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: profile_home)
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda name: profile_home)
    monkeypatch.setattr(
        profiles,
        "list_profiles_api",
        lambda: [
            {
                "name": "default",
                "path": str(profile_home),
                "is_default": True,
            }
        ],
    )

    handler = _FakeHandler()
    routes.handle_get(handler, urlparse("/api/profile/file?path=profiles/default.md"))

    assert handler.status == 200
    payload = handler.json_body()
    assert payload["path"] == str(profile_file.resolve())
    assert payload["avatar"] == ""
    assert payload["profile_key"] == "profiles_default"
    assert payload["profile_name"] == "default"
    assert payload["webui_profile_id"] == "default:profiles/default.md"
    assert payload["source"] == "registration"
    assert payload["is_default"] is True
    assert payload["sort"] == 0
    assert payload["status"] == "active"
    assert payload["relative_path"] == "profiles/default.md"
    assert payload["content"] == "hello\nprofile\n"


def test_profile_file_endpoint_blocks_paths_outside_profile(monkeypatch, tmp_path):
    profile_home = tmp_path / ".hermes"
    outside = tmp_path / "outside.md"
    profile_home.mkdir()
    outside.write_text("nope", encoding="utf-8")

    import api.profiles as profiles

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: profile_home)
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", lambda name: profile_home)

    handler = _FakeHandler()
    routes.handle_get(handler, urlparse(f"/api/profile/file?path={outside}"))

    assert handler.status == 400
    assert handler.json_body() == {"error": "Invalid profile file path"}
