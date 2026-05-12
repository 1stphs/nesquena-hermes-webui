import json
import sys
import types


class _ProfileInfo:
    def __init__(self, name, path, *, avatar=""):
        self.name = name
        self.path = path
        self.is_default = False
        self.gateway_running = False
        self.model = None
        self.provider = None
        self.has_env = False
        self.skill_count = 0
        if avatar:
            self.avatar = avatar


def _install_fake_hermes_profiles(monkeypatch, infos):
    hermes_cli = types.ModuleType("hermes_cli")
    hermes_profiles = types.ModuleType("hermes_cli.profiles")
    hermes_profiles.list_profiles = lambda: infos
    hermes_cli.profiles = hermes_profiles
    monkeypatch.setitem(sys.modules, "hermes_cli", hermes_cli)
    monkeypatch.setitem(sys.modules, "hermes_cli.profiles", hermes_profiles)


def test_list_profiles_api_includes_avatar_attribute(monkeypatch, tmp_path):
    import api.profiles as profiles

    info = _ProfileInfo(
        "agent",
        tmp_path / "agent",
        avatar="https://example.test/avatar.png",
    )
    _install_fake_hermes_profiles(monkeypatch, [info])
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "agent")

    payload = profiles.list_profiles_api()

    assert payload[0]["avatar"] == "https://example.test/avatar.png"


def test_list_profiles_api_reads_avatar_metadata(monkeypatch, tmp_path):
    import api.profiles as profiles

    profile_path = tmp_path / "profiles" / "agent"
    metadata_dir = profile_path / "webui"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "agent.json").write_text(
        json.dumps({"avatar": "/uploads/agent.png"}),
        encoding="utf-8",
    )

    _install_fake_hermes_profiles(
        monkeypatch,
        [_ProfileInfo("agent", profile_path)],
    )
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "agent")

    payload = profiles.list_profiles_api()

    assert payload[0]["avatar"] == "/uploads/agent.png"
