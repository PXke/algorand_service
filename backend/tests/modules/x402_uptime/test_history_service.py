"""services/history_service.py: record (write-path, fail-soft) and read (bounded aggregation).

Uses the real InMemoryUptimeHistoryStore (no Cassandra needed) as the store
seam, same convention x402_storage's own tests use for BackupService against
InMemoryBackupStore.
"""

from __future__ import annotations

from typing import Never

import pytest

from app.core.config import settings
from app.modules.x402_directory.services.listing_service import url_hash
from app.modules.x402_uptime.models.domain import StoredUptimeCheck
from app.modules.x402_uptime.services import history_service as history_service_module
from app.modules.x402_uptime.services.checker import UptimeResult
from app.modules.x402_uptime.stores.memory import InMemoryUptimeHistoryStore

_URL = "https://example.com/"
# A fixed, small "now" so tiny checked_at_epoch fixtures (1000, 2000, ...)
# land inside a 30-day read window -- read() computes since_epoch from the
# real wall clock otherwise, which would make small fixture epochs look
# decades old and silently exclude them.
_NOW = 100_000


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze history_service.time.time() to _NOW for every test in this file."""
    monkeypatch.setattr(history_service_module.time, "time", lambda: float(_NOW))


def _up(response_time_ms: int = 100) -> UptimeResult:
    return UptimeResult(
        final_url=_URL,
        reachable=True,
        http_status=200,
        response_time_ms=response_time_ms,
        resolved_ip="203.0.113.5",
        error="",
        redirect_chain=[_URL],
    )


def _down() -> UptimeResult:
    return UptimeResult(
        final_url=_URL,
        reachable=False,
        http_status=0,
        response_time_ms=5000,
        resolved_ip="",
        error="timeout",
        redirect_chain=[_URL],
    )


class _BrokenStore:
    def record(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("cassandra down")

    def list_since(self, *_a: object, **_kw: object) -> Never:
        raise ConnectionError("cassandra down")


# --------------------------------------------------------------------------- #
# record
# --------------------------------------------------------------------------- #
def test_record_writes_a_row_keyed_by_the_same_url_hash_the_directory_uses() -> None:
    """record() reuses listing_service.url_hash rather than a second hash implementation."""
    store = InMemoryUptimeHistoryStore()
    service = history_service_module.HistoryService(store)

    service.record(_URL, _up(150), checked_at_epoch=1000)

    rows = store.list_since(url_hash(_URL), since_epoch=0, limit=10)
    assert len(rows) == 1
    assert rows[0] == StoredUptimeCheck(
        url_hash=url_hash(_URL),
        url=_URL,
        checked_at_epoch=1000,
        reachable=True,
        http_status=200,
        response_time_ms=150,
        error="",
    )


def test_record_of_an_unreachable_result_is_still_written() -> None:
    """Regression: a downtime IS the point of history -- a failed check must be recorded too."""
    store = InMemoryUptimeHistoryStore()
    service = history_service_module.HistoryService(store)

    service.record(_URL, _down(), checked_at_epoch=1000)

    rows = store.list_since(url_hash(_URL), since_epoch=0, limit=10)
    assert len(rows) == 1
    assert rows[0].reachable is False
    assert rows[0].error == "timeout"


def test_record_failure_is_swallowed_not_raised() -> None:
    """A storage hiccup recording history must never turn an already-served check into a 500."""
    service = history_service_module.HistoryService(_BrokenStore())
    service.record(_URL, _up(), checked_at_epoch=1000)  # must not raise


# --------------------------------------------------------------------------- #
# read
# --------------------------------------------------------------------------- #
def test_read_with_no_history_is_an_honest_empty_answer() -> None:
    """No real checks recorded yet -- uptime_pct is None (undefined), not 0 or 100."""
    service = history_service_module.HistoryService(InMemoryUptimeHistoryStore())

    report = service.read(_URL, days=30)

    assert report["checks_recorded"] == 0
    assert report["uptime_pct"] is None
    assert report["history"] == []


def test_read_computes_uptime_pct_from_mixed_up_and_down_rows() -> None:
    """3 up + 1 down over the window -> 75% uptime, not skewed by call frequency (one row per real check)."""
    store = InMemoryUptimeHistoryStore()
    service = history_service_module.HistoryService(store)
    service.record(_URL, _up(100), checked_at_epoch=1000)
    service.record(_URL, _up(110), checked_at_epoch=2000)
    service.record(_URL, _up(120), checked_at_epoch=3000)
    service.record(_URL, _down(), checked_at_epoch=4000)

    report = service.read(_URL, days=30)

    assert report["checks_recorded"] == 4
    assert report["uptime_pct"] == 75.0


def test_read_a_5xx_counts_as_down_same_as_cache_pys_own_rule() -> None:
    """A reachable-but-5xx row counts toward downtime, same "down" rule as cache.py's is_down."""
    store = InMemoryUptimeHistoryStore()
    service = history_service_module.HistoryService(store)
    server_error = UptimeResult(
        final_url=_URL,
        reachable=True,
        http_status=503,
        response_time_ms=10,
        resolved_ip="203.0.113.5",
        error="",
        redirect_chain=[_URL],
    )
    service.record(_URL, _up(100), checked_at_epoch=1000)
    service.record(_URL, server_error, checked_at_epoch=2000)

    report = service.read(_URL, days=30)

    assert report["checks_recorded"] == 2
    assert report["uptime_pct"] == 50.0


