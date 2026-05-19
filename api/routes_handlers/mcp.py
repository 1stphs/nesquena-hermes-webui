"""MCP endpoint handlers re-exported by api.routes."""

from api.routes_handlers._base import _routes_binding


def _handle_mcp_tools_list(handler):
    """List known MCP tools from already-available runtime inventory only."""
    cfg = _routes_binding("get_config")()
    servers = cfg.get("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
    runtime = _routes_binding("_mcp_runtime_status_by_name")()
    server_summary = _routes_binding("_server_summary")
    server_summaries = {
        str(name): server_summary(str(name), scfg, runtime.get(str(name)))
        for name, scfg in servers.items()
    }
    tools = _routes_binding("_mcp_tools_from_runtime_status")(runtime, server_summaries)
    source = "mcp_runtime_status"
    if not tools:
        tools = _routes_binding("_mcp_tools_from_registry")(server_summaries)
        source = "tool_registry" if tools else "none"
    tools.sort(key=lambda row: (row.get("server", ""), row.get("name", "")))
    unavailable_servers = [
        summary["name"] for summary in server_summaries.values()
        if summary.get("enabled") and not summary.get("active")
    ]
    return _routes_binding("j")(handler, {
        "tools": tools,
        "total": len(tools),
        "source": source,
        "inventory_scope": "already_known_runtime_only",
        "unavailable_servers": unavailable_servers,
    })


def _handle_mcp_servers_list(handler):
    """List configured MCP servers with safe, read-only runtime visibility."""
    cfg = _routes_binding("get_config")()
    servers = cfg.get("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
    runtime = _routes_binding("_mcp_runtime_status_by_name")()
    result = [
        _routes_binding("_server_summary")(name, scfg, runtime.get(str(name)))
        for name, scfg in servers.items()
    ]
    return _routes_binding("j")(handler, {
        "servers": result,
        "toggle_supported": False,
        "reload_required": True,
    })


def _handle_mcp_server_delete(handler, name):
    """Delete an MCP server by name."""
    from urllib.parse import unquote

    name = unquote(name)
    if not name:
        return _routes_binding("bad")(handler, "name is required")
    cfg = _routes_binding("get_config")()
    servers = cfg.get("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
    if name not in servers:
        return _routes_binding("bad")(handler, f"MCP server '{name}' not found", 404)
    del servers[name]
    cfg["mcp_servers"] = servers
    _routes_binding("_save_yaml_config_file")(_routes_binding("_get_config_path")(), cfg)
    _routes_binding("reload_config")()
    return _routes_binding("j")(handler, {"ok": True, "deleted": name})


def _handle_mcp_server_update(handler, name, body):
    """Add or update an MCP server."""
    from urllib.parse import unquote

    name = unquote(name)
    if not name:
        return _routes_binding("bad")(handler, "name is required")
    # Validate: must have url (http) or command (stdio)
    server_cfg = {}
    cfg = _routes_binding("get_config")()
    servers = cfg.get("mcp_servers", {})
    if not isinstance(servers, dict):
        servers = {}
    existing_cfg = servers.get(name, {})
    if body.get("url"):
        server_cfg["url"] = body["url"].strip()
        if body.get("headers"):
            server_cfg["headers"] = _routes_binding("_strip_masked_values")(body["headers"], existing_cfg.get("headers", {}))
    elif body.get("command"):
        server_cfg["command"] = body["command"].strip()
        if body.get("args"):
            server_cfg["args"] = body["args"] if isinstance(body["args"], list) else [body["args"]]
        if body.get("env"):
            server_cfg["env"] = _routes_binding("_strip_masked_values")(body["env"], existing_cfg.get("env", {}))
    else:
        return _routes_binding("bad")(handler, "url or command is required")
    if body.get("timeout") is not None:
        try:
            server_cfg["timeout"] = int(body["timeout"])
        except (ValueError, TypeError):
            pass
    servers[name] = server_cfg
    cfg["mcp_servers"] = servers
    _routes_binding("_save_yaml_config_file")(_routes_binding("_get_config_path")(), cfg)
    _routes_binding("reload_config")()
    return _routes_binding("j")(handler, {"ok": True, "server": _routes_binding("_server_summary")(name, server_cfg)})
