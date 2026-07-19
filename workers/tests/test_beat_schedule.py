"""The editorial-brief recurrence beat is off by default (it silently
regenerated standing briefs, 2026-07-19) and only appears when explicitly
enabled via env."""

from __future__ import annotations

import app.celery_app as celery_app


def test_editorial_brief_scan_absent_by_default(monkeypatch):
    monkeypatch.delenv("EDITORIAL_BRIEF_SCAN_ENABLED", raising=False)
    schedule = celery_app._build_beat_schedule()
    assert "scan-editorial-brief-schedule" not in schedule


def test_editorial_brief_scan_present_when_enabled(monkeypatch):
    monkeypatch.setenv("EDITORIAL_BRIEF_SCAN_ENABLED", "true")
    schedule = celery_app._build_beat_schedule()
    assert "scan-editorial-brief-schedule" in schedule
    assert (
        schedule["scan-editorial-brief-schedule"]["task"]
        == "app.tasks.newspaper.scan_editorial_brief_schedule"
    )


def test_editorial_brief_scan_stays_off_for_falsey_values(monkeypatch):
    monkeypatch.setenv("EDITORIAL_BRIEF_SCAN_ENABLED", "0")
    assert "scan-editorial-brief-schedule" not in celery_app._build_beat_schedule()
