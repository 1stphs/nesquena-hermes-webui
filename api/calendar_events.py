"""Traditional calendar event storage for the WebUI cron calendar."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import uuid
from datetime import date as _date, datetime as _datetime, time as _time, timedelta as _timedelta
from pathlib import Path
from typing import Any

from hermes_constants import get_hermes_home
from utils import atomic_replace


logger = logging.getLogger(__name__)

_calendar_events_lock = threading.Lock()
_TIME_ONLY_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")


def _secure_dir(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except (OSError, NotImplementedError):
        pass


def _secure_file(path: Path) -> None:
    try:
        if path.exists():
            os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass


def _cron_dir() -> Path:
    return get_hermes_home().resolve() / "cron"


def _calendar_events_file() -> Path:
    return _cron_dir() / "calendar_events.json"


def ensure_calendar_event_dirs() -> None:
    cron_dir = _cron_dir()
    cron_dir.mkdir(parents=True, exist_ok=True)
    _secure_dir(cron_dir)


def load_calendar_events() -> list[dict[str, Any]]:
    ensure_calendar_event_dirs()
    events_file = _calendar_events_file()
    if not events_file.exists():
        return []
    try:
        with events_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("events", [])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Calendar event database corrupted: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"Failed to read calendar event database: {exc}") from exc


def save_calendar_events(events: list[dict[str, Any]]) -> None:
    ensure_calendar_event_dirs()
    events_file = _calendar_events_file()
    fd, tmp_path = tempfile.mkstemp(
        dir=str(events_file.parent),
        suffix=".tmp",
        prefix=".calendar_events_",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                {"events": events, "updated_at": _datetime.now().astimezone().isoformat()},
                f,
                indent=2,
                ensure_ascii=False,
            )
            f.flush()
            os.fsync(f.fileno())
        atomic_replace(tmp_path, events_file)
        _secure_file(events_file)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _parse_event_datetime(value, field_name: str) -> _datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field_name} is required")
    try:
        dt = _datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO datetime") from exc
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def _parse_event_date(value) -> _date:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("date is required")
    normalized = raw.replace("/", "-")
    try:
        return _date.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError("date must be in yyyy-mm-dd or yyyy/mm/dd format") from exc


def _combine_date_and_time(day: _date, value, field_name: str) -> _datetime:
    raw = str(value or "").strip()
    if not _TIME_ONLY_RE.fullmatch(raw):
        raise ValueError(f"{field_name} must be a valid ISO datetime or HH:MM time")
    try:
        parsed_time = _time.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid ISO datetime or HH:MM time") from exc
    return _datetime.combine(day, parsed_time).astimezone()


def _resolve_event_datetime(value, *, day: _date | None, field_name: str) -> _datetime:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{field_name} is required")
    if "T" in raw or re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}", raw):
        return _parse_event_datetime(raw, field_name)
    if day is None:
        raise ValueError(f"date is required when {field_name} is a time-only value")
    return _combine_date_and_time(day, raw, field_name)


def _parse_all_day(value) -> bool:
    if value in (None, ""):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError("all_day must be a boolean")


def _normalize_participants(value) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw_items = [item.strip() for item in re.split(r"[\n,;]+", value) if item.strip()]
    else:
        raw_items = value
    if not isinstance(raw_items, (list, tuple, set)):
        raise ValueError("participants must be an array")
    normalized: list[str] = []
    for item in raw_items:
        text = str(item or "").strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _display_title(title, name, description) -> str:
    for value in (title, name):
        text = str(value or "").strip()
        if text:
            return text[:200]
    desc = str(description or "").strip()
    if desc:
        first_line = desc.splitlines()[0].strip()
        if first_line:
            return first_line[:200]
    return "Untitled event"


def create_calendar_event(
    *,
    date=None,
    start_time,
    end_time=None,
    all_day=False,
    location=None,
    participants=None,
    description=None,
    remark=None,
    title=None,
    name=None,
    event_type=None,
    profile=None,
) -> dict[str, Any]:
    event_day = _parse_event_date(date) if date not in (None, "") else None
    parsed_all_day = _parse_all_day(all_day)

    if parsed_all_day:
        if event_day is None:
            start_dt = _resolve_event_datetime(start_time, day=None, field_name="start_time")
            event_day = start_dt.date()
        start_dt = _datetime.combine(event_day, _time.min).astimezone()
        end_dt = _datetime.combine(event_day, _time(23, 59, 59)).astimezone()
    else:
        start_dt = _resolve_event_datetime(start_time, day=event_day, field_name="start_time")
        end_dt = (
            _resolve_event_datetime(end_time, day=event_day, field_name="end_time")
            if end_time not in (None, "")
            else start_dt
        )
    if end_dt < start_dt:
        raise ValueError("end_time must be on or after start_time")

    normalized_description = str(description or remark or "").strip()
    normalized_profile = None
    if profile is not None:
        normalized_profile = str(profile).strip() or None
    normalized_event_type = str(event_type or "").strip() or None

    event = {
        "id": uuid.uuid4().hex[:12],
        "type": "calendar_event",
        "title": _display_title(title, name, normalized_description),
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "all_day": parsed_all_day,
        "location": str(location or "").strip() or None,
        "participants": _normalize_participants(participants),
        "description": normalized_description,
        "event_type": normalized_event_type,
        "profile": normalized_profile,
        "created_at": _datetime.now().astimezone().isoformat(),
    }

    with _calendar_events_lock:
        events = load_calendar_events()
        events.append(event)
        save_calendar_events(events)
    return event


def _stored_event_datetime(value) -> _datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = _datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt


def calendar_event_dates(event: dict[str, Any], start: _date, end: _date) -> set[_date]:
    start_dt = _stored_event_datetime(event.get("start_time"))
    end_dt = _stored_event_datetime(event.get("end_time")) or start_dt
    if start_dt is None or end_dt is None:
        return set()
    if end_dt < start_dt:
        start_dt, end_dt = end_dt, start_dt

    dates: set[_date] = set()
    cursor = start_dt.date()
    end_date = end_dt.date()
    while cursor <= end_date:
        if start <= cursor <= end:
            dates.add(cursor)
        cursor = cursor + _timedelta(days=1)
    return dates


def calendar_event_for_api(event: dict[str, Any], profile: str) -> dict[str, Any]:
    return {
        "id": str(event.get("id") or ""),
        "type": "calendar_event",
        "title": str(event.get("title") or "Untitled event"),
        "name": str(event.get("title") or "Untitled event"),
        "profile": profile,
        "start_time": event.get("start_time"),
        "end_time": event.get("end_time"),
        "all_day": bool(event.get("all_day", False)),
        "location": event.get("location"),
        "participants": list(event.get("participants") or []),
        "description": str(event.get("description") or ""),
        "event_type": event.get("event_type"),
        "created_at": event.get("created_at"),
    }
