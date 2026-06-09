"""Endpoint handlers migrated from api.routes for routes step3."""

from __future__ import annotations

from api.routes_handlers._base import _sync_routes_bindings


def _handle_cron_history(handler, parsed):
    """Validate cron history query parameters before delegating to cron_read."""
    _sync_routes_bindings(globals())
    import re as _re

    qs = parse_qs(parsed.query)
    job_id = qs.get("job_id", [""])[0]
    if not job_id:
        return j(handler, {"error": "job_id required"}, status=400)
    if not _re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}", job_id) or job_id in (".", ".."):
        return j(handler, {"error": "invalid job_id"}, status=400)
    try:
        offset = max(0, int(qs.get("offset", ["0"])[0]))
        limit = max(1, min(500, int(qs.get("limit", ["50"])[0])))
    except (ValueError, TypeError):
        return j(handler, {"error": "offset and limit must be integers"}, status=400)
    return _cron_history_response(handler, job_id, offset, limit)


def _handle_cron_run_detail(handler, parsed):
    """Validate cron run detail query parameters before delegating to cron_read."""
    _sync_routes_bindings(globals())
    import re as _re

    qs = parse_qs(parsed.query)
    job_id = qs.get("job_id", [""])[0]
    filename = qs.get("filename", [""])[0]
    if not job_id or not filename:
        return j(handler, {"error": "job_id and filename required"}, status=400)
    if not _re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}", job_id) or job_id in (".", ".."):
        return j(handler, {"error": "invalid job_id"}, status=400)
    return _cron_run_detail_response(handler, job_id, filename)


def _handle_cron_create(handler, body):
    _sync_routes_bindings(globals())
    try:
        require(body, "prompt", "schedule")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        from cron.jobs import create_job, update_job

        profile = _normalize_cron_profile_value(body.get("profile"))
        job = create_job(
            prompt=body["prompt"],
            schedule=body["schedule"],
            name=body.get("name") or None,
            deliver=body.get("deliver") or "local",
            skills=body.get("skills") or [],
            model=body.get("model") or None,
        )
        if profile is not None:
            job = update_job(job["id"], {"profile": profile}) or job
        return j(handler, {"ok": True, "job": _cron_job_for_api(job)})
    except Exception as e:
        return j(handler, {"error": str(e)}, status=400)


def _handle_cron_calendar_create(handler, body):
    _sync_routes_bindings(globals())
    try:
        if not body.get("start_time"):
            return bad(handler, "Missing required field(s): start_time")
    except ValueError as e:
        return bad(handler, str(e))
    try:
        from api.calendar_events import create_calendar_event

        profile = _normalize_cron_profile_value(body.get("profile"))
        event = create_calendar_event(
            date=body.get("date"),
            start_time=body.get("start_time"),
            end_time=body.get("end_time"),
            all_day=body.get("all_day", False),
            location=body.get("location"),
            participants=body.get("participants"),
            description=body.get("description"),
            remark=body.get("remark"),
            title=body.get("title"),
            name=body.get("name"),
            event_type=body.get("event_type", body.get("type")),
            profile=profile,
        )
        return j(handler, {"ok": True, "event": event})
    except Exception as e:
        return j(handler, {"error": str(e)}, status=400)


def _handle_cron_batch(handler, body):
    _sync_routes_bindings(globals())
    raw_profiles = body.get("profiles", body.get("profile_names"))
    if not isinstance(raw_profiles, list):
        return bad(handler, "profiles must be an array")
    if len(raw_profiles) > 100:
        return bad(handler, "profiles cannot contain more than 100 entries")

    profiles = []
    seen = set()
    try:
        for raw in raw_profiles:
            profile = _normalize_cron_profile_lookup_name(raw)
            if profile in seen:
                continue
            seen.add(profile)
            profiles.append(profile)
    except ValueError as e:
        return bad(handler, str(e))

    from api.profiles import cron_profile_context_for_home
    from cron.jobs import list_jobs

    results = []
    for profile in profiles:
        home = _profile_home_for_cron_profile_name(profile)
        with cron_profile_context_for_home(home):
            jobs = _cron_jobs_for_api(list_jobs(include_disabled=True))
        results.append({
            "profile": profile,
            "path": str(home),
            "jobs": jobs,
        })
    return j(handler, {"profiles": results})


def _handle_cron_update(handler, body):
    _sync_routes_bindings(globals())
    try:
        require(body, "job_id")
    except ValueError as e:
        return bad(handler, str(e))
    from cron.jobs import update_job

    try:
        updates = {}
        for k, v in body.items():
            if k == "job_id":
                continue
            if k == "profile":
                updates[k] = _normalize_cron_profile_value(v)
            elif v is not None:
                updates[k] = v
    except ValueError as e:
        return bad(handler, str(e))
    job = update_job(body["job_id"], updates)
    if not job:
        return bad(handler, "Job not found", 404)
    return j(handler, {"ok": True, "job": _cron_job_for_api(job)})


def _handle_cron_delete(handler, body):
    _sync_routes_bindings(globals())
    try:
        require(body, "job_id")
    except ValueError as e:
        return bad(handler, str(e))
    from cron.jobs import remove_job

    ok = remove_job(body["job_id"])
    if not ok:
        return bad(handler, "Job not found", 404)
    return j(handler, {"ok": True, "job_id": body["job_id"]})


def _handle_cron_run(handler, body):
    _sync_routes_bindings(globals())
    job_id = body.get("job_id", "")
    if not job_id:
        return bad(handler, "job_id required")
    from cron.jobs import get_job

    job = get_job(job_id)
    if not job:
        return bad(handler, "Job not found", 404)
    # Prevent double-run: reject if the job is already tracked as running
    already_running, elapsed = _is_cron_running(job_id)
    if already_running:
        return j(handler, {"ok": False, "job_id": job_id, "status": "already_running",
                            "elapsed": round(elapsed, 1)})
    _mark_cron_running(job_id)
    # Capture the TLS-active profile home now — the thread runs after the
    # request finishes, so TLS is gone by then.
    #
    # Resolve directly without a try/except: get_active_hermes_home() does
    # in-memory dict reads + a single Path.is_dir() stat, so the only way
    # it could raise from inside a request handler is if api.profiles
    # itself partially failed to import (in which case we'd already be
    # 500-ing the whole request). A silent fallback to None here would
    # re-introduce the exact bug #1573 fixes — the worker thread would
    # run unpinned against the process-global HERMES_HOME — so we'd
    # rather let any unexpected exception 500 the request than corrupt
    # cross-profile state.
    from api.profiles import get_active_hermes_home

    _profile_home = get_active_hermes_home()
    _execution_profile_home = _profile_home_for_cron_job(job)
    threading.Thread(target=_run_cron_tracked, args=(job, _profile_home, _execution_profile_home), daemon=True).start()
    return j(handler, {"ok": True, "job_id": job_id, "status": "running"})


def _handle_cron_pause(handler, body):
    _sync_routes_bindings(globals())
    job_id = body.get("job_id", "")
    if not job_id:
        return bad(handler, "job_id required")
    from cron.jobs import pause_job

    result = pause_job(job_id, reason=body.get("reason"))
    if result:
        return j(handler, {"ok": True, "job": result})
    return bad(handler, "Job not found", 404)


def _handle_cron_resume(handler, body):
    _sync_routes_bindings(globals())
    job_id = body.get("job_id", "")
    if not job_id:
        return bad(handler, "job_id required")
    from cron.jobs import resume_job

    result = resume_job(job_id)
    if result:
        return j(handler, {"ok": True, "job": result})
    return bad(handler, "Job not found", 404)
