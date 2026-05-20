"""Log endpoint handlers re-exported by api.routes."""

import os
from pathlib import Path
from urllib.parse import parse_qs

from api.routes_handlers._base import _routes_binding

_LOG_FILE_WHITELIST = {
    "agent": "agent.log",
    "errors": "errors.log",
    "gateway": "gateway.log",
}
_LOG_TAIL_VALUES = {100, 200, 500, 1000}
_LOG_DEFAULT_TAIL = 200
_LOG_MAX_BYTES = 4 * 1024 * 1024


def _normalize_logs_tail(raw_tail) -> int:
    try:
        tail = int(str(raw_tail or "").strip())
    except (TypeError, ValueError):
        return _LOG_DEFAULT_TAIL
    return tail if tail in _LOG_TAIL_VALUES else _LOG_DEFAULT_TAIL


def _handle_logs(handler, parsed) -> bool:
    """Return a bounded tail window for an active-profile Hermes log file."""
    query = parse_qs(parsed.query)
    file_key = (query.get("file", ["agent"])[0] or "agent").strip().lower()
    filename = _LOG_FILE_WHITELIST.get(file_key)
    if not filename:
        return _routes_binding("bad")(handler, "Unknown log file", status=400)

    tail = _normalize_logs_tail(query.get("tail", [None])[0])
    try:
        from api.profiles import get_active_hermes_home

        hermes_home = Path(get_active_hermes_home()).expanduser()
    except Exception:
        hermes_home = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes")).expanduser()

    log_dir = hermes_home / "logs"
    log_path = log_dir / filename
    try:
        # Defense in depth: the filename is hardcoded above, but keep the final
        # path anchored under the active profile's logs directory.
        if log_path.resolve(strict=False).parent != log_dir.resolve(strict=False):
            return _routes_binding("bad")(handler, "Invalid log file", status=400)
        if not log_path.exists() or not log_path.is_file():
            return _routes_binding("j")(
                handler,
                {
                    "file": file_key,
                    "tail": tail,
                    "lines": [],
                    "truncated": False,
                    "total_bytes": 0,
                    "mtime": None,
                    "hint": f"Log file for {file_key} not found yet.",
                },
            )
        st = log_path.stat()
        total_bytes = int(st.st_size)
        read_bytes = min(total_bytes, _LOG_MAX_BYTES)
        with log_path.open("rb") as fh:
            if total_bytes > read_bytes:
                fh.seek(total_bytes - read_bytes)
            raw = fh.read(read_bytes)
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()[-tail:]
        return _routes_binding("j")(
            handler,
            {
                "file": file_key,
                "tail": tail,
                "lines": lines,
                "truncated": total_bytes > read_bytes,
                "total_bytes": total_bytes,
                "mtime": st.st_mtime,
                "hint": "",
            },
        )
    except Exception as exc:
        _routes_binding("logger").exception("Failed to read whitelisted log file %s", file_key)
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(exc), status=500)
