import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _reload_profiles_module(base_home: Path):
    import api as api_package

    old_base_home = os.environ.get("HERMES_BASE_HOME")
    old_hermes_home = os.environ.get("HERMES_HOME")
    os.environ["HERMES_BASE_HOME"] = str(base_home)
    os.environ["HERMES_HOME"] = str(base_home)

    saved_modules = {
        name: sys.modules[name]
        for name in ["api.config", "api.profiles"]
        if name in sys.modules
    }
    saved_attrs = {
        name: (hasattr(api_package, name), getattr(api_package, name, None))
        for name in ["config", "profiles"]
    }

    for name in ["api.config", "api.profiles"]:
        sys.modules.pop(name, None)

    try:
        profiles = importlib.import_module("api.profiles")
    finally:
        for name in ["api.config", "api.profiles"]:
            if name in saved_modules:
                sys.modules[name] = saved_modules[name]
            else:
                sys.modules.pop(name, None)
        for name, (had_attr, value) in saved_attrs.items():
            if had_attr:
                setattr(api_package, name, value)
            else:
                try:
                    delattr(api_package, name)
                except AttributeError:
                    pass
        if old_base_home is None:
            os.environ.pop("HERMES_BASE_HOME", None)
        else:
            os.environ["HERMES_BASE_HOME"] = old_base_home
        if old_hermes_home is None:
            os.environ.pop("HERMES_HOME", None)
        else:
            os.environ["HERMES_HOME"] = old_hermes_home

    return profiles


def test_switch_profile_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        base = temp_root / ".hermes"
        (base / "profiles").mkdir(parents=True)
        (temp_root / "escape-target").mkdir()

        profiles = _reload_profiles_module(base)

        with pytest.raises(ValueError):
            profiles.switch_profile("../../escape-target")


def test_delete_profile_rejects_path_traversal():
    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        base = temp_root / ".hermes"
        (base / "profiles").mkdir(parents=True)
        (temp_root / "escape-target").mkdir()

        profiles = _reload_profiles_module(base)

        with pytest.raises(ValueError):
            profiles.delete_profile_api("../../escape-target")


def test_switch_profile_allows_valid_profile_name():
    with tempfile.TemporaryDirectory() as td:
        temp_root = Path(td)
        base = temp_root / ".hermes"
        profile_dir = base / "profiles" / "demo"
        profile_dir.mkdir(parents=True)

        profiles = _reload_profiles_module(base)
        result = profiles.switch_profile("demo")

        assert result["active"] == "demo"
        assert Path(os.environ["HERMES_HOME"]).resolve() == profile_dir.resolve()
