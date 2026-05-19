import calendar as _calendar
import logging
import re
import sys
import threading
import time
from datetime import date as _date, datetime as _datetime


logger = logging.getLogger(__name__)


_RUNNING_CRON_JOBS: dict[str, float] = {}
_RUNNING_CRON_LOCK = threading.Lock()
_CRON_OUTPUT_CONTENT_LIMIT = 8000
_CRON_OUTPUT_HEADER_CONTEXT = 200


def _routes_binding(name: str):
    routes = sys.modules.get("api.routes")
    if routes is not None and hasattr(routes, name):
        return getattr(routes, name)
    return globals()[name]


def _mark_cron_running(job_id: str):
    with _RUNNING_CRON_LOCK:
        _RUNNING_CRON_JOBS[job_id] = time.time()


def _mark_cron_done(job_id: str):
    with _RUNNING_CRON_LOCK:
        _RUNNING_CRON_JOBS.pop(job_id, None)


def _is_cron_running(job_id: str) -> tuple[bool, float]:
    """Return (is_running, elapsed_seconds)."""
    with _RUNNING_CRON_LOCK:
        t = _RUNNING_CRON_JOBS.get(job_id)
        if t is None:
            return False, 0.0
        return True, time.time() - t


def _cron_response_marker_index(text: str) -> int:
    """Return the start index of a markdown Response heading, if present."""
    candidates = []
    for heading in ("## Response", "# Response"):
        if text.startswith(heading):
            candidates.append(0)
        idx = text.find(f"\n{heading}")
        if idx >= 0:
            candidates.append(idx + 1)
    return min(candidates) if candidates else -1


def _cron_output_content_window(text: str, limit: int = _CRON_OUTPUT_CONTENT_LIMIT) -> str:
    """Return a bounded cron output window that preserves useful response text."""
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text

    response_idx = _cron_response_marker_index(text)
    if response_idx >= 0:
        header = text[:min(_CRON_OUTPUT_HEADER_CONTEXT, response_idx)].rstrip()
        response = text[response_idx:].lstrip("\n")
        content = f"{header}\n...\n{response}" if header else response
        return content[:limit]

    return text[-limit:]


def _cron_job_for_api(job: dict) -> dict:
    """Return a cron job payload with the #617 optional profile field present."""
    payload = dict(job or {})
    payload.setdefault("profile", None)
    return payload


def _cron_jobs_for_api(jobs) -> list[dict]:
    return [_cron_job_for_api(job) for job in (jobs or [])]


def _normalize_cron_profile_lookup_name(value) -> str:
    profile = str(value or "").strip()
    if not profile:
        raise ValueError("profile name cannot be empty")
    if profile not in _available_cron_profile_names():
        raise ValueError(f"Unknown profile: {profile}")
    return profile


def _available_cron_profile_names() -> set[str]:
    from api.profiles import list_profiles_api

    names = {"default"}
    for profile in list_profiles_api():
        try:
            name = str(profile.get("name") or "").strip()
        except AttributeError:
            continue
        if name:
            names.add(name)
    return names


def _normalize_cron_profile_value(value) -> str | None:
    if value is None:
        return None
    profile = str(value).strip()
    if not profile:
        return None
    if profile not in _available_cron_profile_names():
        raise ValueError(f"Unknown profile: {profile}")
    return profile


def _profile_home_for_cron_job(job: dict):
    """Resolve the execution profile for a cron job, with graceful fallback."""
    from api.profiles import get_active_hermes_home, get_hermes_home_for_profile

    raw = str((job or {}).get("profile") or "").strip()
    if not raw:
        return get_active_hermes_home()
    if raw not in _available_cron_profile_names():
        logger.warning(
            "Cron job %s references missing profile %r; falling back to server default",
            (job or {}).get("id", "?"), raw,
        )
        return get_active_hermes_home()
    return get_hermes_home_for_profile(raw)


def _profile_home_for_cron_profile_name(profile: str):
    from api.profiles import get_hermes_home_for_profile

    return get_hermes_home_for_profile(profile)


def _parse_cron_calendar_month(value) -> tuple[str, int, int, int]:
    raw = str(value or "").strip()
    if not re.fullmatch(r"\d{6}", raw):
        raise ValueError("month must be in yyyymm format")
    year = int(raw[:4])
    month = int(raw[4:])
    if month < 1 or month > 12:
        raise ValueError("month must be in yyyymm format")
    return raw, year, month, _calendar.monthrange(year, month)[1]


def _cron_job_frequency(job: dict) -> str:
    schedule = (job or {}).get("schedule")
    if (job or {}).get("schedule_display"):
        return str(job.get("schedule_display"))
    if isinstance(schedule, dict):
        if schedule.get("display"):
            return str(schedule.get("display"))
        kind = str(schedule.get("kind") or "").strip()
        if kind == "interval":
            minutes = schedule.get("minutes")
            if minutes:
                return f"every {minutes}m"
        return kind or "scheduled"
    if schedule:
        return str(schedule)
    return "scheduled"


