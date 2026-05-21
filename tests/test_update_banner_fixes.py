"""Tests for update banner fixes — #813 (conflict recovery) and #814 (restart after update).

Covers:
  - conflict error now includes 'conflict: True' flag and actionable git command (#813)
  - successful update returns 'restart_scheduled: True' (#814)
  - _schedule_restart() spawns a daemon thread, does not block (#814)
  - apply_force_update() returns ok on clean reset path (#813)
  - /api/updates/force route exists in routes.py (#813)
  - UI: _showUpdateError and forceUpdate functions exist in ui.js (#813)
  - UI: updateError element and btnForceUpdate element exist in index.html (#813)
  - UI: success toast says 'Restarting' not 'Reloading' (#814)
  - UI: reload timeout bumped to 2500 ms to allow server restart (#814)
"""

import pathlib
import re
import threading
import time
import sys
import os

REPO = pathlib.Path(__file__).parent.parent


def read(rel):
    return (REPO / rel).read_text(encoding='utf-8')


# ── api/updates.py ────────────────────────────────────────────────────────────

class TestUpdateChecker:
    def test_repo_url_strips_only_dot_git_suffix(self, tmp_path, monkeypatch):
        import api.updates as upd

        (tmp_path / '.git').mkdir()

        def fake_run(args, cwd, timeout=10):
            if args[0] == 'fetch':
                return '', True
            if args[:2] == ['rev-parse', '--abbrev-ref']:
                return 'origin/master', True
            if args[:2] == ['rev-list', '--count']:
                return '0', True
            if args[0] == 'merge-base':
                return 'abcdef1234567890', True
            if args[:2] == ['rev-parse', '--short']:
                return 'abcdef1', True
            if args[:2] == ['remote', 'get-url']:
                return 'https://github.com/nesquena/hermes-webui.git', True
            return '', True

        monkeypatch.setattr(upd, '_run_git', fake_run)
        result = upd._check_repo(tmp_path, 'webui')

        assert result['repo_url'] == 'https://github.com/nesquena/hermes-webui'

    def test_repo_url_converts_ssh_and_strips_only_dot_git_suffix(self, tmp_path, monkeypatch):
        import api.updates as upd

        (tmp_path / '.git').mkdir()

        def fake_run(args, cwd, timeout=10):
            if args[0] == 'fetch':
                return '', True
            if args[:2] == ['rev-parse', '--abbrev-ref']:
                return 'origin/main', True
            if args[:2] == ['rev-list', '--count']:
                return '0', True
            if args[0] == 'merge-base':
                return 'abcdef1234567890', True
            if args[:2] == ['rev-parse', '--short']:
                return 'abcdef1', True
            if args[:2] == ['remote', 'get-url']:
                return 'git@github.com:NousResearch/hermes-agent.git', True
            return '', True

        monkeypatch.setattr(upd, '_run_git', fake_run)
        result = upd._check_repo(tmp_path, 'agent')

        assert result['repo_url'] == 'https://github.com/NousResearch/hermes-agent'

    def test_repo_url_strips_dot_git_before_trailing_slashes(self, tmp_path, monkeypatch):
        import api.updates as upd

        (tmp_path / '.git').mkdir()

        def fake_run(args, cwd, timeout=10):
            if args[0] == 'fetch':
                return '', True
            if args[:2] == ['rev-parse', '--abbrev-ref']:
                return 'origin/master', True
            if args[:2] == ['rev-list', '--count']:
                return '2', True
            if args[0] == 'merge-base':
                return 'abcdef1234567890', True
            if args[:2] == ['rev-parse', '--short']:
                return 'abcdef1', True
            if args[:2] == ['remote', 'get-url']:
                return 'https://github.com/nesquena/hermes-webui.git/', True
            return '', True

        monkeypatch.setattr(upd, '_run_git', fake_run)
        result = upd._check_repo(tmp_path, 'webui')

        assert result['repo_url'] == 'https://github.com/nesquena/hermes-webui'


