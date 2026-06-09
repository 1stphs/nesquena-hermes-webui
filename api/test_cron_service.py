from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import json


def test_bridge_cronjob_handler_uses_session_profile_home(monkeypatch):
    import api.services.cron_service as cron_service

    events = []

    @contextmanager
    def fake_context(home):
        events.append(("enter", home))
        try:
            yield
        finally:
            events.append(("exit", home))

    monkeypatch.setattr(cron_service, "_current_session_key", lambda: "session-1")
    monkeypatch.setattr(
        cron_service,
        "_resolve_session_profile_home",
        lambda session_id: Path("/tmp/profiles/demo"),
    )
    monkeypatch.setattr(cron_service, "_cron_context_for_home", fake_context)

    def original_handler(args, **kwargs):
        events.append(("handler", args, kwargs))
        return "ok"

    result = cron_service.bridge_cronjob_handler(
        original_handler,
        {"action": "list"},
        task_id="task-1",
    )

    assert result == "ok"
    assert events == [
        ("enter", Path("/tmp/profiles/demo")),
        ("handler", {"action": "list"}, {"task_id": "task-1"}),
        ("exit", Path("/tmp/profiles/demo")),
    ]


def test_bridge_cronjob_handler_without_session_key_falls_back(monkeypatch):
    import api.services.cron_service as cron_service

    events = []

    def fail_if_called(_home):
        raise AssertionError("cron profile context should not be entered")

    monkeypatch.setattr(cron_service, "_current_session_key", lambda: None)
    monkeypatch.setattr(cron_service, "_cron_context_for_home", fail_if_called)

    def original_handler(args, **kwargs):
        events.append(("handler", args, kwargs))
        return "fallback"

    result = cron_service.bridge_cronjob_handler(
        original_handler,
        {"action": "list"},
        task_id="task-2",
    )

    assert result == "fallback"
    assert events == [
        ("handler", {"action": "list"}, {"task_id": "task-2"}),
    ]


def test_install_webui_cronjob_bridge_wraps_registry_entry(monkeypatch):
    import api.services.cron_service as cron_service

    events = []

    @contextmanager
    def fake_context(home):
        events.append(("enter", home))
        try:
            yield
        finally:
            events.append(("exit", home))

    def original_handler(args, **kwargs):
        events.append(("handler", args, kwargs))
        return "wrapped"

    entry = SimpleNamespace(
        name="cronjob",
        toolset="cronjob",
        schema={"name": "cronjob"},
        handler=original_handler,
        check_fn=None,
        requires_env=[],
        is_async=False,
        description="cron tool",
        emoji="⏰",
        max_result_size_chars=1234,
    )

    class FakeRegistry:
        def __init__(self, entry_obj):
            self.entry = entry_obj
            self.register_calls = []

        def get_entry(self, name):
            if name == "cronjob":
                return self.entry
            return None

        def register(self, **kwargs):
            self.register_calls.append(kwargs)
            self.entry = SimpleNamespace(**kwargs)

    fake_registry = FakeRegistry(entry)

    monkeypatch.setattr(cron_service, "_get_registry", lambda: fake_registry)
    monkeypatch.setattr(cron_service, "_current_session_key", lambda: "session-3")
    monkeypatch.setattr(
        cron_service,
        "_resolve_session_profile_home",
        lambda session_id: Path("/tmp/profiles/wrapped"),
    )
    monkeypatch.setattr(cron_service, "_cron_context_for_home", fake_context)

    assert cron_service.install_webui_cronjob_bridge() is True
    assert len(fake_registry.register_calls) == 1
    assert getattr(fake_registry.entry.handler, "_webui_profile_bridge", False) is True

    result = fake_registry.entry.handler({"action": "list"}, task_id="task-3")

    assert result == "wrapped"
    assert events == [
        ("enter", Path("/tmp/profiles/wrapped")),
        ("handler", {"action": "list"}, {"task_id": "task-3"}),
        ("exit", Path("/tmp/profiles/wrapped")),
    ]