def _parse_iso_date(value) -> _date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return _datetime.fromisoformat(raw).date()
    except ValueError:
        try:
            return _date.fromisoformat(raw[:10])
        except ValueError:
            return None


def _days_from_next_run(job: dict, year: int, month: int) -> set[int]:
    for key in ("next_run_at", "run_at", "at"):
        dt = _parse_iso_date((job or {}).get(key))
        if dt and dt.year == year and dt.month == month:
            return {dt.day}
    return set()


def _all_days(last_day: int) -> set[int]:
    return set(range(1, last_day + 1))


def _parse_int_set(raw, *, min_value: int, max_value: int) -> set[int] | None:
    if raw in (None, "", "*", "?"):
        return None
    values: set[int] = set()
    if isinstance(raw, (list, tuple, set)):
        parts = list(raw)
    else:
        parts = str(raw).split(",")
    for part in parts:
        text = str(part).strip().lower()
        if not text or text in ("*", "?"):
            return None
        if "/" in text:
            text = text.split("/", 1)[0]
        if "-" in text:
            start_s, end_s = text.split("-", 1)
            if start_s.isdigit() and end_s.isdigit():
                start, end = int(start_s), int(end_s)
                values.update(v for v in range(start, end + 1) if min_value <= v <= max_value)
            continue
        if text.isdigit():
            value = int(text)
            if min_value <= value <= max_value:
                values.add(value)
    return values


def _cron_dow_value(value: int) -> int:
    # Cron accepts both 0 and 7 for Sunday. Python date.weekday(): Monday=0.
    return 6 if value in (0, 7) else value - 1


def _cron_expr_days(expr: str, year: int, month: int, last_day: int) -> set[int] | None:
    parts = str(expr or "").strip().split()
    if len(parts) != 5:
        return None
    _, _, dom_raw, mon_raw, dow_raw = parts
    months = _parse_int_set(mon_raw, min_value=1, max_value=12)
    if months is not None and month not in months:
        return set()
    doms = _parse_int_set(dom_raw, min_value=1, max_value=31)
    dows_raw = _parse_int_set(dow_raw, min_value=0, max_value=7)
    dows = {_cron_dow_value(v) for v in dows_raw} if dows_raw is not None else None
    days: set[int] = set()
    for day in range(1, last_day + 1):
        current = _date(year, month, day)
        dom_match = doms is None or day in doms
        dow_match = dows is None or current.weekday() in dows
        if doms is not None and dows is not None:
            if dom_match or dow_match:
                days.add(day)
        elif dom_match and dow_match:
            days.add(day)
    return days


def _weekday_days(year: int, month: int, last_day: int, weekday_values) -> set[int]:
    name_to_weekday = {
        "mon": 0, "monday": 0,
        "tue": 1, "tuesday": 1,
        "wed": 2, "wednesday": 2,
        "thu": 3, "thursday": 3,
        "fri": 4, "friday": 4,
        "sat": 5, "saturday": 5,
        "sun": 6, "sunday": 6,
    }
    if weekday_values in (None, ""):
        return set()
    raw_values = weekday_values if isinstance(weekday_values, list) else [weekday_values]
    wanted = set()
    for raw in raw_values:
        text = str(raw).strip().lower()
        if text in name_to_weekday:
            wanted.add(name_to_weekday[text])
        elif text.isdigit():
            wanted.add(_cron_dow_value(int(text)))
    return {
        day for day in range(1, last_day + 1)
        if _date(year, month, day).weekday() in wanted
    }


def _cron_calendar_days_for_job(job: dict, year: int, month: int, last_day: int) -> set[int]:
    if job.get("enabled") is False:
        return set()
    schedule = job.get("schedule")
    next_run_days = _days_from_next_run(job, year, month)
    if isinstance(schedule, dict):
        kind = str(schedule.get("kind") or "").strip().lower()
        if kind == "interval":
            minutes = schedule.get("minutes") or schedule.get("every_minutes")
            hours = schedule.get("hours") or schedule.get("every_hours")
            days = schedule.get("days") or schedule.get("every_days")
            try:
                interval_minutes = float(minutes or 0) + float(hours or 0) * 60 + float(days or 0) * 1440
            except (TypeError, ValueError):
                interval_minutes = 0
            if interval_minutes and interval_minutes <= 1440:
                return _all_days(last_day)
            return next_run_days
        if kind in {"daily", "day"}:
            return _all_days(last_day)
        if kind in {"weekly", "week"}:
            weekday_values = (
                schedule.get("weekdays") or schedule.get("days_of_week") or
                schedule.get("weekday") or schedule.get("day")
            )
            return _weekday_days(year, month, last_day, weekday_values) or next_run_days
        if kind in {"monthly", "month"}:
            day_values = schedule.get("days") or schedule.get("day_of_month") or schedule.get("day")
            parsed = _parse_int_set(day_values, min_value=1, max_value=31) or set()
            return {day for day in parsed if 1 <= day <= last_day} or next_run_days
        if kind in {"once", "one_time", "date"}:
            return next_run_days
        if schedule.get("cron"):
            return _cron_expr_days(str(schedule.get("cron")), year, month, last_day) or next_run_days
    elif isinstance(schedule, str):
        cron_days = _cron_expr_days(schedule, year, month, last_day)
        if cron_days is not None:
            return cron_days
        text = schedule.strip().lower()
        if text.startswith("every "):
            m = re.search(r"every\s+(\d+(?:\.\d+)?)\s*([mhd])", text)
            if m:
                amount = float(m.group(1))
                unit = m.group(2)
                minutes = amount if unit == "m" else amount * 60 if unit == "h" else amount * 1440
                return _all_days(last_day) if minutes <= 1440 else next_run_days
        if "daily" in text or "every day" in text:
            return _all_days(last_day)
        if "weekly" in text:
            return next_run_days
    return next_run_days or _all_days(last_day)


