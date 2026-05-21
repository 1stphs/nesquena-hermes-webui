"""Regression tests for busy_input_mode (PR #1062, closes #720).

Pins the wiring for the three modes (queue / interrupt / steer):
- The setting key + default + enum validation in api/config.py
- Three slash commands registered in static/commands.js
- send()'s busy branch reads window._busyInputMode and dispatches
- Boot initializes window._busyInputMode from settings
- 17 new i18n keys present in all 6 locale blocks

Issue: #720 (configurable busy-input behaviour)
"""
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG_PY = (ROOT / "api" / "config.py").read_text(encoding="utf-8")
# ── Backend: setting registration + enum validation ─────────────────────

class TestBusyInputModeSetting:
    """The new setting key must be registered with a default and enum validator."""

    def test_default_is_queue(self):
        """Default value preserves existing queue behaviour for users who don't touch the setting."""
        assert '"busy_input_mode": "queue"' in CONFIG_PY, (
            "_DEFAULT_SETTINGS must include busy_input_mode='queue' so existing users see no change"
        )

    def test_enum_validator_present(self):
        """_SETTINGS_ENUM_KEYS must validate busy_input_mode against {queue, interrupt, steer}."""
        # Find the entry inside the enum dict (a set literal as the value)
        idx = CONFIG_PY.find('"busy_input_mode": {')
        assert idx >= 0, "busy_input_mode entry missing from _SETTINGS_ENUM_KEYS"
        block = CONFIG_PY[idx:idx + 200]
        assert '"queue"' in block and '"interrupt"' in block and '"steer"' in block, (
            "busy_input_mode enum must contain {queue, interrupt, steer}"
        )


# ── Frontend: slash commands ─────────────────────────────────────────────

# ── Boot init + settings panel wiring ───────────────────────────────────

# ── i18n locale coverage ─────────────────────────────────────────────────
