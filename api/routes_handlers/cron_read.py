"""Cron read endpoint handlers re-exported by api.routes."""

import copy
import datetime
import logging
import threading
import time
from collections import OrderedDict
from pathlib import Path
from urllib.parse import parse_qs

from api.routes_handlers._base import _routes_binding


logger = logging.getLogger(__name__)

_CRON_CALENDAR_CACHE_TTL_SECONDS = 30.0
_CRON_CALENDAR_RESPONSE_CACHE_MAX = 64
_CRON_CALENDAR_SOURCE_CACHE_MAX = 256
_CRON_CALENDAR_CACHE_LOCK = threading.RLock()
_CRON_CALENDAR_RESPONSE_CACHE: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()
_CRON_CALENDAR_SOURCE_CACHE: OrderedDict[tuple, tuple[float, dict]] = OrderedDict()


def _cron_calendar_file_signature(path: Path):
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        return ("error", exc.errno, type(exc).__name__)
    return (st.st_mtime_ns, st.st_size)


def _cron_calendar_source_signature(profile: str, home) -> tuple:
    home_path = Path(home).expanduser()
    try:
        home_key = str(home_path.resolve())
    except OSError:
        home_key = str(home_path)
    cron_dir = home_path / "cron"
    return (
        profile,
        home_key,
        _cron_calendar_file_signature(cron_dir / "jobs.json"),
        _cron_calendar_file_signature(cron_dir / "calendar_events.json"),
    )


def _cron_calendar_cache_key(
    profiles: list[str],
    source_signatures: tuple[tuple, ...],
    start_date,
    end_date,
    month_key,
) -> tuple:
    return (
        source_signatures,
        start_date.isoformat(),
        end_date.isoformat(),
        month_key or "",
        tuple(profiles),
    )


