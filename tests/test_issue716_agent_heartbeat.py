"""Regression coverage for #716 Hermes agent/gateway heartbeat monitor."""

from __future__ import annotations

import pathlib

from tests.route_source import read_route_sources


REPO_ROOT = pathlib.Path(__file__).parent.parent
ROUTE_SOURCES = read_route_sources()


class _FakeGatewayStatus:
    def __init__(self, runtime_status, running_pid):
        self._runtime_status = runtime_status
        self._running_pid = running_pid

    def read_runtime_status(self):
        return self._runtime_status

    def get_running_pid(self, cleanup_stale=False):
        assert cleanup_stale is False
        return self._running_pid


def _runtime_status(**overrides):
    payload = {
        "gateway_state": "running",
        "updated_at": "2026-05-04T12:00:00+00:00",
        "active_agents": 2,
        "platforms": {
            "discord": {"state": "connected"},
            "telegram": {"state": "starting"},
        },
        # Sensitive/raw process fields that must never reach the browser.
        "pid": 12345,
        "argv": ["hermes", "gateway", "--token", "secret-token"],
        "command": "hermes gateway --token secret-token",
        "executable": "/home/user/.hermes/hermes-agent/venv/bin/python",
        "env": {"API_KEY": "secret"},
    }
    payload.update(overrides)
    return payload


def test_agent_health_payload_alive_uses_safe_runtime_details(monkeypatch):
    from api import agent_health

    monkeypatch.setattr(
        agent_health,
        "_gateway_status_module",
        lambda: _FakeGatewayStatus(_runtime_status(), running_pid=12345),
    )

    payload = agent_health.build_agent_health_payload()

    assert payload["alive"] is True
    assert payload["checked_at"]
    assert payload["details"] == {
        "state": "alive",
        "gateway_state": "running",
        "updated_at": "2026-05-04T12:00:00+00:00",
        "active_agents": 2,
        "platform_count": 2,
        "platform_states": {"connected": 1, "starting": 1},
    }
    rendered = repr(payload)
    assert "secret-token" not in rendered
    assert "API_KEY" not in rendered
    assert "argv" not in rendered
    assert "command" not in rendered
    assert "executable" not in rendered
    assert "pid" not in payload["details"]


def test_agent_health_payload_down_when_gateway_metadata_exists_but_no_process(monkeypatch):
    from api import agent_health

    monkeypatch.setattr(
        agent_health,
        "_gateway_status_module",
        lambda: _FakeGatewayStatus(_runtime_status(gateway_state="stale"), running_pid=None),
    )

    payload = agent_health.build_agent_health_payload()

    assert payload["alive"] is False
    assert payload["details"]["state"] == "down"
    assert payload["details"]["reason"] == "gateway_not_running"
    assert payload["details"]["gateway_state"] == "stale"


def test_agent_health_payload_unknown_when_gateway_is_not_configured(monkeypatch):
    from api import agent_health

    monkeypatch.setattr(
        agent_health,
        "_gateway_status_module",
        lambda: _FakeGatewayStatus(runtime_status=None, running_pid=None),
    )

    payload = agent_health.build_agent_health_payload()

    assert payload["alive"] is None
    assert payload["details"] == {"state": "unknown", "reason": "gateway_not_configured"}


def test_agent_health_route_is_registered_with_tri_state_payload_shape():
    assert 'parsed.path == "/api/health/agent"' in ROUTE_SOURCES
    assert "build_agent_health_payload()" in ROUTE_SOURCES
    src = (REPO_ROOT / "api" / "agent_health.py").read_text(encoding="utf-8")
    assert '"alive"' in src
    assert '"checked_at"' in src
    assert '"details"' in src


def test_agent_health_backend_does_not_use_shell_or_expose_raw_process_fields():
    src = (REPO_ROOT / "api" / "agent_health.py").read_text(encoding="utf-8")
    assert "import subprocess" not in src
    assert "import psutil" not in src
    for private_field in ("argv", "command", "executable", "env"):
        assert f'details["{private_field}"]' not in src
        assert f"details['{private_field}']" not in src
