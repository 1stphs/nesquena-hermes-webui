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


def test_no_orphan_wiki_i18n_keys():
    """PR #1342 leaked 9 unused `wiki_*` keys (wiki_panel_title, wiki_status_label,
    wiki_entry_count, wiki_last_modified, wiki_not_available, wiki_enabled,
    wiki_disabled, wiki_toggle_failed, wiki_panel_desc) into static/i18n.js
    from a different branch. Zero references existed outside i18n.js. They
    were stripped by Opus pre-release follow-up. This test pins that they
    don't return."""
    i18n_src = (REPO / "static" / "i18n.js").read_text(encoding="utf-8")
    # If wiki_* keys are added in the future, they MUST have at least one
    # reference outside i18n.js. Until then, this test fails loudly.
    forbidden_keys = [
        "wiki_panel_title",
        "wiki_panel_desc",
        "wiki_status_label",
        "wiki_entry_count",
        "wiki_last_modified",
        "wiki_not_available",
        "wiki_enabled",
        "wiki_disabled",
        "wiki_toggle_failed",
    ]
    for key in forbidden_keys:
        assert key not in i18n_src, (
            f"{key!r} is back in static/i18n.js but no consumer uses it. "
            "If you're adding wiki UI, also wire it up in the JS / panel HTML / "
            "Python so the key is actually used. See v0.50.253 Opus pre-release "
            "review."
        )