def test_install_webui_cronjob_bridge_is_idempotent(monkeypatch):
    import api.services.cron_service as cron_service

    def original_handler(args, **kwargs):
        return "ok"

    entry = SimpleNamespace(
        name="cronjob",
        toolset="cronjob",
        schema={"name": "cronjob"},
        handler=original_handler,
        check_fn=None,
        requires_env=[],
        is_async=False,
        description="cron tool",
        emoji="⏰",
        max_result_size_chars=None,
    )

    class FakeRegistry:
        def __init__(self, entry_obj):
            self.entry = entry_obj
            self.register_calls = []

        def get_entry(self, name):
            if name == "cronjob":
                return self.entry
            return None

        def register(self, **kwargs):
            self.register_calls.append(kwargs)
            self.entry = SimpleNamespace(**kwargs)

    fake_registry = FakeRegistry(entry)
    monkeypatch.setattr(cron_service, "_get_registry", lambda: fake_registry)

    assert cron_service.install_webui_cronjob_bridge() is True
    assert cron_service.install_webui_cronjob_bridge() is True
    assert len(fake_registry.register_calls) == 1


def test_cron_calendar_days_for_job_uses_schedule_expr_for_cron_dict():
    from api.routes_helpers.cron import _cron_calendar_days_for_job

    job = {
        "enabled": True,
        "schedule": {
            "kind": "cron",
            "expr": "0 19 * * *",
            "display": "0 19 * * *",
        },
        "next_run_at": "2026-06-01T19:00:00+00:00",
    }

    days = _cron_calendar_days_for_job(job, 2026, 6, 30)

    assert days == set(range(1, 31))


def test_cron_calendar_days_for_job_keeps_legacy_schedule_cron_key():
    from api.routes_helpers.cron import _cron_calendar_days_for_job

    job = {
        "enabled": True,
        "schedule": {
            "kind": "cron",
            "cron": "0 19 * * *",
            "display": "0 19 * * *",
        },
        "next_run_at": "2026-06-01T19:00:00+00:00",
    }

    days = _cron_calendar_days_for_job(job, 2026, 6, 30)

    assert days == set(range(1, 31))


def test_parse_cron_calendar_range_accepts_start_and_end_dates():
    from datetime import date

    from api.routes_helpers.cron import _parse_cron_calendar_range

    start, end, month_key = _parse_cron_calendar_range("2026-05-30", "2026-06-02", None)

    assert start == date(2026, 5, 30)
    assert end == date(2026, 6, 2)
    assert month_key is None


def test_parse_cron_calendar_range_accepts_unix_timestamps_in_seconds():
    from datetime import date

    from api.routes_helpers.cron import _parse_cron_calendar_range

    start, end, month_key = _parse_cron_calendar_range("1780272000", "1780444800", None)

    assert start == date(2026, 6, 1)
    assert end == date(2026, 6, 3)
    assert month_key is None


def test_parse_cron_calendar_range_accepts_unix_timestamps_in_milliseconds():
    from datetime import date

    from api.routes_helpers.cron import _parse_cron_calendar_range

    start, end, month_key = _parse_cron_calendar_range("1780272000000", "1780444800000", None)

    assert start == date(2026, 6, 1)
    assert end == date(2026, 6, 3)
    assert month_key is None


def test_parse_cron_calendar_range_keeps_legacy_month():
    from datetime import date

    from api.routes_helpers.cron import _parse_cron_calendar_range

    start, end, month_key = _parse_cron_calendar_range(None, None, "202606")

    assert start == date(2026, 6, 1)
    assert end == date(2026, 6, 30)
    assert month_key == "202606"


def test_cron_calendar_dates_for_job_filters_cross_month_range():
    from datetime import date

    from api.routes_helpers.cron import _cron_calendar_dates_for_job

    job = {
        "enabled": True,
        "schedule": {
            "kind": "cron",
            "expr": "0 19 * * *",
            "display": "0 19 * * *",
        },
    }

    dates = _cron_calendar_dates_for_job(job, date(2026, 5, 30), date(2026, 6, 2))

    assert dates == {
        date(2026, 5, 30),
        date(2026, 5, 31),
        date(2026, 6, 1),
        date(2026, 6, 2),
    }


class _ResponseHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.body = b""
        self.wfile = self

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        return None

    def write(self, data):
        self.body += data


def _read_json(handler: _ResponseHandler) -> dict:
    return json.loads(handler.body.decode("utf-8"))


