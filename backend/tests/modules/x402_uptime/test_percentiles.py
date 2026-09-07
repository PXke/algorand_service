"""services/percentiles.py: compute_percentiles' math, and sample_target's sampling/budget logic.

`check_target` is the seam for sample_target (same convention test_checker.py
already establishes for checker.py itself: no real DNS/network, just a
canned return value here since checker.py's own request-level behaviour is
covered there).
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.modules.x402_uptime.services import percentiles as percentiles_module
from app.modules.x402_uptime.services.checker import UptimeResult

_URL = "https://example.com/"


def _result(response_time_ms: int) -> UptimeResult:
    return UptimeResult(
        final_url=_URL,
        reachable=True,
        http_status=200,
        response_time_ms=response_time_ms,
        resolved_ip="203.0.113.5",
        error="",
        redirect_chain=[_URL],
    )


# --------------------------------------------------------------------------- #
# compute_percentiles
# --------------------------------------------------------------------------- #
def test_compute_percentiles_of_empty_samples_is_all_zero() -> None:
    """No samples at all (should never happen in practice, sample_target always takes >= 1) -> zeroed, not a crash."""
    result = percentiles_module.compute_percentiles([])
    assert result == {"count": 0, "min": 0, "max": 0, "p50": 0, "p90": 0, "p99": 0}


def test_compute_percentiles_single_sample_is_that_sample_everywhere() -> None:
    """One sample -> every statistic (min/max/p50/p90/p99) is that same value."""
    result = percentiles_module.compute_percentiles([150])
    assert result == {"count": 1, "min": 150, "max": 150, "p50": 150, "p90": 150, "p99": 150}


def test_compute_percentiles_five_samples_nearest_rank() -> None:
    """Known synthetic latencies: nearest-rank over 5 sorted samples [100,110,120,130,500]."""
    result = percentiles_module.compute_percentiles([500, 120, 100, 130, 110])
    assert result["count"] == 5
    assert result["min"] == 100
    assert result["max"] == 500
    assert result["p50"] == 120  # the median (3rd of 5)
    # At N=5, nearest-rank p90 and p99 both collapse to the single highest
    # sample -- an honest property of small-N percentiles, not a bug (see
    # the module's own docstring).
    assert result["p90"] == 500
    assert result["p99"] == 500


def test_compute_percentiles_is_order_independent() -> None:
    """The input order must not matter -- compute_percentiles sorts internally."""
    ascending = percentiles_module.compute_percentiles([10, 20, 30, 40, 50])
    descending = percentiles_module.compute_percentiles([50, 40, 30, 20, 10])
    assert ascending == descending


# --------------------------------------------------------------------------- #
# sample_target
# --------------------------------------------------------------------------- #
def test_sample_target_takes_the_configured_number_of_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under budget, sample_target takes exactly settings.x402_uptime_percentile_samples samples."""
    monkeypatch.setattr(settings, "x402_uptime_percentile_samples", 5)
    monkeypatch.setattr(settings, "x402_uptime_percentile_budget_s", 999.0)
    calls: list[int] = []
    latencies = iter([100, 110, 120, 130, 140])

    def _fake_check_target(*_a: object, **_kw: object) -> UptimeResult:
        calls.append(1)
        return _result(next(latencies))

    monkeypatch.setattr(percentiles_module, "check_target", _fake_check_target)

    primary, samples_ms = percentiles_module.sample_target(_URL)

    assert len(calls) == 5
    assert primary.response_time_ms == 100  # the FIRST sample, unchanged single-check contract
    assert samples_ms == [100, 110, 120, 130, 140]


def test_sample_target_always_takes_at_least_one_sample_even_at_a_tiny_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A near-zero wall-clock budget still gets the primary sample -- never zero."""
    monkeypatch.setattr(settings, "x402_uptime_percentile_samples", 5)
    monkeypatch.setattr(settings, "x402_uptime_percentile_budget_s", 0.0)
    monkeypatch.setattr(percentiles_module, "check_target", lambda *_a, **_kw: _result(100))

    primary, samples_ms = percentiles_module.sample_target(_URL)

    assert primary.response_time_ms == 100
    assert samples_ms == [100]


def test_sample_target_stops_early_once_the_wall_clock_budget_is_exceeded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow target must not be sampled samples-times-timeout worst case -- budget cuts it short."""
    monkeypatch.setattr(settings, "x402_uptime_percentile_samples", 5)
    monkeypatch.setattr(settings, "x402_uptime_percentile_budget_s", 5.0)

    calls: list[int] = []
    times = iter([0.0, 0.0, 6.0])  # started=0.0, then over-budget on the 2nd loop check

    def _fake_monotonic() -> float:
        return next(times, 6.0)

    def _fake_check_target(*_a: object, **_kw: object) -> UptimeResult:
        calls.append(1)
        return _result(100)

    monkeypatch.setattr(percentiles_module.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(percentiles_module, "check_target", _fake_check_target)

    primary, samples_ms = percentiles_module.sample_target(_URL)

    # The primary sample plus at most one more before the budget check trips.
    assert len(calls) <= 2
    assert primary.response_time_ms == 100
    assert samples_ms == [100] * len(calls)


def test_sample_target_samples_override_wins_over_a_higher_configured_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (finding #10): the explicit `samples=` override caps real fetches even when the configured setting is higher -- this is what api/routes.py's preview path relies on."""
    monkeypatch.setattr(settings, "x402_uptime_percentile_samples", 5)
    monkeypatch.setattr(settings, "x402_uptime_percentile_budget_s", 999.0)
    calls: list[int] = []

    def _fake_check_target(*_a: object, **_kw: object) -> UptimeResult:
        calls.append(1)
        return _result(100)

    monkeypatch.setattr(percentiles_module, "check_target", _fake_check_target)

    primary, samples_ms = percentiles_module.sample_target(_URL, samples=1)

    assert len(calls) == 1
    assert primary.response_time_ms == 100
    assert samples_ms == [100]


def test_sample_target_samples_override_of_none_falls_back_to_the_configured_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting `samples` (the real paid path's own call) must still use the configured setting."""
    monkeypatch.setattr(settings, "x402_uptime_percentile_samples", 3)
    monkeypatch.setattr(settings, "x402_uptime_percentile_budget_s", 999.0)
    calls: list[int] = []

    def _fake_check_target(*_a: object, **_kw: object) -> UptimeResult:
        calls.append(1)
        return _result(100)

    monkeypatch.setattr(percentiles_module, "check_target", _fake_check_target)

    percentiles_module.sample_target(_URL, samples=None)

    assert len(calls) == 3


def test_sample_target_never_calls_check_target_more_than_once_when_samples_is_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """samples=1 is a plain single check -- the pre-percentiles behaviour, unchanged."""
    monkeypatch.setattr(settings, "x402_uptime_percentile_samples", 1)
    monkeypatch.setattr(settings, "x402_uptime_percentile_budget_s", 999.0)
    calls: list[int] = []

    def _fake_check_target(*_a: object, **_kw: object) -> UptimeResult:
        calls.append(1)
        return _result(123)

    monkeypatch.setattr(percentiles_module, "check_target", _fake_check_target)

    primary, samples_ms = percentiles_module.sample_target(_URL)

    assert len(calls) == 1
    assert primary.response_time_ms == 123
    assert samples_ms == [123]
