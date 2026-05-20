"""Workspace endpoint handlers re-exported by api.routes."""

from pathlib import Path

from api.routes_handlers._base import _routes_binding


def _handle_workspace_add(handler, body):
    # Strip surrounding paired quotes BEFORE any further processing — macOS
    # Finder's "Copy as Pathname" wraps paths in single quotes, and users
    # routinely paste those quoted strings into the Add Space input.
    # Doing this at the route entry means every downstream check (blocked
    # system path, validate_workspace_to_add, duplicate detection) sees the
    # cleaned form.
    path_str = _routes_binding("_strip_surrounding_quotes")(body.get("path", "").strip())
    name = body.get("name", "").strip()
    auto_create = body.get("create", False)
    if not path_str:
        return _routes_binding("bad")(handler, "path is required")
    # Validate the path is NOT a blocked system root BEFORE any filesystem mutation.
    # This prevents creating orphan directories on rejected paths (#782 review).
    # _is_blocked_system_path honours user-tmp carve-outs (e.g. /var/folders on
    # macOS) so pytest's tmp_path_factory paths and other legit user-tmp dirs
    # still register cleanly.
    candidate = Path(path_str).expanduser().resolve()
    if _routes_binding("_is_blocked_system_path")(candidate):
        return _routes_binding("bad")(handler, f"Path points to a system directory: {candidate}")
    # Now safe to create the directory if requested
    if auto_create:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except (OSError, PermissionError) as e:
            return _routes_binding("bad")(handler, f"Could not create directory: {_routes_binding('_sanitize_error')(e)}")
    # Full validation (exists, is_dir) — should pass now that dir exists
    try:
        p = _routes_binding("validate_workspace_to_add")(path_str)
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))
    wss = _routes_binding("load_workspaces")()
    if any(w["path"] == str(p) for w in wss):
        return _routes_binding("bad")(handler, "Workspace already in list")
    wss.append({"path": str(p), "name": name or p.name})
    _routes_binding("save_workspaces")(wss)
    return _routes_binding("j")(handler, {"ok": True, "workspaces": wss})


def _handle_workspace_remove(handler, body):
    path_str = body.get("path", "").strip()
    if not path_str:
        return _routes_binding("bad")(handler, "path is required")
    wss = _routes_binding("load_workspaces")()
    wss = [w for w in wss if w["path"] != path_str]
    _routes_binding("save_workspaces")(wss)
    return _routes_binding("j")(handler, {"ok": True, "workspaces": wss})


def _handle_workspace_rename(handler, body):
    path_str = body.get("path", "").strip()
    name = body.get("name", "").strip()
    if not path_str or not name:
        return _routes_binding("bad")(handler, "path and name are required")
    wss = _routes_binding("load_workspaces")()
    for w in wss:
        if w["path"] == path_str:
            w["name"] = name
            break
    else:
        return _routes_binding("bad")(handler, "Workspace not found", 404)
    _routes_binding("save_workspaces")(wss)
    return _routes_binding("j")(handler, {"ok": True, "workspaces": wss})