def test_read_latency_percentiles_only_use_reachable_rows() -> None:
    """A down row's response_time_ms (e.g. a timeout's elapsed time) must not pollute latency stats."""
    store = InMemoryUptimeHistoryStore()
    service = history_service_module.HistoryService(store)
    service.record(_URL, _up(100), checked_at_epoch=1000)
    service.record(_URL, _down(), checked_at_epoch=2000)  # response_time_ms=5000, must be excluded

    report = service.read(_URL, days=30)

    assert report["latency_max_ms"] == 100
    assert report["latency_p50_ms"] == 100


def test_read_excludes_rows_older_than_the_requested_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row outside the `days` window is not counted, same as a real 30-day-old check would age out."""
    store = InMemoryUptimeHistoryStore()
    service = history_service_module.HistoryService(store)
    one_day = 86400
    # A bigger "now" than the module fixture's default, so a genuine 40-day
    # offset stays non-negative while still landing outside a 30-day window
    # (the shared _freeze_now fixture's small _NOW can't express that).
    now = 10_000_000
    monkeypatch.setattr(history_service_module.time, "time", lambda: float(now))
    service.record(_URL, _up(100), checked_at_epoch=now - 40 * one_day)  # outside a 30-day window
    service.record(_URL, _up(200), checked_at_epoch=now - 1 * one_day)  # inside

    report = service.read(_URL, days=30)

    assert report["checks_recorded"] == 1
    assert report["history"][0]["response_time_ms"] == 200


def test_read_respects_the_configured_max_results_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md section 4: no unbounded listings -- the read is capped even within the time window."""
    monkeypatch.setattr(settings, "x402_uptime_history_max_results", 2)
    store = InMemoryUptimeHistoryStore()
    service = history_service_module.HistoryService(store)
    for i in range(5):
        service.record(_URL, _up(100 + i), checked_at_epoch=1000 + i)

    report = service.read(_URL, days=30)

    assert report["checks_recorded"] == 2


def test_read_reports_truncated_and_the_actual_covered_window_when_the_row_cap_is_hit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (finding #7): a frequently-checked target must not silently claim full `days` coverage.

    Cap the store at 3 rows and write 5 checks, all within the requested
    30-day window, spaced 1 hour apart ending "now". The row cap (not the
    `days` boundary or a lack of history) is what stops the read short, so
    `truncated` must be True and `window_days_actual` must reflect only the
    span of the 3 rows actually returned (2 hours), not the requested 30
    days -- the exact "claims a 30-day window but only covers ~1 day for a
    hot target" bug the same-night review found.
    """
    monkeypatch.setattr(settings, "x402_uptime_history_max_results", 3)
    store = InMemoryUptimeHistoryStore()
    service = history_service_module.HistoryService(store)
    one_hour = 3600
    for i in range(5):
        service.record(_URL, _up(100), checked_at_epoch=_NOW - (4 - i) * one_hour)

    report = service.read(_URL, days=30)

    assert report["checks_recorded"] == 3
    assert report["truncated"] is True
    assert report["window_days_requested"] == 30
    # Only the newest 3 of the 5 rows are returned (newest-first, cap=3):
    # checked_at_epoch = NOW-2h, NOW-1h, NOW -- a 2-hour actual span.
    assert report["window_days_actual"] == round(2 * one_hour / 86400, 2)
    assert report["oldest_checked_at_epoch"] == _NOW - 2 * one_hour
    assert report["newest_checked_at_epoch"] == _NOW


def test_read_is_not_truncated_when_all_in_window_history_fits_under_the_cap() -> None:
    """A target with little history is genuinely under-covered, not truncated -- the two must be distinguishable."""
    store = InMemoryUptimeHistoryStore()
    service = history_service_module.HistoryService(store)
    service.record(_URL, _up(100), checked_at_epoch=_NOW - 3600)

    report = service.read(_URL, days=30)

    assert report["checks_recorded"] == 1
    assert report["truncated"] is False
    assert report["window_days_actual"] == 0.0


def test_read_with_no_history_reports_no_window_and_is_not_truncated() -> None:
    """Regression: the new window fields must degrade gracefully to an honest empty answer, not a crash."""
    service = history_service_module.HistoryService(InMemoryUptimeHistoryStore())

    report = service.read(_URL, days=30)

    assert report["truncated"] is False
    assert report["window_days_actual"] == 0.0
    assert report["oldest_checked_at_epoch"] is None
    assert report["newest_checked_at_epoch"] is None


def test_read_returns_history_newest_first() -> None:
    """The raw per-check rows come back newest-first, same clustering order the real table uses."""
    store = InMemoryUptimeHistoryStore()
    service = history_service_module.HistoryService(store)
    service.record(_URL, _up(100), checked_at_epoch=1000)
    service.record(_URL, _up(200), checked_at_epoch=3000)
    service.record(_URL, _up(300), checked_at_epoch=2000)

    report = service.read(_URL, days=30)

    assert [row["checked_at_epoch"] for row in report["history"]] == [3000, 2000, 1000]
