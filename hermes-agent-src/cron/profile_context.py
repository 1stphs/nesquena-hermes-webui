"""Cron-specific helpers for temporarily scoping storage to one profile home."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from pathlib import Path

_cron_profile_lock = threading.RLock()


@contextmanager
def profile_cron_home_context(home: Path):
    """Temporarily bind cron storage globals to one profile home."""
    resolved_home = Path(home).resolve()

    with _cron_profile_lock:
        prev_env = os.environ.get("HERMES_HOME")

        import cron.jobs as jobs_mod
        import cron.scheduler as sched_mod

        prev_jobs = (
            jobs_mod.HERMES_DIR,
            jobs_mod.CRON_DIR,
            jobs_mod.JOBS_FILE,
            jobs_mod.OUTPUT_DIR,
        )
        prev_sched = (
            sched_mod._hermes_home,
            sched_mod._LOCK_DIR,
            sched_mod._LOCK_FILE,
        )

        try:
            os.environ["HERMES_HOME"] = str(resolved_home)
            jobs_mod.HERMES_DIR = resolved_home
            jobs_mod.CRON_DIR = resolved_home / "cron"
            jobs_mod.JOBS_FILE = jobs_mod.CRON_DIR / "jobs.json"
            jobs_mod.OUTPUT_DIR = jobs_mod.CRON_DIR / "output"
            sched_mod._hermes_home = resolved_home
            sched_mod._LOCK_DIR = resolved_home / "cron"
            sched_mod._LOCK_FILE = sched_mod._LOCK_DIR / ".tick.lock"
            yield
        finally:
            if prev_env is None:
                os.environ.pop("HERMES_HOME", None)
            else:
                os.environ["HERMES_HOME"] = prev_env

            (
                jobs_mod.HERMES_DIR,
                jobs_mod.CRON_DIR,
                jobs_mod.JOBS_FILE,
                jobs_mod.OUTPUT_DIR,
            ) = prev_jobs
            (
                sched_mod._hermes_home,
                sched_mod._LOCK_DIR,
                sched_mod._LOCK_FILE,
            ) = prev_sched