class TestConflictError:
    """#813 — conflict error must include flag + recovery command."""

    def test_conflict_returns_conflict_flag(self, tmp_path, monkeypatch):
        import api.updates as upd

        # Fake a repo with conflict markers in git status output
        (tmp_path / '.git').mkdir()
        conflict_status = 'UU some/file.py'

        calls = []
        def fake_run(args, cwd, timeout=10):
            calls.append(args)
            if args[:2] == ['status', '--porcelain']:
                return conflict_status, True
            if args[0] == 'fetch':
                return '', True
            if args[:2] == ['rev-parse', '--abbrev-ref']:
                return 'origin/master', True
            return '', True

        monkeypatch.setattr(upd, '_run_git', fake_run)
        monkeypatch.setattr(upd, 'REPO_ROOT', tmp_path)
        monkeypatch.setattr(upd, '_AGENT_DIR', tmp_path)

        result = upd.apply_update('webui')
        assert result['ok'] is False
        assert result.get('conflict') is True, "conflict flag must be True"
        assert 'checkout' in result['message'] or 'pull' in result['message'], (
            "conflict message must include recovery command"
        )
        assert 'merge conflict' in result['message'].lower()

    def test_conflict_message_includes_git_command(self, tmp_path, monkeypatch):
        import api.updates as upd

        (tmp_path / '.git').mkdir()

        def fake_run(args, cwd, timeout=10):
            if args[:2] == ['status', '--porcelain']:
                return 'AA conflict.txt', True
            if args[0] == 'fetch':
                return '', True
            if args[:2] == ['rev-parse', '--abbrev-ref']:
                return 'origin/master', True
            return '', True

        monkeypatch.setattr(upd, '_run_git', fake_run)
        monkeypatch.setattr(upd, 'REPO_ROOT', tmp_path)
        monkeypatch.setattr(upd, '_AGENT_DIR', tmp_path)

        result = upd.apply_update('agent')
        # Message must be actionable — should mention git checkout or pull
        msg = result['message']
        assert 'git' in msg.lower(), f"message should mention git: {msg}"


class TestScheduleRestart:
    """#814 — _schedule_restart must exist and be non-blocking."""

    def test_schedule_restart_exists(self):
        from api.updates import _schedule_restart
        assert callable(_schedule_restart)

    def test_schedule_restart_is_nonblocking(self, monkeypatch):
        """_schedule_restart() must return immediately (spawns daemon thread)."""
        import api.updates as upd

        execv_called = []

        def fake_execv(exe, args):
            execv_called.append((exe, args))

        # Monkeypatch os.execv inside the module's thread closure
        import os as _os
        original_execv = _os.execv

        monkeypatch.setattr(_os, 'execv', fake_execv)

        start = time.monotonic()
        upd._schedule_restart(delay=0.05)
        elapsed = time.monotonic() - start

        assert elapsed < 0.5, f"_schedule_restart must return immediately, took {elapsed:.2f}s"
        # Give the thread time to call execv
        time.sleep(0.2)
        assert execv_called, "_schedule_restart must eventually call os.execv"