def _evict_expired_cron_calendar_cache(cache: OrderedDict, now: float) -> None:
    expired_keys = [
        key
        for key, (ts, _payload) in cache.items()
        if now - ts >= _CRON_CALENDAR_CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        cache.pop(key, None)


def _get_cron_calendar_cache(cache: OrderedDict, key: tuple) -> dict | None:
    now = time.monotonic()
    with _CRON_CALENDAR_CACHE_LOCK:
        cached = cache.get(key)
        if not cached:
            return None
        ts, payload = cached
        if now - ts >= _CRON_CALENDAR_CACHE_TTL_SECONDS:
            cache.pop(key, None)
            return None
        cache.move_to_end(key)
        return copy.deepcopy(payload)


def _set_cron_calendar_cache(cache: OrderedDict, key: tuple, payload: dict, max_size: int) -> None:
    now = time.monotonic()
    with _CRON_CALENDAR_CACHE_LOCK:
        _evict_expired_cron_calendar_cache(cache, now)
        cache[key] = (now, copy.deepcopy(payload))
        cache.move_to_end(key)
        while len(cache) > max_size:
            cache.popitem(last=False)


def _get_cached_cron_calendar_payload(key: tuple) -> dict | None:
    return _get_cron_calendar_cache(_CRON_CALENDAR_RESPONSE_CACHE, key)


def _set_cached_cron_calendar_payload(key: tuple, payload: dict) -> None:
    _set_cron_calendar_cache(
        _CRON_CALENDAR_RESPONSE_CACHE,
        key,
        payload,
        _CRON_CALENDAR_RESPONSE_CACHE_MAX,
    )


def _get_cached_cron_calendar_source(key: tuple) -> dict | None:
    return _get_cron_calendar_cache(_CRON_CALENDAR_SOURCE_CACHE, key)


def _set_cached_cron_calendar_source(key: tuple, payload: dict) -> None:
    _set_cron_calendar_cache(
        _CRON_CALENDAR_SOURCE_CACHE,
        key,
        payload,
        _CRON_CALENDAR_SOURCE_CACHE_MAX,
    )


def _load_cron_calendar_profile_source(
    *,
    home,
    source_key: tuple,
    cron_profile_context_for_home,
    list_jobs,
    load_calendar_events,
) -> tuple[list[dict], list[dict]]:
    cached = _get_cached_cron_calendar_source(source_key)
    if cached is not None:
        return cached.get("jobs", []), cached.get("events", [])

    with cron_profile_context_for_home(home):
        jobs = _routes_binding("_cron_jobs_for_api")(list_jobs(include_disabled=True))
        events = load_calendar_events()
    _set_cached_cron_calendar_source(source_key, {"jobs": jobs, "events": events})
    return jobs, events


def _clear_cron_calendar_cache_for_tests() -> None:
    with _CRON_CALENDAR_CACHE_LOCK:
        _CRON_CALENDAR_RESPONSE_CACHE.clear()
        _CRON_CALENDAR_SOURCE_CACHE.clear()


def _cron_history_response(handler, job_id, offset, limit):
    """List cron run output files with metadata after routes.py validation."""
    from cron.jobs import OUTPUT_DIR as CRON_OUT

    out_dir = CRON_OUT / job_id
    runs = []
    total = 0
    if out_dir.exists():
        all_files = sorted(out_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
        total = len(all_files)
        page = all_files[offset:offset + limit]
        for f in page:
            try:
                st = f.stat()
                runs.append({
                    "filename": f.name,
                    "size": st.st_size,
                    "modified": st.st_mtime,
                })
            except OSError:
                logger.debug("Failed to stat cron output file %s", f)
    return _routes_binding("j")(handler, {"job_id": job_id, "runs": runs, "total": total, "offset": offset})


def _cron_run_detail_response(handler, job_id, filename):
    """Return full content of a single cron run output file after routes.py validation."""
    from cron.jobs import OUTPUT_DIR as CRON_OUT

    fpath = (CRON_OUT / job_id / filename).resolve()
    if not fpath.is_relative_to(CRON_OUT.resolve()):
        return _routes_binding("j")(handler, {"error": "invalid filename"}, status=400)
    if not fpath.exists():
        return _routes_binding("j")(handler, {"error": "run not found"}, status=404)
    try:
        content = fpath.read_text(encoding="utf-8", errors="replace")
        snippet = _cron_output_snippet(content)
        return _routes_binding("j")(handler, {
            "job_id": job_id,
            "filename": filename,
            "content": content,
            "snippet": snippet,
        })
    except Exception as e:
        return _routes_binding("j")(handler, {"error": str(e)}, status=500)


def _cron_output_snippet(text: str, limit: int = 600) -> str:
    """Extract the response body from a cron output .md file for preview."""
    lines = text.split("\n")
    response_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("## Response") or line.startswith("# Response"):
            response_idx = i
            break
    body = ("\n".join(lines[response_idx + 1:]) if response_idx >= 0 else "\n".join(lines)).strip()
    return body[:limit] or "(empty)"


def _handle_cron_output(handler, parsed):
    from cron.jobs import OUTPUT_DIR as CRON_OUT

    qs = parse_qs(parsed.query)
    job_id = qs.get("job_id", [""])[0]
    limit = int(qs.get("limit", ["5"])[0])
    if not job_id:
        return _routes_binding("j")(handler, {"error": "job_id required"}, status=400)
    out_dir = CRON_OUT / job_id
    outputs = []
    if out_dir.exists():
        files = sorted(out_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
        for f in files:
            try:
                txt = f.read_text(encoding="utf-8", errors="replace")
                outputs.append({
                    "filename": f.name,
                    "content": _routes_binding("_cron_output_content_window")(txt),
                })
            except Exception:
                logger.debug("Failed to read cron output file %s", f)
    return _routes_binding("j")(handler, {"job_id": job_id, "outputs": outputs})


def _handle_cron_status(handler, parsed):
    """Return running status for one or all cron jobs."""
    qs = parse_qs(parsed.query)
    job_id = qs.get("job_id", [""])[0]
    if job_id:
        running, elapsed = _routes_binding("_is_cron_running")(job_id)
        return _routes_binding("j")(handler, {"job_id": job_id, "running": running, "elapsed": round(elapsed, 1)})
    with _routes_binding("_RUNNING_CRON_LOCK"):
        running_jobs = _routes_binding("_RUNNING_CRON_JOBS")
        all_running = {jid: round(time.time() - t, 1) for jid, t in running_jobs.items()}
    return _routes_binding("j")(handler, {"running": all_running})


def _handle_cron_recent(handler, parsed):
    """Return cron jobs that have completed since a given timestamp."""
    qs = parse_qs(parsed.query)
    since = float(qs.get("since", ["0"])[0])
    try:
        from cron.jobs import list_jobs

        jobs = list_jobs(include_disabled=True)
        completions = []
        for job in jobs:
            last_run = job.get("last_run_at")
            if not last_run:
                continue
            if isinstance(last_run, str):
                try:
                    ts = datetime.datetime.fromisoformat(
                        last_run.replace("Z", "+00:00")
                    ).timestamp()
                except (ValueError, TypeError):
                    continue
            else:
                ts = float(last_run)
            if ts > since:
                completions.append(
                    {
                        "job_id": job.get("id", ""),
                        "name": job.get("name", "Unknown"),
                        "status": job.get("last_status", "unknown"),
                        "completed_at": ts,
                    }
                )
        return _routes_binding("j")(handler, {"completions": completions, "since": since})
    except ImportError:
        return _routes_binding("j")(handler, {"completions": [], "since": since})


def _handle_cron_calendar(handler, body):
    raw_profiles = body.get("profiles", body.get("profile_names"))
    if not isinstance(raw_profiles, list):
        return _routes_binding("bad")(handler, "profile_names must be an array")
    try:
        start_date, end_date, month_key = _routes_binding("_parse_cron_calendar_range")(
            body.get("start_date"),
            body.get("end_date"),
            body.get("month"),
        )
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))
    if len(raw_profiles) > 100:
        return _routes_binding("bad")(handler, "profile_names cannot contain more than 100 entries")

    profiles = []
    seen = set()
    try:
        for raw in raw_profiles:
            profile = _routes_binding("_normalize_cron_profile_lookup_name")(raw)
            if profile in seen:
                continue
            seen.add(profile)
            profiles.append(profile)
    except ValueError as e:
        return _routes_binding("bad")(handler, str(e))

    from api.calendar_events import calendar_event_dates, calendar_event_for_api, load_calendar_events
    from api.profiles import cron_profile_context_for_home
    from cron.jobs import list_jobs

    profile_homes = [
        (profile, _routes_binding("_profile_home_for_cron_profile_name")(profile))
        for profile in profiles
    ]
    source_signatures = tuple(
        _cron_calendar_source_signature(profile, home)
        for profile, home in profile_homes
    )
    cache_key = _cron_calendar_cache_key(
        profiles,
        source_signatures,
        start_date,
        end_date,
        month_key,
    )
    cached_payload = _get_cached_cron_calendar_payload(cache_key)
    if cached_payload is not None:
        return _routes_binding("j")(handler, cached_payload)

    calendar_days = _routes_binding("_cron_calendar_range_days")(start_date, end_date)
    buckets = {
        day.isoformat(): {
            "date": day.isoformat(),
            "jobs": [],
            "count": 0,
        }
        for day in calendar_days
    }
    for (profile, home), source_key in zip(profile_homes, source_signatures):
        jobs, events = _load_cron_calendar_profile_source(
            home=home,
            source_key=source_key,
            cron_profile_context_for_home=cron_profile_context_for_home,
            list_jobs=list_jobs,
            load_calendar_events=load_calendar_events,
        )
        for job in jobs:
            entry = _routes_binding("_cron_calendar_entry")(job, profile)
            for scheduled_date in sorted(_routes_binding("_cron_calendar_dates_for_job")(job, start_date, end_date)):
                key = scheduled_date.isoformat()
                if key not in buckets:
                    continue
                buckets[key]["jobs"].append(dict(entry))
                buckets[key]["count"] += 1
        for event in events:
            event_profile = str(event.get("profile") or profile).strip() or profile
            if event_profile != profile:
                continue
            entry = calendar_event_for_api(event, profile)
            for scheduled_date in sorted(calendar_event_dates(event, start_date, end_date)):
                key = scheduled_date.isoformat()
                if key not in buckets:
                    continue
                buckets[key]["jobs"].append(dict(entry))
                buckets[key]["count"] += 1

    payload = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "profiles": profiles,
        "days": [buckets[day.isoformat()] for day in calendar_days],
    }
    if month_key is not None:
        payload["month"] = month_key
    _set_cached_cron_calendar_payload(cache_key, payload)
    return _routes_binding("j")(
        handler,
        payload,
    )
