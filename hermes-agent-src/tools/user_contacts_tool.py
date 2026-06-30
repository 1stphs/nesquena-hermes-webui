#!/usr/bin/env python3
"""Current-user contact lookup tool for WebUI-originated Hermes sessions."""

from __future__ import annotations

import json
from typing import Any

from tools.registry import registry, tool_error


def _current_user_id() -> str:
    try:
        from gateway.session_context import get_session_env

        return str(get_session_env("HERMES_SESSION_USER_ID", "") or "").strip()
    except Exception:
        return ""


def current_user_contacts_lookup_tool(query: Any, limit: Any = 5) -> str:
    user_id = _current_user_id()
    if not user_id:
        return tool_error(
            "Current user context is unavailable.",
            code="missing_user_context",
        )

    try:
        from api.features.user_contacts import search_current_user_contacts

        result = search_current_user_contacts(user_id, query=query, limit=limit)
        return json.dumps(result, ensure_ascii=False)
    except Exception as exc:
        return tool_error(str(exc), code="contact_lookup_failed")


def check_user_contacts_requirements() -> bool:
    """The tool is always registered; runtime config is validated per call."""

    return True


CURRENT_USER_CONTACTS_LOOKUP_SCHEMA = {
    "name": "current_user_contacts_lookup",
    "description": (
        "Look up contacts from the current WebUI user's address book. "
        "This includes the user's personal contacts and company contacts. "
        "Use this before sending email when the user mentions a person by name "
        "but does not provide an email address. The tool never accepts a "
        "user_id parameter and does not expose other users' personal contacts."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Person name, nickname, email, phone, company, or department keyword.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum contacts to return. Defaults to 5 and is capped server-side.",
                "default": 5,
                "minimum": 1,
                "maximum": 20,
            },
        },
        "required": ["query"],
    },
}


registry.register(
    name="current_user_contacts_lookup",
    toolset="user_contacts",
    schema=CURRENT_USER_CONTACTS_LOOKUP_SCHEMA,
    handler=lambda args, **_kw: current_user_contacts_lookup_tool(
        query=args.get("query", ""),
        limit=args.get("limit", 5),
    ),
    check_fn=check_user_contacts_requirements,
    emoji="📇",
)
