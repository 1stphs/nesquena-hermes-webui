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
