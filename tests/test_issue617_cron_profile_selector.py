"""Regression coverage for issue #617 scheduled-job profile selection."""

import io
import json
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


class _JSONHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.response_headers = []
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers.append((key, value))

    def end_headers(self):
        pass


def _payload(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def test_cron_api_serializes_legacy_profile_as_explicit_server_default():
    from api.routes import _cron_job_for_api

    legacy = {"id": "legacy", "name": "Legacy job"}
    payload = _cron_job_for_api(legacy)

    assert payload["profile"] is None
    assert "profile" not in legacy, "API serialization must not mutate stored legacy jobs"


def test_cron_profile_value_validates_against_existing_profiles(monkeypatch):
    import api.profiles as profiles
    from api.routes import _normalize_cron_profile_value

    monkeypatch.setattr(
        profiles,
        "list_profiles_api",
        lambda: [
            {"name": "default"},
            {"name": "research"},
        ],
    )

    assert _normalize_cron_profile_value(" research ") == "research"
    assert _normalize_cron_profile_value("") is None
    assert _normalize_cron_profile_value(None) is None
    with pytest.raises(ValueError, match="Unknown profile: missing"):
        _normalize_cron_profile_value("missing")


def test_cron_create_api_persists_profile_and_returns_it(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    created = {
        "id": "job617",
        "name": "Profiled job",
        "prompt": "ping",
        "schedule": {"kind": "interval", "minutes": 60},
    }
    updated = {**created, "profile": "research"}
    calls = []

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.create_job = lambda **kwargs: calls.append(("create", kwargs)) or dict(created)
    cron_jobs.update_job = lambda job_id, updates: calls.append(("update", job_id, updates)) or dict(updated)

    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [{"name": "research"}])
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    routes._handle_cron_create(
        handler,
        {
            "name": "Profiled job",
            "prompt": "ping",
            "schedule": "every 60m",
            "deliver": "local",
            "profile": "research",
        },
    )

    body = _payload(handler)
    assert handler.status == 200
    assert body["ok"] is True
    assert body["job"]["profile"] == "research"
    assert calls[0][0] == "create"
    assert calls[1] == ("update", "job617", {"profile": "research"})


def test_cron_create_api_rejects_unknown_profile_before_persisting(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.create_job = lambda **kwargs: pytest.fail("invalid profiles must not create jobs")
    cron_jobs.update_job = lambda *args, **kwargs: pytest.fail("invalid profiles must not update jobs")

    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [{"name": "research"}])
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    routes._handle_cron_create(
        handler,
        {"prompt": "ping", "schedule": "every 60m", "profile": "missing"},
    )

    assert handler.status == 400
    assert "Unknown profile: missing" in _payload(handler)["error"]


def test_cron_batch_api_lists_jobs_for_requested_profiles(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    events = []

    class Ctx:
        def __init__(self, home):
            self.home = str(home)

        def __enter__(self):
            events.append(("enter", self.home))
            current_home[0] = self.home

        def __exit__(self, exc_type, exc, tb):
            events.append(("exit", self.home))
            current_home[0] = None

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    current_home = [None]

    def list_jobs(include_disabled=False):
        events.append(("list", include_disabled))
        home = current_home[0]
        return [{"id": home.rsplit("/", 1)[-1], "name": "Job"}]

    cron_jobs.list_jobs = list_jobs
    monkeypatch.setattr(
        profiles,
        "list_profiles_api",
        lambda: [{"name": "default"}, {"name": "research"}, {"name": "sales"}],
    )
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda name: Path("/hermes") if name == "default" else Path("/hermes/profiles") / name,
    )
    monkeypatch.setattr(profiles, "cron_profile_context_for_home", Ctx)
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    routes._handle_cron_batch(
        handler,
        {"profile_names": ["research", "sales", "research"]},
    )

    body = _payload(handler)
    assert handler.status == 200
    assert [item["profile"] for item in body["profiles"]] == ["research", "sales"]
    assert body["profiles"][0]["path"] == "/hermes/profiles/research"
    assert body["profiles"][0]["jobs"][0]["id"] == "research"
    assert body["profiles"][1]["jobs"][0]["id"] == "sales"
    assert events == [
        ("enter", "/hermes/profiles/research"),
        ("list", True),
        ("exit", "/hermes/profiles/research"),
        ("enter", "/hermes/profiles/sales"),
        ("list", True),
        ("exit", "/hermes/profiles/sales"),
    ]


def test_cron_batch_api_rejects_unknown_profiles(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [{"name": "research"}])

    handler = _JSONHandler()
    routes._handle_cron_batch(handler, {"profiles": ["ghost"]})

    assert handler.status == 400
    assert "Unknown profile: ghost" in _payload(handler)["error"]


def test_cron_calendar_api_summarizes_profile_jobs_by_month(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    current_home = [None]

    class Ctx:
        def __init__(self, home):
            self.home = str(home)

        def __enter__(self):
            current_home[0] = self.home

        def __exit__(self, exc_type, exc, tb):
            current_home[0] = None

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")

    def list_jobs(include_disabled=False):
        if current_home[0].endswith("/research"):
            return [
                {
                    "id": "daily",
                    "name": "Daily report",
                    "enabled": True,
                    "schedule": {"kind": "daily", "display": "daily"},
                },
                {
                    "id": "weekly",
                    "name": "Monday sync",
                    "enabled": True,
                    "schedule": {"kind": "weekly", "weekdays": ["monday"], "display": "weekly"},
                },
                {
                    "id": "off",
                    "name": "Disabled",
                    "enabled": False,
                    "schedule": {"kind": "daily", "display": "daily"},
                },
            ]
        return [
            {
                "id": "interval",
                "name": "Mailbox watcher",
                "enabled": True,
                "schedule": {"kind": "interval", "minutes": 5, "display": "every 5m"},
            },
            {
                "id": "monthly",
                "name": "Invoice check",
                "enabled": True,
                "schedule": {"kind": "monthly", "day": 15, "display": "monthly"},
            },
        ]

    cron_jobs.list_jobs = list_jobs
    monkeypatch.setattr(
        profiles,
        "list_profiles_api",
        lambda: [{"name": "research"}, {"name": "sales"}],
    )
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda name: Path("/hermes/profiles") / name,
    )
    monkeypatch.setattr(profiles, "cron_profile_context_for_home", Ctx)
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    routes._handle_cron_calendar(
        handler,
        {"profile_names": ["research", "sales"], "month": "202605"},
    )

    body = _payload(handler)
    assert handler.status == 200
    assert body["month"] == "202605"
    assert body["profiles"] == ["research", "sales"]
    assert len(body["days"]) == 31

    may_1 = body["days"][0]
    assert may_1["date"] == "2026-05-01"
    assert {(job["name"], job["profile"], job["frequency"]) for job in may_1["jobs"]} == {
        ("Daily report", "research", "daily"),
        ("Mailbox watcher", "sales", "every 5m"),
    }

    may_4 = body["days"][3]
    assert any(job["name"] == "Monday sync" and job["profile"] == "research" for job in may_4["jobs"])

    may_15 = body["days"][14]
    assert any(job["name"] == "Invoice check" and job["profile"] == "sales" for job in may_15["jobs"])
    assert all(job["name"] != "Disabled" for day in body["days"] for job in day["jobs"])


def test_cron_calendar_api_validates_month(monkeypatch):
    import api.routes as routes

    handler = _JSONHandler()
    routes._handle_cron_calendar(handler, {"profile_names": ["research"], "month": "2026-05"})

    assert handler.status == 400
    assert "yyyymm" in _payload(handler)["error"]


def test_cron_update_api_accepts_profile_clear_and_rejects_unknown(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    calls = []
    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")

    def update_job(job_id, updates):
        calls.append((job_id, updates))
        return {"id": job_id, "name": "Updated", **updates}

    cron_jobs.update_job = update_job
    monkeypatch.setattr(profiles, "list_profiles_api", lambda: [{"name": "research"}])
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)

    handler = _JSONHandler()
    routes._handle_cron_update(handler, {"job_id": "job617", "profile": ""})
    assert handler.status == 200
    assert _payload(handler)["job"]["profile"] is None
    assert calls == [("job617", {"profile": None})]

    bad_handler = _JSONHandler()
    routes._handle_cron_update(bad_handler, {"job_id": "job617", "profile": "ghost"})
    assert bad_handler.status == 400
    assert "Unknown profile: ghost" in _payload(bad_handler)["error"]
    assert calls == [("job617", {"profile": None})]


def test_manual_cron_run_uses_execution_profile_but_persists_to_owning_store(monkeypatch):
    import api.profiles as profiles
    import api.routes as routes

    events = []

    class Ctx:
        def __init__(self, home):
            self.home = str(home)

        def __enter__(self):
            events.append(("enter", self.home))

        def __exit__(self, exc_type, exc, tb):
            events.append(("exit", self.home))

    cron_pkg = types.ModuleType("cron")
    cron_pkg.__path__ = []
    cron_jobs = types.ModuleType("cron.jobs")
    cron_jobs.save_job_output = lambda job_id, output: events.append(("save", job_id, output))
    cron_jobs.mark_job_run = lambda job_id, success, error=None: events.append(("mark", job_id, success, error))
    cron_scheduler = types.ModuleType("cron.scheduler")
    cron_scheduler.run_job = lambda job: events.append(("run", job["id"])) or (True, "output", "final", None)

    def fake_subprocess_run(job, execution_profile_home):
        events.append(("run", job["id"], str(execution_profile_home)))
        return True, "output", "final", None

    monkeypatch.setattr(profiles, "cron_profile_context_for_home", Ctx)
    monkeypatch.setattr(routes, "_run_cron_job_in_profile_subprocess", fake_subprocess_run)
    monkeypatch.setitem(sys.modules, "cron", cron_pkg)
    monkeypatch.setitem(sys.modules, "cron.jobs", cron_jobs)
    monkeypatch.setitem(sys.modules, "cron.scheduler", cron_scheduler)

    routes._mark_cron_running("job617")
    routes._run_cron_tracked(
        {"id": "job617"},
        profile_home="/hermes/default",
        execution_profile_home="/hermes/profiles/research",
    )

    assert events == [
        ("run", "job617", "/hermes/profiles/research"),
        ("enter", "/hermes/default"),
        ("save", "job617", "output"),
        ("mark", "job617", True, None),
        ("exit", "/hermes/default"),
    ]
    assert routes._is_cron_running("job617") == (False, 0.0)
