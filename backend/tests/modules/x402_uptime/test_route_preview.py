"""x402_uptime_check's `?preview=true` support (modules/x402/preview.py).

Same convention `test_routes.py` uses: `require_paid_request` is
monkeypatched directly to a canned PaymentResult (the shared preview gate
itself is already covered by test_x402_preview.py) and the service-level
functions `_uptime_product_write` calls are faked individually, so the
route's OWN preview branching runs for real. Unlike x402_scan's preview
(which never runs the real product write at all), this route's preview
DOES run the real `_uptime_product_write` -- same SSRF guard, cache and
per-target budget as a real payment -- so these tests specifically prove
that reuse and the one redaction (response_time_ms) it applies on top.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Never

import pytest

from app.core.config import settings
from app.core.http import QueryParams, Request
from app.modules.x402 import circuit_breaker
from app.modules.x402.guard import PaymentResult
from app.modules.x402_uptime.api import routes as uptime_routes
from app.modules.x402_uptime.services import percentiles as percentiles_module
from app.modules.x402_uptime.services.cache import CachedCheck
from app.modules.x402_uptime.services.checker import UptimeResult

_URL = "https://example.com/"

_UP_RESULT = UptimeResult(
    final_url=_URL,
    reachable=True,
    http_status=200,
    response_time_ms=137,
    resolved_ip="203.0.113.5",
    error="",
    redirect_chain=[_URL],
)


def _request(*, preview: str = "true", url: str = _URL) -> Request:
    return Request(
        method="POST",
        headers={},
        query_params=QueryParams({"preview": preview} if preview else {}),
        path_params={},
        body=json.dumps({"url": url}).encode(),
        url=SimpleNamespace(scheme="http", host="localhost", path="/api/v1/x402/uptime/check"),
    )


def _preview_result() -> PaymentResult:
    return PaymentResult(error=None, is_preview=True)


@pytest.fixture(autouse=True)
def _already_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uptime_routes, "challenge_if_unpaid", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _no_real_ip_rate_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uptime_routes, "ip_rate_limited", lambda _request: False)


def _never_called(*_a: object, **_kw: object) -> Never:
    raise AssertionError("must not be called")


def test_preview_runs_the_real_check_but_redacts_only_the_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cache-miss, under-budget preview runs the REAL checker (same as a paid call) and returns real reachability/status/redirects/resolved_ip -- only response_time_ms and settlement_tx_id are sentinel."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    captured: dict = {}

    def _spy_require_paid_request(*_a: object, **kwargs: object) -> PaymentResult:
        captured.update(kwargs)
        return _preview_result()

    monkeypatch.setattr(uptime_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: None)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: False)
    monkeypatch.setattr(uptime_routes, "sample_target", lambda _url, **_kw: (_UP_RESULT, [137]))
    set_calls: list[str] = []
    monkeypatch.setattr(
        uptime_routes,
        "set_cached",
        lambda url, _result, _latencies: (set_calls.append(url), 2000.0)[1],
    )

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 200
    assert captured["preview"] is True
    body = json.loads(response.description)
    assert body["reachable"] is True
    assert body["http_status"] == 200
    assert body["resolved_ip"] == "203.0.113.5"
    assert body["redirect_chain"] == [_URL]
    assert body["response_time_ms"] == -1  # redacted, not the real 137
    assert body["latency_p50_ms"] == -1  # every latency figure is redacted, not just the average
    assert body["settlement_tx_id"] == "<preview>"
    # A real check ran (and was cached), same as a paid call would.
    assert set_calls == [_URL]


def test_preview_serves_a_fresh_cache_hit_without_a_real_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh cache hit is served as-is (minus latency) for a preview too -- no real checker call, no target-budget touch."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _preview_result())
    cached = CachedCheck(result=_UP_RESULT, cached_at=1000.0)
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: cached)
    monkeypatch.setattr(uptime_routes, "is_fresh", lambda _cached, **_kw: True)
    monkeypatch.setattr(uptime_routes, "target_over_budget", _never_called)
    monkeypatch.setattr(uptime_routes, "sample_target", _never_called)

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["cache"] == "hit"
    assert body["response_time_ms"] == -1
    assert body["settlement_tx_id"] == "<preview>"


def test_preview_with_budget_exhausted_and_no_cache_is_a_503_never_a_refund(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OUR own capacity limit hit during a preview: a plain 503, run_with_refund is never touched (nothing was ever paid)."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _preview_result())
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: None)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: True)
    monkeypatch.setattr(uptime_routes, "run_with_refund", _never_called)

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 503
    assert "uptime_check_unavailable" in response.description


def test_preview_never_marks_fulfilled_or_carries_settlement_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing settled for a preview: no PAYMENT-RESPONSE header, mark_fulfilled never called."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _preview_result())
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: None)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: False)
    monkeypatch.setattr(uptime_routes, "sample_target", lambda _url, **_kw: (_UP_RESULT, [137]))
    monkeypatch.setattr(uptime_routes, "set_cached", lambda _url, _result, _latencies: 2000.0)
    fulfilled: list[str] = []
    monkeypatch.setattr(
        uptime_routes, "mark_fulfilled", lambda txid, **_kw: fulfilled.append(txid) or True
    )

    response = uptime_routes.x402_uptime_check(_request())

    assert "PAYMENT-RESPONSE" not in response.headers
    assert fulfilled == []


def test_preview_still_gated_by_the_ssrf_guard_via_normalize_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-http(s) scheme is rejected before the payment gate for a preview too -- there is no preview-only bypass of normalize_url's own SSRF guard."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", _never_called)

    response = uptime_routes.x402_uptime_check(_request(url="ftp://example.com/"))

    assert response.status_code == 400


def test_preview_never_takes_more_than_one_real_sample_regardless_of_configured_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (finding #10): a preview must never run the full multi-sample percentile fetch.

    The REAL sample_target() is left in place (not mocked) so its own
    sample-count logic actually runs; only the underlying real-fetch
    primitive (check_target) is spied on. With
    x402_uptime_percentile_samples configured high (5) and plenty of
    wall-clock budget, a preview call must still make exactly ONE real
    fetch attempt against the caller-supplied target -- proving
    `_handle_preview_check`'s `max_samples=1` actually caps the real
    amplification, not just the displayed figures.
    """
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _preview_result())
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: None)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: False)
    monkeypatch.setattr(uptime_routes, "set_cached", lambda _url, _result, _latencies: 2000.0)
    monkeypatch.setattr(settings, "x402_uptime_percentile_samples", 5)
    monkeypatch.setattr(settings, "x402_uptime_percentile_budget_s", 999.0)
    calls: list[int] = []

    def _fake_check_target(*_a: object, **_kw: object) -> UptimeResult:
        calls.append(1)
        return _UP_RESULT

    monkeypatch.setattr(percentiles_module, "check_target", _fake_check_target)

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 200
    assert len(calls) == 1, "preview must take exactly one real sample, never the configured 5"


