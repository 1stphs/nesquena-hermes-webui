from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def test_auto_compression_running_sse_is_emitted_from_agent_lifecycle_status():
    src = _read("api/streaming.py")
    start = src.find("def _agent_status_callback")
    assert start != -1, "agent status callback bridge not found"
    end = src.find("# Initialised here", start)
    assert end != -1, "status callback block end marker not found"
    block = src[start:end]

    assert "put('compressing'" in block
    assert "'session_id': session_id" in block
    assert "'message': 'Auto-compressing context to continue...'" in block
    assert "'preflight compression'" in block
    assert "'compressing'" in block
    assert "'compacting context'" in block
    assert "'context too large'" in block
    assert "'status_callback' in _agent_params" in src
    assert "_agent_kwargs['status_callback'] = _agent_status_callback" in src
    assert "agent.status_callback = _agent_kwargs.get('status_callback')" in src