class TestApplyUpdateRestartSafety:
    """Self-update must not re-exec while chat streams are active."""

    def test_apply_update_refuses_when_stream_active(self, tmp_path, monkeypatch):
        import queue
        import api.updates as upd
        from api.config import STREAMS, STREAMS_LOCK

        (tmp_path / '.git').mkdir()
        monkeypatch.setattr(upd, 'REPO_ROOT', tmp_path)
        monkeypatch.setattr(upd, '_AGENT_DIR', tmp_path)
        called = []
        monkeypatch.setattr(upd, '_run_git', lambda *a, **k: (called.append(a) or ('', True)))
        monkeypatch.setattr(upd, '_schedule_restart', lambda delay=2.0: (_ for _ in ()).throw(AssertionError('must not restart')))

        with STREAMS_LOCK:
            old = dict(STREAMS)
            STREAMS.clear()
            STREAMS['stream_active'] = queue.Queue()
        try:
            result = upd.apply_update('webui')
        finally:
            with STREAMS_LOCK:
                STREAMS.clear()
                STREAMS.update(old)

        assert result['ok'] is False
        assert result.get('active_streams') == 1
        assert result.get('restart_blocked') is True
        assert 'active chat stream' in result['message']
        assert called == []

    def test_force_update_refuses_when_stream_active(self, tmp_path, monkeypatch):
        import queue
        import api.updates as upd
        from api.config import STREAMS, STREAMS_LOCK

        (tmp_path / '.git').mkdir()
        monkeypatch.setattr(upd, 'REPO_ROOT', tmp_path)
        monkeypatch.setattr(upd, '_AGENT_DIR', tmp_path)
        monkeypatch.setattr(upd, '_run_git', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not run git')))
        monkeypatch.setattr(upd, '_schedule_restart', lambda delay=2.0: (_ for _ in ()).throw(AssertionError('must not restart')))

        with STREAMS_LOCK:
            old = dict(STREAMS)
            STREAMS.clear()
            STREAMS['stream_active'] = queue.Queue()
        try:
            result = upd.apply_force_update('agent')
        finally:
            with STREAMS_LOCK:
                STREAMS.clear()
                STREAMS.update(old)

        assert result['ok'] is False
        assert result.get('active_streams') == 1
        assert result.get('restart_blocked') is True
        assert 'active chat stream' in result['message']


class TestSuccessfulUpdateReturnsRestartScheduled:
    """#814 — successful apply_update must return restart_scheduled: True."""

    def test_apply_update_returns_restart_scheduled(self, tmp_path, monkeypatch):
        import api.updates as upd

        (tmp_path / '.git').mkdir()

        def fake_run(args, cwd, timeout=10):
            if args[0] == 'fetch':
                return '', True
            if args[:2] == ['status', '--porcelain']:
                return '', True   # clean tree
            if args[:2] == ['rev-parse', '--abbrev-ref']:
                return 'origin/master', True
            if args[0] == 'pull':
                return 'Already up to date.', True
            return '', True

        monkeypatch.setattr(upd, '_run_git', fake_run)
        monkeypatch.setattr(upd, 'REPO_ROOT', tmp_path)
        monkeypatch.setattr(upd, '_AGENT_DIR', tmp_path)
        # Don't actually restart
        monkeypatch.setattr(upd, '_schedule_restart', lambda delay=2.0: None)

        result = upd.apply_update('webui')
        assert result['ok'] is True
        assert result.get('restart_scheduled') is True, (
            "successful update must set restart_scheduled: True"
        )


class TestApplyForceUpdate:
    """#813 — apply_force_update must reset hard and return ok."""

    def test_apply_force_update_ok(self, tmp_path, monkeypatch):
        import api.updates as upd

        (tmp_path / '.git').mkdir()
        ran = []

        def fake_run(args, cwd, timeout=10):
            ran.append(args)
            if args[0] == 'fetch':
                return '', True
            if args[:2] == ['rev-parse', '--abbrev-ref']:
                return 'origin/master', True
            if args[0] == 'checkout':
                return '', True
            if args[0] == 'reset':
                return '', True
            return '', True

        monkeypatch.setattr(upd, '_run_git', fake_run)
        monkeypatch.setattr(upd, 'REPO_ROOT', tmp_path)
        monkeypatch.setattr(upd, '_AGENT_DIR', tmp_path)
        monkeypatch.setattr(upd, '_schedule_restart', lambda delay=2.0: None)

        result = upd.apply_force_update('webui')
        assert result['ok'] is True
        assert result.get('restart_scheduled') is True

        git_cmds = [r[0] for r in ran]
        assert 'reset' in git_cmds, "force update must call git reset --hard"
        assert 'checkout' in git_cmds, "force update must call git checkout . to clear conflicts"

    def test_apply_force_update_rejects_unknown_target(self, tmp_path, monkeypatch):
        import api.updates as upd
        monkeypatch.setattr(upd, 'REPO_ROOT', tmp_path)
        monkeypatch.setattr(upd, '_AGENT_DIR', tmp_path)
        result = upd.apply_force_update('invalid')
        assert result['ok'] is False


# ── api/routes.py ─────────────────────────────────────────────────────────────

class TestForceUpdateRoute:
    """#813 — /api/updates/force route must exist in routes.py."""

    def test_force_route_exists(self):
        from tests.route_source import read_route_sources
        src = read_route_sources()
        assert '"/api/updates/force"' in src, (
            "routes.py must handle POST /api/updates/force"
        )
        assert 'apply_force_update' in src, (
            "routes.py must import and call apply_force_update"
        )


# ── static/ui.js ──────────────────────────────────────────────────────────────

# ── static/index.html ─────────────────────────────────────────────────────────

# ── Regression: sequential webui+agent update — restart coordination ──────────

class TestSequentialUpdateRestartCoordination:
    """Regression guard for the two-target race: when both webui and agent
    have updates, the client POSTs them sequentially (webui → agent). The
    first update's success schedules a restart timer; without coordination
    that timer fires while the second update's git-pull is still running,
    killing it mid-stream and leaving the second repo partial.

    Fix: `_schedule_restart` must acquire `_apply_lock` before calling
    `os.execv`, so a pending second update always completes first.
    """

    def test_schedule_restart_waits_for_apply_lock(self, monkeypatch):
        """The restart thread must wait for any in-flight update before
        calling execv. Exercised by holding _apply_lock from another thread
        and verifying execv is delayed until the lock is released."""
        import api.updates as upd
        import threading as _th
        import time as _t

        execv_called = _th.Event()
        execv_time = []

        def fake_execv(exe, args):
            execv_time.append(_t.monotonic())
            execv_called.set()

        monkeypatch.setattr(os, 'execv', fake_execv)

        # Hold _apply_lock from another thread (simulating an in-flight
        # second update) for 0.4 s.
        release_time = []
        lock_held = _th.Event()

        def holder():
            with upd._apply_lock:
                lock_held.set()
                _t.sleep(0.4)
                release_time.append(_t.monotonic())

        holder_thread = _th.Thread(target=holder, daemon=True)
        holder_thread.start()
        lock_held.wait(timeout=2)

        # Schedule a restart with a short delay. The lock is held;
        # the restart thread should block on it.
        upd._schedule_restart(delay=0.05)
        _t.sleep(0.15)
        assert not execv_called.is_set(), (
            "execv called while _apply_lock was still held by another "
            "thread — restart must wait for in-flight updates to finish"
        )

        # Let the holder release.
        holder_thread.join(timeout=2)
        assert release_time, "holder didn't release the lock"

        # execv should fire shortly after the lock release.
        assert execv_called.wait(timeout=2), (
            "execv never fired after _apply_lock was released"
        )
        assert execv_time[0] >= release_time[0], (
            f"execv fired before lock was released "
            f"(execv={execv_time[0]}, release={release_time[0]})"
        )

    def test_schedule_restart_still_fires_when_no_update_in_flight(self, monkeypatch):
        """Sanity: with nothing holding the lock, restart still fires promptly."""
        import api.updates as upd
        import time as _t

        execv_called = []
        def fake_execv(exe, args):
            execv_called.append(True)
        monkeypatch.setattr(os, 'execv', fake_execv)

        upd._schedule_restart(delay=0.05)
        _t.sleep(0.25)
        assert execv_called, (
            "restart must still fire when _apply_lock is free"
        )


# ── Regression: force button reset on retry ──────────────────────────────────

# ── #785: Manual 'Check for Updates' button ───────────────────────────────────
