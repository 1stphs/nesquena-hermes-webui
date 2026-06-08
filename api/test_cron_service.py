from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


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