def test_handle_cron_calendar_create_accepts_form_style_fields(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

    import api.routes as routes
    from api.routes_handlers.cron_write import _handle_cron_calendar_create

    handler = _ResponseHandler()
    body = {
        "title": "项目周会",
        "date": "2026/06/09",
        "start_time": "09:00",
        "end_time": "10:00",
        "type": "个人",
        "profile": "default",
        "remark": "腾讯会议",
    }

    _handle_cron_calendar_create(handler, body)

    payload = _read_json(handler)
    assert handler.status == 200
    assert payload["ok"] is True
    assert payload["event"]["title"] == "项目周会"
    assert payload["event"]["event_type"] == "个人"
    assert payload["event"]["description"] == "腾讯会议"
    assert payload["event"]["start_time"].startswith("2026-06-09T09:00:00")
    assert payload["event"]["end_time"].startswith("2026-06-09T10:00:00")


def test_handle_cron_calendar_create_writes_to_explicit_profile_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

    import api.routes as routes
    import api.profiles as profiles
    from api.calendar_events import load_calendar_events
    from api.routes_handlers.cron_write import _handle_cron_calendar_create

    alpha_home = tmp_path / ".hermes" / "profiles" / "alpha"
    beta_home = tmp_path / ".hermes" / "profiles" / "beta"

    monkeypatch.setattr(routes, "_normalize_cron_profile_value", lambda raw: str(raw).strip() if raw else None)
    monkeypatch.setattr(routes, "_profile_home_for_cron_profile_name", lambda profile: alpha_home if profile == "alpha" else beta_home)
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "alpha")

    real_context = profiles.cron_profile_context_for_home
    handler = _ResponseHandler()
    body = {
        "title": "跨 profile 测试",
        "date": "2026-06-09",
        "start_time": "09:00",
        "end_time": "10:00",
        "profile": "beta",
    }

    _handle_cron_calendar_create(handler, body)

    payload = _read_json(handler)
    assert handler.status == 200
    assert payload["ok"] is True
    assert payload["event"]["profile"] == "beta"

    with real_context(beta_home):
        beta_events = load_calendar_events()
    with real_context(alpha_home):
        alpha_events = load_calendar_events()

    assert len(beta_events) == 1
    assert beta_events[0]["title"] == "跨 profile 测试"
    assert alpha_events == []


def test_handle_cron_calendar_includes_calendar_events(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))

    import api.routes as routes
    from api.routes_handlers.cron_read import _handle_cron_calendar
    from api.calendar_events import create_calendar_event

    @contextmanager
    def _passthrough_context(_home):
        yield

    monkeypatch.setattr(routes, "_normalize_cron_profile_lookup_name", lambda raw: str(raw))
    monkeypatch.setattr(routes, "_profile_home_for_cron_profile_name", lambda _profile: tmp_path / ".hermes")
    monkeypatch.setattr(routes, "_cron_jobs_for_api", lambda jobs: jobs)
    monkeypatch.setattr(routes, "_cron_calendar_entry", lambda job, profile: job)
    monkeypatch.setattr(routes, "_cron_calendar_dates_for_job", lambda job, start, end: set())
    monkeypatch.setattr(routes, "_cron_calendar_range_days", lambda start, end: [start])

    import api.profiles as profiles
    monkeypatch.setattr(profiles, "cron_profile_context_for_home", _passthrough_context)

    import cron.jobs as cron_jobs
    monkeypatch.setattr(cron_jobs, "list_jobs", lambda include_disabled=True: [])

    create_calendar_event(
        title="设计评审",
        date="2026-06-09",
        start_time="13:00",
        end_time="14:00",
        profile="default",
        description="评审新方案",
    )

    handler = _ResponseHandler()
    _handle_cron_calendar(
        handler,
        {
            "profiles": ["default"],
            "start_date": "2026-06-09",
            "end_date": "2026-06-09",
        },
    )

    payload = _read_json(handler)
    assert handler.status == 200
    assert payload["days"][0]["count"] == 1
    event = payload["days"][0]["jobs"][0]
    assert event["type"] == "calendar_event"
    assert event["title"] == "设计评审"
    assert event["description"] == "评审新方案"