def test_non_preview_request_still_takes_the_full_configured_sample_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counterpart to the regression above: a real paid call is unaffected -- it still amplifies as designed."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(
        uptime_routes,
        "require_paid_request",
        lambda *_a, **_kw: PaymentResult(
            error=None,
            payer="P" * 58,
            settlement_headers={"PAYMENT-RESPONSE": "ok"},
            amount_atomic="1000",
            payment_txid="TX-UPTIME-FULL",
            asset_id="10458941",
            network="algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
        ),
    )
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: None)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: False)
    monkeypatch.setattr(uptime_routes, "set_cached", lambda _url, _result, _latencies: 2000.0)
    monkeypatch.setattr(uptime_routes, "mark_fulfilled", lambda *_a, **_kw: True)
    monkeypatch.setattr(settings, "x402_uptime_percentile_samples", 5)
    monkeypatch.setattr(settings, "x402_uptime_percentile_budget_s", 999.0)
    calls: list[int] = []

    def _fake_check_target(*_a: object, **_kw: object) -> UptimeResult:
        calls.append(1)
        return _UP_RESULT

    monkeypatch.setattr(percentiles_module, "check_target", _fake_check_target)

    response = uptime_routes.x402_uptime_check(_request(preview=""))

    assert response.status_code == 200
    assert len(calls) == 5


def test_non_preview_request_is_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a plain (non-preview) request still gets the real, un-redacted latency."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    captured: dict = {}

    def _spy_require_paid_request(*_a: object, **kwargs: object) -> PaymentResult:
        captured.update(kwargs)
        return PaymentResult(
            error=None,
            payer="P" * 58,
            settlement_headers={"PAYMENT-RESPONSE": "ok"},
            amount_atomic="1000",
            payment_txid="TX-UPTIME-NP",
            asset_id="10458941",
            network="algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
        )

    monkeypatch.setattr(uptime_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: None)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: False)
    monkeypatch.setattr(uptime_routes, "sample_target", lambda _url, **_kw: (_UP_RESULT, [137]))
    monkeypatch.setattr(uptime_routes, "set_cached", lambda _url, _result, _latencies: 2000.0)
    monkeypatch.setattr(uptime_routes, "mark_fulfilled", lambda *_a, **_kw: True)

    response = uptime_routes.x402_uptime_check(_request(preview=""))

    assert captured["preview"] is False
    body = json.loads(response.description)
    assert body["response_time_ms"] == 137
    assert body["latency_p50_ms"] == 137  # not redacted for a non-preview call
    assert body["settlement_tx_id"] == "TX-UPTIME-NP"
