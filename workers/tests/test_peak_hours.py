"""DeepSeek peak/off-peak billing confinement (owner decision 2026-08-15): all compose start decisions must be off-peak, with a start-margin so a long compose isn't begun close enough to peak to still be running once it starts."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.newspaper.peak_hours import (
    _parse_peak_windows,
    _parse_weekdays,
    _peak_window_starts_within,
    is_off_peak_now,
    next_off_peak_at,
)


def _freeze_now(monkeypatch: pytest.MonkeyPatch, fixed: datetime) -> None:
    """Pin peak_hours.datetime.now() to `fixed` for the duration of a test."""
    import app.modules.newspaper.peak_hours as peak_hours_module

    class _FixedDatetime(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            del tz
            return fixed

    monkeypatch.setattr(peak_hours_module, "datetime", _FixedDatetime)


def test_parse_peak_windows_basic() -> None:
    """"1-4,6-10" parses into two (start, end) hour ranges."""
    assert _parse_peak_windows("1-4,6-10") == [(1, 4), (6, 10)]


def test_parse_peak_windows_skips_malformed_entries() -> None:
    """A bad env value must degrade to fewer/no windows, never raise -- this gate must fail toward NOT blocking compose."""
    assert _parse_peak_windows("1-4,garbage,6-10,9-3,,25-26") == [(1, 4), (6, 10)]


def test_parse_peak_windows_empty_spec() -> None:
    """Empty or missing spec parses to no windows (fail-open elsewhere)."""
    assert _parse_peak_windows("") == []
    assert _parse_peak_windows(None) == []  # type: ignore[arg-type]


def test_peak_window_starts_within_inside_window() -> None:
    """Currently inside a peak window counts as blocked regardless of margin."""
    now = datetime(2026, 8, 15, 2, 0, tzinfo=UTC)  # inside 1-4
    assert _peak_window_starts_within(now, [(1, 4), (6, 10)], horizon_minutes=90) is True


def test_peak_window_starts_within_outside_and_beyond_margin() -> None:
    """A window exactly `horizon_minutes` away is caught; one minute further out isn't."""
    now = datetime(2026, 8, 15, 4, 30, tzinfo=UTC)  # off-peak, next window (6-10) starts in 90m
    assert _peak_window_starts_within(now, [(1, 4), (6, 10)], horizon_minutes=89) is False
    assert _peak_window_starts_within(now, [(1, 4), (6, 10)], horizon_minutes=90) is True


def test_peak_window_starts_within_midnight_wraparound() -> None:
    """A check just before midnight must see tomorrow's first window, not just today's remaining ones."""
    now = datetime(2026, 8, 15, 23, 50, tzinfo=UTC)  # 10 min before midnight
    # Tomorrow's window starts at 01:00 -> 70 minutes away.
    assert _peak_window_starts_within(now, [(1, 4), (6, 10)], horizon_minutes=69) is False
    assert _peak_window_starts_within(now, [(1, 4), (6, 10)], horizon_minutes=70) is True


def test_is_off_peak_now_no_windows_configured_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty/unparseable LLM_PEAK_HOURS_UTC must never block compose."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "")
    assert is_off_peak_now() is True


def test_is_off_peak_now_inside_peak_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Currently inside a configured peak window -> not off-peak."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "1-4,6-10")
    monkeypatch.setattr(config, "LLM_PEAK_MARGIN_MINUTES", 90)
    monkeypatch.setattr(config, "LLM_ALWAYS_OFF_PEAK_WEEKDAYS_UTC", "")
    _freeze_now(monkeypatch, datetime(2026, 8, 15, 7, 0, tzinfo=UTC))  # a Saturday -- isolate hour-window logic from the weekend rule
    assert is_off_peak_now() is False


