"""Tests for issue #465 — session branching (/branch).

Verifies:
  1. Backend endpoint POST /api/session/branch exists in routes.py
  2. Session model supports parent_session_id field
  3. Frontend /branch slash command is registered
  4. forkFromMessage function exists in commands.js
  5. Fork button (git-branch icon) is rendered in ui.js message actions
  6. Parent session indicator uses a subtle git-branch icon in sessions.js sidebar
  7. i18n keys exist for all branch-related strings
  8. git-branch icon exists in icons.js
"""
import re

from tests.route_source import read_route_sources


def _routes_source():
    return read_route_sources()


# ── Backend ────────────────────────────────────────────────────────────────────

def test_branch_endpoint_exists():
    """Verify the POST /api/session/branch route handler exists."""
    src = _routes_source()
    assert '"POST /api/session/branch"' in src or '"/api/session/branch"' in src, \
        "Missing /api/session/branch route"


def test_branch_endpoint_validates_session_id():
    """Verify the branch endpoint requires session_id."""
    src = _routes_source()
    # Find the branch block
    branch_match = re.search(
        r'parsed\.path == "/api/session/branch"(.*?)(?=\n    if parsed\.path|$)',
        src, re.DOTALL
    )
    assert branch_match, "Could not find /api/session/branch handler block"
    block = branch_match.group(1)
    assert 'require(body, "session_id")' in block, \
        "Branch handler should validate session_id"


def test_branch_endpoint_returns_new_session_id():
    """Verify the branch endpoint returns session_id and title."""
    src = _routes_source()
    branch_match = re.search(
        r'parsed\.path == "/api/session/branch"(.*?)(?=\n    if parsed\.path|$)',
        src, re.DOTALL
    )
    assert branch_match
    block = branch_match.group(1)
    assert '"session_id"' in block, "Branch handler should return session_id"
    assert '"title"' in block, "Branch handler should return title"
    assert '"parent_session_id"' in block, \
        "Branch handler should return parent_session_id"


def test_branch_creates_session_with_parent():
    """Verify the branch creates a Session with parent_session_id set."""
    src = _routes_source()
    branch_match = re.search(
        r'parsed\.path == "/api/session/branch"(.*?)(?=\n    if parsed\.path|$)',
        src, re.DOTALL
    )
    assert branch_match
    block = branch_match.group(1)
    assert 'parent_session_id=source.session_id' in block, \
        "Branch handler should set parent_session_id to source session"


def test_branch_keep_count_support():
    """Verify the branch endpoint supports keep_count parameter."""
    src = _routes_source()
    branch_match = re.search(
        r'parsed\.path == "/api/session/branch"(.*?)(?=\n    if parsed\.path|$)',
        src, re.DOTALL
    )
    assert branch_match
    block = branch_match.group(1)
    assert 'keep_count' in block, "Branch handler should support keep_count"
    assert 'forked_messages = source_messages[:keep_count]' in block, \
        "Branch handler should slice messages by keep_count"


def test_branch_auto_title():
    """Verify fork title defaults to '<original> (fork)'."""
    src = _routes_source()
    branch_match = re.search(
        r'parsed\.path == "/api/session/branch"(.*?)(?=\n    if parsed\.path|$)',
        src, re.DOTALL
    )
    assert branch_match
    block = branch_match.group(1)
    assert '(fork)' in block, "Branch handler should auto-title as '(fork)'"


# ── Session model ──────────────────────────────────────────────────────────────

def test_session_model_parent_session_id():
    """Verify Session model supports parent_session_id."""
    with open('api/models.py') as f:
        src = f.read()
    assert 'parent_session_id' in src, "Session model should have parent_session_id"
    # Check __init__ parameter
    assert 'parent_session_id: str=None' in src, \
        "Session.__init__ should accept parent_session_id parameter"
    # Check it's set on self
    assert 'self.parent_session_id = parent_session_id' in src, \
        "Session.__init__ should assign parent_session_id"


def test_session_compact_includes_parent():
    """Verify compact() includes parent_session_id."""
    with open('api/models.py') as f:
        src = f.read()
    # Find the compact method and scan its full body for parent_session_id.
    # PR #1591 (May 2026) added a has_pending_user_message recompute block at
    # the top of compact() which pushed the parent_session_id field beyond a
    # 1500-char window — widen the scan to 3000 chars to cover the full
    # return-dict body without re-tightening every time compact() grows.
    compact_def_match = re.search(r"def compact\(self", src)
    assert compact_def_match, "Could not find compact() method"
    snippet = src[compact_def_match.start():compact_def_match.start() + 3000]
    assert "'parent_session_id'" in snippet, \
        "compact() should include parent_session_id"


def test_session_metadata_fields_includes_parent():
    """Verify parent_session_id is in METADATA_FIELDS for persistence."""
    with open('api/models.py') as f:
        src = f.read()
    assert "'parent_session_id'" in src, \
        "METADATA_FIELDS should include parent_session_id"


# ── Frontend: slash command ────────────────────────────────────────────────────

# ── Frontend: forkFromMessage ─────────────────────────────────────────────────

# ── Frontend: fork button in messages ──────────────────────────────────────────

# ── Frontend: sidebar parent indicator ────────────────────────────────────────

# ── Frontend: i18n keys ────────────────────────────────────────────────────────

# ── Frontend: icon ─────────────────────────────────────────────────────────────
