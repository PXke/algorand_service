"""x402_uptime_check route: gating order, cache-hit/miss/stale product-write branching, refund-only-on-our-own-capacity-failure.

Same convention `tests/modules/x402_scan/test_scan_route_refund.py` uses:
require_paid_request is monkeypatched directly to a canned PaymentResult
(the payment gate itself is covered elsewhere), and the service-level
functions the real `_uptime_product_write` calls (get_cached/is_fresh/
target_over_budget/check_target/set_cached) are faked individually so the
route's OWN branching logic runs for real.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from types import SimpleNamespace
from typing import Never

import pytest

from app.core.http import QueryParams, Request, Response
from app.modules.x402 import circuit_breaker
from app.modules.x402.guard import PaymentResult
from app.modules.x402.paid_request import run_with_refund as _real_run_with_refund
from app.modules.x402_uptime.api import routes as uptime_routes
from app.modules.x402_uptime.services.cache import CachedCheck
from app.modules.x402_uptime.services.checker import UptimeResult

_RESOURCE = "x402-uptime-check"
_URL = "https://example.com/"


def _request(*, url: str = _URL) -> Request:
    return Request(
        method="POST",
        headers={},
        query_params=QueryParams({}),
        path_params={},
        body=json.dumps({"url": url}).encode(),
        url=SimpleNamespace(scheme="http", host="localhost", path="/api/v1/x402/uptime/check"),
    )


def _settled_result(*, is_promo: bool = False) -> PaymentResult:
    return PaymentResult(
        error=None,
        payer="P" * 58,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="1000",
        payment_txid="" if is_promo else "TX-UPTIME-1",
        asset_id="10458941",
        network="algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
        is_promo=is_promo,
    )


_UP_RESULT = UptimeResult(
    final_url=_URL,
    reachable=True,
    http_status=200,
    response_time_ms=100,
    resolved_ip="203.0.113.5",
    error="",
    redirect_chain=[_URL],
)


@pytest.fixture(autouse=True)
def _no_mark_fulfilled_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uptime_routes, "mark_fulfilled", lambda *_a, **_kw: True)


@pytest.fixture(autouse=True)
def _no_real_ip_rate_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uptime_routes, "ip_rate_limited", lambda _request: False)


def _never_paid(*_a: object, **_kw: object) -> Never:
    raise AssertionError("require_paid_request must not be called")


def test_circuit_breaker_tripped_refuses_before_the_payment_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tripped resource never reaches require_paid_request -- no money at risk."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: True)
    monkeypatch.setattr(uptime_routes, "require_paid_request", _never_paid)

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 503
    assert "temporarily_disabled" in response.description


def test_ip_rate_limited_refuses_before_the_payment_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A rate-limited caller IP gets 429 before require_paid_request ever runs."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "ip_rate_limited", lambda _request: True)
    monkeypatch.setattr(uptime_routes, "require_paid_request", _never_paid)

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 429


def test_bad_scheme_is_rejected_before_the_payment_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """normalize_url's own rejection (a non-http(s) scheme) is a plain 400, never charged."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", _never_paid)

    response = uptime_routes.x402_uptime_check(_request(url="ftp://example.com/"))

    assert response.status_code == 400


def test_fresh_cache_hit_never_calls_the_real_checker_or_the_target_limiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh cache hit is served as-is; neither the target limiter nor a real check ever runs."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())
    cached = CachedCheck(result=_UP_RESULT, cached_at=1000.0)
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: cached)
    monkeypatch.setattr(uptime_routes, "is_fresh", lambda _cached, **_kw: True)
    monkeypatch.setattr(
        uptime_routes,
        "target_over_budget",
        lambda _key: (_ for _ in ()).throw(AssertionError("must not be called on a fresh hit")),
    )
    monkeypatch.setattr(
        uptime_routes,
        "check_target",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not run a real check")),
    )

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["cache"] == "hit"
    assert body["reachable"] is True
    assert body["settlement_tx_id"] == "TX-UPTIME-1"


