import pytest

from api.profiles import _validate_profile_name


def test_webui_profile_name_accepts_150_characters():
    _validate_profile_name("a" * 150)


def test_webui_profile_name_rejects_151_characters():
    with pytest.raises(ValueError):
        _validate_profile_name("a" * 151)


def test_install_profiles_accepts_150_character_profile_name(tmp_path, monkeypatch):
    import api.profiles as profiles
    import api.routes_handlers.profile as profile_handler

    long_name = "a" * 150
    talent_root = tmp_path / "talent"
    source_dir = talent_root / "source"
    source_dir.mkdir(parents=True)
    (source_dir / "SOUL.md").write_text("profile", encoding="utf-8")
    profiles_root = tmp_path / ".hermes" / "profiles"
    profiles_root.mkdir(parents=True)

    monkeypatch.setattr(profile_handler, "_talent_market_profiles_root", lambda: talent_root)
    monkeypatch.setattr(profiles, "_profiles_root", lambda: profiles_root)

    responses = []

    def fake_routes_binding(name):
        if name == "j":
            return lambda _handler, payload, status=200, **_kwargs: responses.append((status, payload)) or True
        if name == "bad":
            return lambda _handler, msg, status=400: responses.append((status, {"error": msg})) or True
        if name == "_sanitize_error":
            return str
        raise AttributeError(name)

    monkeypatch.setattr(profile_handler, "_routes_binding", fake_routes_binding)

    result = profile_handler._handle_profile_install_profiles(
        object(),
        {"profile_name": long_name, "source_path": str(source_dir)},
    )

    assert result is True
    assert responses[0][0] == 200
    assert responses[0][1]["profile"]["name"] == long_name
    assert (profiles_root / long_name / "SOUL.md").read_text(encoding="utf-8") == "profile"
