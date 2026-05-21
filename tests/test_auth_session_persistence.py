"""Regression tests for API auth session persistence across process restarts."""

import importlib
import json
import time

import pytest

from api import auth


@pytest.fixture()
def isolatedAuthState(tmp_path):
    """Bind api.auth session files to a temp STATE_DIR for this test."""
    import api.config as config

    originalStateDir = config.STATE_DIR
    originalAuthStateDir = auth.STATE_DIR
    originalSessionsFile = auth._SESSIONS_FILE
    originalSessions = dict(auth._sessions)
    config.STATE_DIR = tmp_path
    auth.STATE_DIR = tmp_path
    auth._SESSIONS_FILE = tmp_path / ".sessions.json"
    auth._sessions.clear()
    try:
        yield tmp_path
    finally:
        auth._sessions.clear()
        auth._sessions.update(originalSessions)
        auth._SESSIONS_FILE = originalSessionsFile
        auth.STATE_DIR = originalAuthStateDir
        config.STATE_DIR = originalStateDir


def reload_auth_with_state(state_dir):
    """Reload api.auth as a process-restart approximation."""
    import api.config as config

    originalStateDir = config.STATE_DIR
    config.STATE_DIR = state_dir
    try:
        importlib.reload(auth)
    finally:
        config.STATE_DIR = originalStateDir
    auth._SESSIONS_FILE = state_dir / ".sessions.json"


def test_session_survives_restart(isolatedAuthState):
    cookie = auth.create_session()

    assert auth.verify_session(cookie)
    reload_auth_with_state(isolatedAuthState)

    assert auth.verify_session(cookie)


def test_invalidated_session_does_not_survive_restart(isolatedAuthState):
    cookie = auth.create_session()

    auth.invalidate_session(cookie)
    reload_auth_with_state(isolatedAuthState)

    assert not auth.verify_session(cookie)


def test_expired_sessions_pruned_on_load(isolatedAuthState):
    now = time.time()
    (isolatedAuthState / ".sessions.json").write_text(
        json.dumps({"expired_token": now - 10, "valid_token": now + 3600}),
        encoding="utf-8",
    )

    reload_auth_with_state(isolatedAuthState)

    assert "expired_token" not in auth._sessions
    assert "valid_token" in auth._sessions


def test_sessions_file_permissions(isolatedAuthState):
    auth.create_session()

    sessionsFile = isolatedAuthState / ".sessions.json"

    assert sessionsFile.exists()
    assert oct(sessionsFile.stat().st_mode & 0o777) == oct(0o600)


def test_malformed_sessions_file_starts_fresh(isolatedAuthState):
    (isolatedAuthState / ".sessions.json").write_text("not valid json {{{{", encoding="utf-8")

    reload_auth_with_state(isolatedAuthState)

    assert auth._sessions == {}


def test_sessions_file_wrong_type_starts_fresh(isolatedAuthState):
    (isolatedAuthState / ".sessions.json").write_text(json.dumps(["list"]), encoding="utf-8")

    reload_auth_with_state(isolatedAuthState)

    assert auth._sessions == {}