def test_cache_miss_under_budget_runs_a_real_check_and_caches_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No cache, budget available -> a real check runs and its result is cached."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: None)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: False)
    monkeypatch.setattr(uptime_routes, "check_target", lambda *_a, **_kw: _UP_RESULT)
    set_calls: list[str] = []
    monkeypatch.setattr(
        uptime_routes,
        "set_cached",
        lambda url, _result: (set_calls.append(url), 2000.0)[1],
    )

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["cache"] == "miss"
    assert body["reachable"] is True
    assert set_calls == [_URL]


def test_budget_exhausted_with_a_stale_cache_serves_it_labeled_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale cache present, target budget exhausted -> the stale result is served, honestly labeled, no real check."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())
    cached = CachedCheck(result=_UP_RESULT, cached_at=1000.0)
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: cached)
    monkeypatch.setattr(uptime_routes, "is_fresh", lambda _cached, **_kw: False)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: True)
    monkeypatch.setattr(
        uptime_routes,
        "check_target",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not run a real check")),
    )

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["cache"] == "stale"
    assert body["reachable"] is True


def test_budget_exhausted_with_no_cache_at_all_is_refunded_not_a_bare_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No cache, target budget exhausted -> OUR capacity failure, routed through run_with_refund, not a bare error."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: None)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: True)

    refund_calls: list[str] = []

    def _fake_run_with_refund(
        _result: PaymentResult,
        *,
        resource: str,
        product_write: Callable[[], object],
        **_kw: object,
    ) -> Response:
        with pytest.raises(uptime_routes.TargetBudgetExhaustedError):
            product_write()
        refund_calls.append(resource)
        return Response(
            status_code=503,
            headers={},
            description=json.dumps({"error": "product_failed_refunded"}),
        )

    monkeypatch.setattr(uptime_routes, "run_with_refund", _fake_run_with_refund)

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 503
    assert "refund" in response.description.lower()
    assert refund_calls == [_RESOURCE]


def test_a_down_result_is_a_normal_charged_answer_not_a_refund(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of this product: "down" must flow through the happy path, never the refund path."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: None)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: False)
    down_result = UptimeResult(
        final_url=_URL,
        reachable=False,
        http_status=0,
        response_time_ms=10,
        resolved_ip="",
        error="timeout",
        redirect_chain=[_URL],
    )
    monkeypatch.setattr(uptime_routes, "check_target", lambda *_a, **_kw: down_result)
    monkeypatch.setattr(uptime_routes, "set_cached", lambda _url, _result: 2000.0)

    fulfilled: list[str] = []
    monkeypatch.setattr(
        uptime_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append(txid),  # noqa: ARG005
    )
    # Use the real run_with_refund to prove _uptime_product_write never raises for "down".
    monkeypatch.setattr(uptime_routes, "run_with_refund", _real_run_with_refund)

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["reachable"] is False
    assert body["error"] == "timeout"
    assert body["cache"] == "miss"
    assert fulfilled == ["TX-UPTIME-1"]


def test_promo_success_never_marks_fulfilled_and_carries_no_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean promo check returns via=promo with an empty settlement_tx_id and no mark_fulfilled call."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(
        uptime_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(is_promo=True)
    )
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: None)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: False)
    monkeypatch.setattr(uptime_routes, "check_target", lambda *_a, **_kw: _UP_RESULT)
    monkeypatch.setattr(uptime_routes, "set_cached", lambda _url, _result: 2000.0)
    fulfilled: list[str] = []
    monkeypatch.setattr(
        uptime_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append(txid),  # noqa: ARG005
    )

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["via"] == "promo"
    assert body["settlement_tx_id"] == ""
    assert fulfilled == []


def test_promo_budget_exhausted_with_no_cache_returns_503_without_a_refund_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A promo call settles nothing, so a capacity failure must NOT go through run_with_refund."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(
        uptime_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(is_promo=True)
    )
    monkeypatch.setattr(uptime_routes, "get_cached", lambda _url: None)
    monkeypatch.setattr(uptime_routes, "target_over_budget", lambda _key: True)

    def _must_not_refund(*_a: object, **_kw: object) -> Never:
        raise AssertionError("run_with_refund must never be called for a promo result")

    monkeypatch.setattr(uptime_routes, "run_with_refund", _must_not_refund)

    response = uptime_routes.x402_uptime_check(_request())

    assert response.status_code == 503
    assert "uptime_check_unavailable" in response.description
