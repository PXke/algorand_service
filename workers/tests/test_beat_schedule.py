"""The editorial-brief recurrence beat is off by default (it silently regenerated standing briefs, 2026-07-19) and only appears when explicitly enabled via env."""

from __future__ import annotations

import pytest

import app.celery_app as celery_app


def test_editorial_brief_scan_absent_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Omits the editorial-brief scan beat when the enable env var is unset."""
    monkeypatch.delenv("EDITORIAL_BRIEF_SCAN_ENABLED", raising=False)
    schedule = celery_app._build_beat_schedule()
    assert "scan-editorial-brief-schedule" not in schedule


def test_editorial_brief_scan_present_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Adds the editorial-brief scan beat, pointed at the right task, when explicitly enabled."""
    monkeypatch.setenv("EDITORIAL_BRIEF_SCAN_ENABLED", "true")
    schedule = celery_app._build_beat_schedule()
    assert "scan-editorial-brief-schedule" in schedule
    assert (
        schedule["scan-editorial-brief-schedule"]["task"]
        == "app.tasks.newspaper.scan_editorial_brief_schedule"
    )


def test_editorial_brief_scan_stays_off_for_falsey_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps the editorial-brief scan beat off for a falsey env value like "0"."""
    monkeypatch.setenv("EDITORIAL_BRIEF_SCAN_ENABLED", "0")
    assert "scan-editorial-brief-schedule" not in celery_app._build_beat_schedule()


def test_flush_pending_view_counts_scheduled_every_ten_minutes_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Redis-buffered view-count flush runs unconditionally (no feature flag) every 10 minutes by default -- see view_counts.flush_pending_views for why that interval is safe."""
    monkeypatch.delenv("VIEW_COUNT_FLUSH_SECONDS", raising=False)
    schedule = celery_app._build_beat_schedule()
    assert (
        schedule["flush-pending-view-counts"]["task"] == "app.tasks.newspaper.flush_pending_views"
    )
    assert schedule["flush-pending-view-counts"]["schedule"] == 600.0


def test_flush_pending_view_counts_interval_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flush interval is overridable via VIEW_COUNT_FLUSH_SECONDS."""
    monkeypatch.setenv("VIEW_COUNT_FLUSH_SECONDS", "120")
    schedule = celery_app._build_beat_schedule()
    assert schedule["flush-pending-view-counts"]["schedule"] == 120.0
