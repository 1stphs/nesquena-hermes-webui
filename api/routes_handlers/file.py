"""File endpoint handlers re-exported by api.routes."""

import shutil
from pathlib import Path
from urllib.parse import parse_qs

from api.routes_handlers._base import _routes_binding


def _handle_list_dir(handler, parsed):
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    if not sid:
        return _routes_binding("bad")(handler, "session_id is required")
    try:
        s = _routes_binding("get_session")(sid)
        workspace = s.workspace
    except KeyError:
        # Fallback for CLI sessions not loaded in WebUI memory
        try:
            cli_meta = None
            for cs in _routes_binding("get_cli_sessions")():
                if cs["session_id"] == sid:
                    cli_meta = cs
                    break
            if not cli_meta:
                return _routes_binding("bad")(handler, "Session not found", 404)
            workspace = cli_meta.get("workspace", "")
        except Exception:
            return _routes_binding("bad")(handler, "Session not found", 404)
    try:
        return _routes_binding("j")(
            handler,
            {
                "entries": _routes_binding("list_dir")(
                    Path(workspace),
                    qs.get("path", ["."])[0],
                ),
                "path": qs.get("path", ["."])[0],
            },
        )
    except (FileNotFoundError, ValueError) as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 404)


def _handle_file_read(handler, parsed):
    qs = parse_qs(parsed.query)
    sid = qs.get("session_id", [""])[0]
    if not sid:
        return _routes_binding("bad")(handler, "session_id is required")
    try:
        s = _routes_binding("get_session")(sid)
    except KeyError:
        return _routes_binding("bad")(handler, "Session not found", 404)
    rel = qs.get("path", [""])[0]
    if not rel:
        return _routes_binding("bad")(handler, "path is required")
    try:
        return _routes_binding("j")(
            handler,
            _routes_binding("read_file_content")(Path(s.workspace), rel),
        )
    except (FileNotFoundError, ValueError) as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e), 404)


def _handle_file_delete(handler, body):
    try:
        _routes_binding("require")(body, "session_id", "path")
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))
    try:
        s = _routes_binding("get_session")(body["session_id"])
    except KeyError:
        return _routes_binding("bad")(handler, "Session not found", 404)
    try:
        target = _routes_binding("safe_resolve")(Path(s.workspace), body["path"])
        if not target.exists():
            return _routes_binding("bad")(handler, "File not found", 404)
        if target.is_dir():
            if not body.get("recursive"):
                return _routes_binding("bad")(handler, "Set recursive=true to delete directories")
            shutil.rmtree(target)
        else:
            target.unlink()
        return _routes_binding("j")(handler, {"ok": True, "path": body["path"]})
    except (ValueError, PermissionError) as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e))


def _handle_file_save(handler, body):
    try:
        _routes_binding("require")(body, "session_id", "path")
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))
    try:
        s = _routes_binding("get_session")(body["session_id"])
    except KeyError:
        return _routes_binding("bad")(handler, "Session not found", 404)
    try:
        target = _routes_binding("safe_resolve")(Path(s.workspace), body["path"])
        if not target.exists():
            return _routes_binding("bad")(handler, "File not found", 404)
        if target.is_dir():
            return _routes_binding("bad")(handler, "Cannot save: path is a directory")
        target.write_text(body.get("content", ""), encoding="utf-8")
        return _routes_binding("j")(
            handler, {"ok": True, "path": body["path"], "size": target.stat().st_size}
        )
    except (ValueError, PermissionError) as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e))


def _handle_file_create(handler, body):
    try:
        _routes_binding("require")(body, "session_id", "path")
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))
    try:
        s = _routes_binding("get_session")(body["session_id"])
    except KeyError:
        return _routes_binding("bad")(handler, "Session not found", 404)
    try:
        target = _routes_binding("safe_resolve")(Path(s.workspace), body["path"])
        if target.exists():
            return _routes_binding("bad")(handler, "File already exists")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.get("content", ""), encoding="utf-8")
        return _routes_binding("j")(
            handler, {"ok": True, "path": str(target.relative_to(Path(s.workspace)))}
        )
    except (ValueError, PermissionError) as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e))


def _handle_file_rename(handler, body):
    try:
        _routes_binding("require")(body, "session_id", "path", "new_name")
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))
    try:
        s = _routes_binding("get_session")(body["session_id"])
    except KeyError:
        return _routes_binding("bad")(handler, "Session not found", 404)
    try:
        source = _routes_binding("safe_resolve")(Path(s.workspace), body["path"])
        if not source.exists():
            return _routes_binding("bad")(handler, "File not found", 404)
        new_name = body["new_name"].strip()
        if not new_name or "/" in new_name or ".." in new_name:
            return _routes_binding("bad")(handler, "Invalid file name")
        dest = source.parent / new_name
        if dest.exists():
            return _routes_binding("bad")(handler, f'A file named "{new_name}" already exists')
        source.rename(dest)
        new_rel = str(dest.relative_to(Path(s.workspace)))
        return _routes_binding("j")(handler, {"ok": True, "old_path": body["path"], "new_path": new_rel})
    except (ValueError, PermissionError, OSError) as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e))


def _handle_create_dir(handler, body):
    try:
        _routes_binding("require")(body, "session_id", "path")
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))
    try:
        s = _routes_binding("get_session")(body["session_id"])
    except KeyError:
        return _routes_binding("bad")(handler, "Session not found", 404)
    try:
        target = _routes_binding("safe_resolve")(Path(s.workspace), body["path"])
        if target.exists():
            return _routes_binding("bad")(handler, "Path already exists")
        target.mkdir(parents=True)
        return _routes_binding("j")(
            handler, {"ok": True, "path": str(target.relative_to(Path(s.workspace)))}
        )
    except (ValueError, PermissionError, OSError) as e:
        return _routes_binding("bad")(handler, _routes_binding("_sanitize_error")(e))