def _cron_calendar_entry(job: dict, profile: str) -> dict:
    return {
        "id": str(job.get("id") or ""),
        "name": str(job.get("name") or job.get("id") or "Untitled job"),
        "profile": profile,
        "frequency": _cron_job_frequency(job),
        "enabled": job.get("enabled", True) is not False,
    }


def _cron_job_subprocess_main(job, execution_profile_home, result_queue):
    """Run one cron job inside a child process pinned to a profile home."""
    try:
        def _run():
            from cron.scheduler import run_job

            return run_job(job)

        if execution_profile_home is None:
            result = _run()
        else:
            from api.profiles import cron_profile_context_for_home

            with cron_profile_context_for_home(execution_profile_home):
                result = _run()
        result_queue.put(("ok", result))
    except BaseException as exc:  # pragma: no cover - surfaced in parent
        import traceback

        result_queue.put(("error", f"{type(exc).__name__}: {exc}", traceback.format_exc()))


def _cron_subprocess_result_timeout_seconds(job):
    """Return how long the manual-run parent waits for child result payloads."""
    for key in ("timeout_seconds", "max_runtime_seconds", "timeout"):
        raw = (job or {}).get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return max(60.0, value + 30.0)
    return 6 * 60 * 60.0


def _run_cron_job_in_profile_subprocess_impl(job, execution_profile_home, ctx, target=None):
    import queue

    result_queue = ctx.Queue(maxsize=1)
    process = ctx.Process(
        target=target or _routes_binding("_cron_job_subprocess_main"),
        args=(job, execution_profile_home, result_queue),
    )
    process.start()

    result_timeout = _cron_subprocess_result_timeout_seconds(job)
    status = "error"
    payload = ["cron run subprocess failed before producing a result", ""]
    try:
        try:
            status, *payload = result_queue.get(timeout=result_timeout)
        except queue.Empty:
            status = "error"
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                payload = [
                    f"cron run subprocess produced no result within {result_timeout:g}s and was terminated",
                    "",
                ]
            else:
                payload = [
                    f"cron run subprocess exited with code {process.exitcode} without producing a result",
                    "",
                ]
        finally:
            process.join(timeout=5)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                if status == "ok":
                    status = "error"
                    payload = [
                        "cron run subprocess did not exit after returning a result",
                        "",
                    ]
    finally:
        result_queue.close()
        result_queue.join_thread()

    if status == "ok":
        return payload[0]

    message = payload[0]
    traceback_text = payload[1] if len(payload) > 1 else ""
    if traceback_text:
        logger.error("Manual cron subprocess failed:\n%s", traceback_text)
    raise RuntimeError(message)


def _run_cron_tracked(
    job,
    profile_home=None,
    execution_profile_home=None,
    run_job_subprocess=None,
):
    """Wrapper that tracks running state around cron.scheduler.run_job."""
    from cron.jobs import mark_job_run, save_job_output

    job_id = job.get("id", "")
    execution_profile_home = execution_profile_home or profile_home

    def _with_cron_home(home, fn):
        if home is None:
            return fn()
        from api.profiles import cron_profile_context_for_home

        with cron_profile_context_for_home(home):
            return fn()

    try:
        runner = run_job_subprocess or _routes_binding("_run_cron_job_in_profile_subprocess")
        success, output, final_response, error = runner(job, execution_profile_home)

        def _persist_success():
            save_job_output(job_id, output)

            _success, _error = success, error
            if _success and not final_response:
                _success = False
                _error = "Agent completed but produced empty response (model error, timeout, or misconfiguration)"

            mark_job_run(job_id, _success, _error)

        _with_cron_home(profile_home, _persist_success)
    except Exception as e:
        logger.exception("Manual cron run failed for job %s", job_id)
        try:
            _with_cron_home(profile_home, lambda: mark_job_run(job_id, False, str(e)))
        except Exception:
            logger.debug("Failed to mark manual cron run failure for %s", job_id)
    finally:
        _mark_cron_done(job_id)
