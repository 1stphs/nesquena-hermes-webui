"""Memory endpoint handlers re-exported by api.routes."""

from pathlib import Path

from api.routes_handlers._base import _routes_binding


def _handle_memory_read(handler):
    try:
        from api.profiles import get_active_hermes_home

        mem_dir = get_active_hermes_home() / "memories"
    except ImportError:
        mem_dir = Path.home() / ".hermes" / "memories"
    mem_file = mem_dir / "MEMORY.md"
    user_file = mem_dir / "USER.md"
    memory = (
        mem_file.read_text(encoding="utf-8", errors="replace")
        if mem_file.exists()
        else ""
    )
    user = (
        user_file.read_text(encoding="utf-8", errors="replace")
        if user_file.exists()
        else ""
    )
    j = _routes_binding("j")
    redact_text = _routes_binding("_redact_text")
    return j(
        handler,
        {
            "memory": redact_text(memory),
            "user": redact_text(user),
            "memory_path": str(mem_file),
            "user_path": str(user_file),
            "memory_mtime": mem_file.stat().st_mtime if mem_file.exists() else None,
            "user_mtime": user_file.stat().st_mtime if user_file.exists() else None,
        },
    )


def _handle_memory_write(handler, body):
    try:
        _routes_binding("require")(body, "section", "content")
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))
    try:
        from api.profiles import get_active_hermes_home

        mem_dir = get_active_hermes_home() / "memories"
    except ImportError:
        mem_dir = Path.home() / ".hermes" / "memories"
    mem_dir.mkdir(parents=True, exist_ok=True)
    section = body["section"]
    if section == "memory":
        target = mem_dir / "MEMORY.md"
    elif section == "user":
        target = mem_dir / "USER.md"
    else:
        return _routes_binding("bad")(handler, 'section must be "memory" or "user"')
    target.write_text(body["content"], encoding="utf-8")
    return _routes_binding("j")(handler, {"ok": True, "section": section, "path": str(target)})
