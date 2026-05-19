from pathlib import Path


def test_load_workspaces_falls_back_to_named_profile_workspace_dir(tmp_path, monkeypatch):
    import api.config as config
    import api.workspace as workspace

    profile_home = tmp_path / ".hermes" / "profiles" / "myprofile"
    profile_ws = profile_home / "workspace"
    profile_ws.mkdir(parents=True)
    state_dir = profile_home / "webui_state"

    monkeypatch.setattr(config, "get_config", lambda: {})
    monkeypatch.setattr("api.profiles.get_active_profile_name", lambda: "myprofile")
    monkeypatch.setattr("api.profiles.get_active_hermes_home", lambda: profile_home)
    monkeypatch.setattr(workspace, "_workspaces_file", lambda: state_dir / "workspaces.json")

    assert workspace.load_workspaces() == [
        {"path": str(profile_ws.resolve()), "name": "Home"}
    ]


def test_profile_default_workspace_keeps_explicit_config_priority(tmp_path, monkeypatch):
    import api.config as config
    import api.workspace as workspace

    profile_home = tmp_path / ".hermes" / "profiles" / "myprofile"
    profile_ws = profile_home / "workspace"
    explicit_ws = tmp_path / "explicit"
    profile_ws.mkdir(parents=True)
    explicit_ws.mkdir()

    monkeypatch.setattr(config, "get_config", lambda: {"workspace": str(explicit_ws)})
    monkeypatch.setattr("api.profiles.get_active_hermes_home", lambda: profile_home)

    assert Path(workspace._profile_default_workspace()) == explicit_ws.resolve()
