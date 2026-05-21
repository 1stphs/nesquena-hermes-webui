import os
import pathlib


REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8")


def test_terminal_routes_are_registered():
    from tests.route_source import read_route_sources
    routes = read_route_sources()
    for path in (
        "/api/terminal/start",
        "/api/terminal/input",
        "/api/terminal/output",
        "/api/terminal/resize",
        "/api/terminal/close",
    ):
        assert path in routes


def test_terminal_process_does_not_mutate_global_terminal_cwd(tmp_path, monkeypatch):
    from api.terminal import close_terminal, start_terminal

    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    sid = "test-terminal-env"
    term = start_terminal(sid, tmp_path, rows=8, cols=40, restart=True)
    try:
        assert term.workspace == str(tmp_path.resolve())
        assert os.environ.get("TERMINAL_CWD") is None
    finally:
        close_terminal(sid)


def test_terminal_output_preserves_control_sequences_for_xterm():
    import codecs
    from api.terminal import _decode_terminal_output

    raw = "\x1b[?2004h$ \x1b[32mhello\x1b[0m\n"
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    assert _decode_terminal_output(decoder, raw.encode()) == raw
