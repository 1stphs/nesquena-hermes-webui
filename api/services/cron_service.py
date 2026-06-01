"""WebUI-only helpers for profile-aware cron tool dispatch."""

from __future__ import annotations

import functools
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_registry():
    from tools.registry import registry

    return registry


def _current_session_key() -> str | None:
    try:
        from gateway.session_context import get_session_env

        session_key = str(get_session_env("HERMES_SESSION_KEY", "") or "").strip()
        if session_key:
            return session_key
    except Exception:
        logger.debug("Failed to read HERMES_SESSION_KEY from session context", exc_info=True)

    session_key = str(os.environ.get("HERMES_SESSION_KEY", "") or "").strip()
    return session_key or None


def _resolve_session_profile_home(session_key: str) -> Path:
    from api.models import get_session
    from api.profiles import get_hermes_home_for_profile

    session = get_session(session_key)
    return get_hermes_home_for_profile(getattr(session, "profile", None))


def _cron_context_for_home(home: Path):
    from api.profiles import cron_profile_context_for_home

    return cron_profile_context_for_home(home)


def bridge_cronjob_handler(original_handler, args: dict, **kwargs):
    """Run a cron tool call inside the current WebUI session profile, when known."""

    session_key = str(kwargs.get("session_id") or "").strip() or _current_session_key()
    if not session_key:
        return original_handler(args, **kwargs)

    try:
        profile_home = _resolve_session_profile_home(session_key)
    except Exception:
        logger.debug(
            "Failed to resolve session profile for cronjob bridge; falling back to original handler",
            exc_info=True,
        )
        return original_handler(args, **kwargs)

    with _cron_context_for_home(profile_home):
        return original_handler(args, **kwargs)


def _wrap_cronjob_handler(original_handler):
    if getattr(original_handler, "_webui_profile_bridge", False):
        return original_handler

    @functools.wraps(original_handler)
    def wrapped(args, **kwargs):
        return bridge_cronjob_handler(original_handler, args, **kwargs)

    wrapped._webui_profile_bridge = True
    wrapped._webui_original_handler = original_handler
    return wrapped


def install_webui_cronjob_bridge() -> bool:
    """Wrap the cronjob tool so WebUI chat sessions use their own profile cron path."""

    registry = _get_registry()
    entry = registry.get_entry("cronjob")
    if entry is None:
        return False
    if getattr(entry.handler, "_webui_profile_bridge", False):
        return True

    registry.register(
        name=entry.name,
        toolset=entry.toolset,
        schema=entry.schema,
        handler=_wrap_cronjob_handler(entry.handler),
        check_fn=entry.check_fn,
        requires_env=entry.requires_env,
        is_async=entry.is_async,
        description=entry.description,
        emoji=entry.emoji,
        max_result_size_chars=entry.max_result_size_chars,
    )
    return True
