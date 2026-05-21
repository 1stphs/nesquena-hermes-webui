"""Shared helpers for endpoint handler modules imported by api.routes."""

import sys


def _routes_binding(name: str):
    routes = sys.modules.get("api.routes")
    if routes is not None and hasattr(routes, name):
        return getattr(routes, name)

    caller_globals = sys._getframe(1).f_globals
    if name in caller_globals:
        return caller_globals[name]

    from api import config, helpers

    if hasattr(config, name):
        return getattr(config, name)
    if hasattr(helpers, name):
        return getattr(helpers, name)
    raise AttributeError(name)


def _sync_routes_bindings(target_globals: dict) -> None:
    routes = sys.modules.get("api.routes")
    if routes is None:
        return
    for name, value in vars(routes).items():
        if name.startswith("__") and name.endswith("__"):
            continue
        target_globals[name] = value
