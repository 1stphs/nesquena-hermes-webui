"""
Sprint 10 Tests: server.py split, cancel endpoint, cron history, tool card polish.
"""
import json, pathlib, urllib.error, urllib.request, urllib.parse
REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()

from tests._pytest_port import BASE

def get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read()), r.status

def post(path, body=None):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                  headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        return json.loads(e.read()), e.code

def make_session(created_list):
    d, _ = post("/api/session/new", {})
    sid = d["session"]["session_id"]
    created_list.append(sid)
    return sid

# ── server.py split: api/ modules served / importable ─────────────────────

def test_health_still_works(cleanup_test_sessions):
    data, status = get("/health")
    assert status == 200
    assert data["status"] == "ok"
    assert "uptime_seconds" in data
    assert "active_streams" in data

def test_api_modules_exist(cleanup_test_sessions):
    """All api/ module files must exist on disk."""
    base = REPO_ROOT / "api"
    for mod in ["__init__.py", "config.py", "helpers.py", "models.py",
                "workspace.py", "upload.py", "streaming.py"]:
        assert (base / mod).exists(), f"Missing api/{mod}"

def test_server_py_under_750_lines(cleanup_test_sessions):
    """server.py should be under 750 lines after the split."""
    lines = len((REPO_ROOT / "server.py").read_text().splitlines())
    assert lines < 750, f"server.py is {lines} lines -- split may not have landed"

def test_api_config_has_cancel_flags(cleanup_test_sessions):
    src = (REPO_ROOT / "api/config.py").read_text()
    assert "CANCEL_FLAGS" in src
    assert "STREAMS" in src

def test_session_crud_still_works(cleanup_test_sessions):
    """Full session lifecycle works after split."""
    created = []
    sid = make_session(created)
    data, status = get(f"/api/session?session_id={urllib.parse.quote(sid)}")
    assert status == 200
    assert data["session"]["session_id"] == sid
    post("/api/session/delete", {"session_id": sid})

# ── Cancel endpoint ────────────────────────────────────────────────────────

def test_cancel_requires_stream_id(cleanup_test_sessions):
    try:
        data, status = get("/api/chat/cancel")
        assert status == 400
    except urllib.error.HTTPError as e:
        assert e.code == 400

def test_cancel_nonexistent_stream(cleanup_test_sessions):
    data, status = get("/api/chat/cancel?stream_id=nonexistent_xyz")
    assert status == 200
    assert data["ok"] is True
    assert data["cancelled"] is False

# ── Cron history ───────────────────────────────────────────────────────────

def test_crons_output_limit_param(cleanup_test_sessions):
    """Server accepts limit parameter > 1."""
    data, status = get("/api/crons/output?job_id=nonexistent&limit=20")
    # 404 or 200 with empty -- both acceptable for nonexistent job
    assert status in (200, 404)

def test_cron_output_window_preserves_response_after_large_prompt(cleanup_test_sessions):
    """Large skill dumps before ## Response must not hide the useful output."""
    from api.routes import _cron_output_content_window

    content = (
        "Job metadata\n"
        "## Prompt\n"
        + ("skill dump\n" * 1200)
        + "user prompt\n"
        "## Response\n"
        "actual useful cron result\n"
    )

    window = _cron_output_content_window(content, limit=8000)

    assert len(window) <= 8000
    assert "## Response" in window
    assert "actual useful cron result" in window
    assert "Job metadata" in window


def test_cron_output_window_without_response_uses_tail(cleanup_test_sessions):
    """Without a response marker, keep the newest tail rather than old prompt text."""
    from api.routes import _cron_output_content_window

    content = "old prompt\n" + ("x" * 9000) + "tail result"

    window = _cron_output_content_window(content, limit=8000)

    assert len(window) == 8000
    assert window.endswith("tail result")
    assert "old prompt" not in window

# ── Tool card polish ───────────────────────────────────────────────────────