def test_is_off_peak_now_within_margin_of_peak_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """The illustrative case from the requirement: a 90-minute-margin compose must not start 1 minute before peak."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "1-4,6-10")
    monkeypatch.setattr(config, "LLM_PEAK_MARGIN_MINUTES", 90)
    monkeypatch.setattr(config, "LLM_ALWAYS_OFF_PEAK_WEEKDAYS_UTC", "")
    _freeze_now(monkeypatch, datetime(2026, 8, 15, 4, 31, tzinfo=UTC))  # 89 min before 6:00 peak, a Saturday
    assert is_off_peak_now() is False


def test_is_off_peak_now_safely_before_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Well outside the margin -> allowed to start."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "1-4,6-10")
    monkeypatch.setattr(config, "LLM_PEAK_MARGIN_MINUTES", 90)
    monkeypatch.setattr(config, "LLM_ALWAYS_OFF_PEAK_WEEKDAYS_UTC", "")
    _freeze_now(monkeypatch, datetime(2026, 8, 15, 4, 0, tzinfo=UTC))  # 120 min before 6:00 peak, a Saturday
    assert is_off_peak_now() is True


def test_is_off_peak_now_respects_explicit_margin_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """An explicit margin_minutes=0 overrides the configured default margin."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "1-4,6-10")
    monkeypatch.setattr(config, "LLM_PEAK_MARGIN_MINUTES", 90)
    monkeypatch.setattr(config, "LLM_ALWAYS_OFF_PEAK_WEEKDAYS_UTC", "")
    _freeze_now(monkeypatch, datetime(2026, 8, 15, 4, 31, tzinfo=UTC))  # 89 min before 6:00 peak, a Saturday
    assert is_off_peak_now(margin_minutes=0) is True


def test_next_off_peak_at_returns_none_when_already_off_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    """No pending block -> nothing to report."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "")
    assert next_off_peak_at() is None


def test_next_off_peak_at_clears_the_margin(monkeypatch: pytest.MonkeyPatch) -> None:
    """When blocked, the returned instant must itself be off-peak (past the window end)."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "1-4,6-10")
    monkeypatch.setattr(config, "LLM_PEAK_MARGIN_MINUTES", 90)
    monkeypatch.setattr(config, "LLM_ALWAYS_OFF_PEAK_WEEKDAYS_UTC", "")
    _freeze_now(monkeypatch, datetime(2026, 8, 15, 7, 0, tzinfo=UTC))  # inside 6-10 peak window, a Saturday

    next_at = next_off_peak_at()
    assert next_at is not None
    # Off-peak resumes at 10:00 (window end); returned instant must itself be off-peak.
    assert next_at.hour == 10


def test_parse_weekdays_basic() -> None:
    """"5,6" -> {5, 6} (Saturday, Sunday)."""
    assert _parse_weekdays("5,6") == {5, 6}


def test_parse_weekdays_skips_malformed_entries() -> None:
    """A bad env value degrades to fewer/no weekend days, never raises."""
    assert _parse_weekdays("5,garbage,6,,9,-1") == {5, 6}


def test_parse_weekdays_empty_spec() -> None:
    """Empty or missing spec parses to no always-off-peak weekdays."""
    assert _parse_weekdays("") == set()
    assert _parse_weekdays(None) == set()  # type: ignore[arg-type]


def test_is_off_peak_now_weekend_override_ignores_peak_hour(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Saturday inside a configured peak window is still off-peak when that weekday is in LLM_ALWAYS_OFF_PEAK_WEEKDAYS_UTC."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "1-4,6-10")
    monkeypatch.setattr(config, "LLM_PEAK_MARGIN_MINUTES", 90)
    monkeypatch.setattr(config, "LLM_ALWAYS_OFF_PEAK_WEEKDAYS_UTC", "5,6")
    _freeze_now(monkeypatch, datetime(2026, 8, 15, 7, 0, tzinfo=UTC))  # Saturday, inside 6-10 peak window
    assert is_off_peak_now() is True


def test_is_off_peak_now_weekday_unaffected_by_weekend_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal weekday inside a peak window still blocks, even with the weekend override configured."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "1-4,6-10")
    monkeypatch.setattr(config, "LLM_PEAK_MARGIN_MINUTES", 90)
    monkeypatch.setattr(config, "LLM_ALWAYS_OFF_PEAK_WEEKDAYS_UTC", "5,6")
    _freeze_now(monkeypatch, datetime(2026, 8, 17, 7, 0, tzinfo=UTC))  # Monday, inside 6-10 peak window
    assert is_off_peak_now() is False


def test_next_off_peak_at_none_when_weekend_override_already_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocked-looking hour on a configured weekend day is already off-peak -- nothing to wait for."""
    import app.core.config as config

    monkeypatch.setattr(config, "LLM_PEAK_HOURS_UTC", "1-4,6-10")
    monkeypatch.setattr(config, "LLM_PEAK_MARGIN_MINUTES", 90)
    monkeypatch.setattr(config, "LLM_ALWAYS_OFF_PEAK_WEEKDAYS_UTC", "5,6")
    _freeze_now(monkeypatch, datetime(2026, 8, 15, 7, 0, tzinfo=UTC))  # Saturday, inside 6-10 peak window
    assert next_off_peak_at() is None
