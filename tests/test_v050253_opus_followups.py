"""Regression tests for v0.50.253 Opus pre-release follow-ups.

Three small follow-ups landed alongside the main batch:

1. /branch endpoint rejects non-string session_id with a 400 (instead of
   crashing with a generic 500 from get_session() raising TypeError).
2. /branch endpoint rejects negative keep_count (Python slicing semantics
   would otherwise produce "all but last N" rather than a forward prefix).
3. PR #1342 leaked 9 unused `wiki_*` i18n keys from a different branch.
   These were stripped — assert they don't come back.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


# ── 1 + 2: /branch endpoint validation ────────────────────────────────────────


def test_branch_endpoint_rejects_non_string_session_id():
    """The handler must reject a non-string session_id with a 400 before
    reaching get_session()."""
    from tests.route_test_utils import invoke_route

    response = invoke_route("post", "/api/session/branch", body={"session_id": 123})

    assert response.status == 400
    assert response.body == {"error": "session_id must be a string"}


def test_branch_endpoint_rejects_negative_keep_count():
    """The handler must reject keep_count < 0 with a 400. Otherwise Python
    slicing would produce a "all but last N" semantic instead of a forward
    prefix, which is confusing fork behavior."""
    import api.routes as routes
    from tests.route_test_utils import invoke_route

    class FakeSession:
        title = "Source"
        workspace = "/tmp"
        model = "test"
        profile = "default"
        session_id = "source"
        messages = [{"role": "user", "content": "hello"}]

    original = routes.get_session
    routes.get_session = lambda sid: FakeSession()
    try:
        response = invoke_route(
            "post",
            "/api/session/branch",
            body={"session_id": "source", "keep_count": -1},
        )
    finally:
        routes.get_session = original

    assert response.status == 400
    assert response.body == {"error": "keep_count must be non-negative"}


# ── 3: orphan wiki_* i18n keys must not return ────────────────────────────────
