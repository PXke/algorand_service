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


def test_flush_pending_analytics_scheduled_every_ten_minutes_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Redis-buffered deferred-analytics flush runs unconditionally (no feature flag) every 10 minutes by default -- see analytics_flush.flush_pending_analytics for why that interval is safe."""
    monkeypatch.delenv("ANALYTICS_FLUSH_SECONDS", raising=False)
    schedule = celery_app._build_beat_schedule()
    assert (
        schedule["flush-pending-analytics"]["task"] == "app.tasks.newspaper.flush_pending_analytics"
    )
    assert schedule["flush-pending-analytics"]["schedule"] == 600.0


def test_flush_pending_analytics_interval_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flush interval is overridable via ANALYTICS_FLUSH_SECONDS."""
    monkeypatch.setenv("ANALYTICS_FLUSH_SECONDS", "180")
    schedule = celery_app._build_beat_schedule()
    assert schedule["flush-pending-analytics"]["schedule"] == 180.0


def test_discard_dead_pending_sources_scheduled_hourly_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dead-source sweep (arima.io incident, 2026-08-27) runs unconditionally (no feature flag), every hour by default -- see source_liveness.py for why the pace stays slow."""
    monkeypatch.delenv("DEAD_SOURCE_SWEEP_SECONDS", raising=False)
    schedule = celery_app._build_beat_schedule()
    assert (
        schedule["discard-dead-pending-sources"]["task"]
        == "app.tasks.newspaper.discard_dead_pending_sources"
    )
    assert schedule["discard-dead-pending-sources"]["schedule"] == 3600.0


def test_discard_dead_pending_sources_interval_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sweep interval is overridable via DEAD_SOURCE_SWEEP_SECONDS."""
    monkeypatch.setenv("DEAD_SOURCE_SWEEP_SECONDS", "1800")
    schedule = celery_app._build_beat_schedule()
    assert schedule["discard-dead-pending-sources"]["schedule"] == 1800.0


def test_drain_url_queue_beat_expires_matches_its_own_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """drain_url_queue is single_flight-locked (W3-A), but a tick that never got a free worker slot within its own interval should be dropped by Celery rather than run stale/backed-up -- `options.expires` on the beat entry is the companion half of that, pinned to the same URL_QUEUE_DRAIN_SECONDS value as the schedule interval itself."""
    monkeypatch.setenv("URL_QUEUE_DRAIN_SECONDS", "10")
    schedule = celery_app._build_beat_schedule()
    entry = schedule["drain-url-queue"]
    assert entry["schedule"] == 10.0
    assert entry["options"]["expires"] == 10.0


def test_drain_url_queue_beat_expires_follows_a_configured_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-default URL_QUEUE_DRAIN_SECONDS carries through to both the schedule interval and the expires option identically."""
    monkeypatch.setenv("URL_QUEUE_DRAIN_SECONDS", "30")
    schedule = celery_app._build_beat_schedule()
    entry = schedule["drain-url-queue"]
    assert entry["schedule"] == 30.0
    assert entry["options"]["expires"] == 30.0


def test_reclaim_stale_processing_urls_beat_present_and_scheduled_every_ten_minutes_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The url_queue processing-row reclaim sweep runs unconditionally (no feature flag) every 10 minutes by default, pointed at the real task name the beat dispatches by."""
    monkeypatch.delenv("URL_QUEUE_PROCESSING_RECLAIM_SECONDS", raising=False)
    schedule = celery_app._build_beat_schedule()
    assert (
        schedule["reclaim-stale-processing-urls"]["task"]
        == "app.tasks.crawler.reclaim_stale_processing_urls"
    )
    assert schedule["reclaim-stale-processing-urls"]["schedule"] == 600.0


def test_reclaim_stale_processing_urls_beat_interval_configurable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reclaim sweep's interval is overridable via URL_QUEUE_PROCESSING_RECLAIM_SECONDS."""
    monkeypatch.setenv("URL_QUEUE_PROCESSING_RECLAIM_SECONDS", "120")
    schedule = celery_app._build_beat_schedule()
    assert schedule["reclaim-stale-processing-urls"]["schedule"] == 120.0
