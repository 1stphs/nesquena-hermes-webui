"""Regression tests for v0.50.258 Opus pre-release follow-up."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_redirect_session_ttl_30_days():
    """Pin the SESSION_TTL constant to the 30-day value introduced by #1419."""
    src = (REPO / "api" / "auth.py").read_text(encoding="utf-8")
    assert "SESSION_TTL = 86400 * 30" in src, (
        "SESSION_TTL must be 30 days (86400 * 30) per #1419. Reverting to "
        "24h would re-introduce the daily-kick-out UX regression."
    )
