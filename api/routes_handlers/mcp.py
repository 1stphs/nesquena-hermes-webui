"""MCP endpoint handlers re-exported by api.routes."""

import sys


def _routes_binding(name: str):
    routes = sys.modules.get("api.routes")
    if routes is not None and hasattr(routes, name):
        return getattr(routes, name)
    from api import config, helpers

    if hasattr(config, name):
        return getattr(config, name)
    return getattr(helpers, name)


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
