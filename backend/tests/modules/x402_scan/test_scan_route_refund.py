"""x402_scan_url's auto-refund + circuit-breaker retrofit.

Route-level: `require_paid_request` is monkeypatched directly to a canned
PaymentResult (real, promo, or error) -- the payment gate itself is covered elsewhere
(test_x402_preview.py/test_x402_promo.py). This file only exercises what's
new here: circuit-breaker-before-gate, refund-on-failure for a real payment,
and the promo path's specific error codes staying refund-free.
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
from app.modules.x402_scan.api import routes as scan_routes
from app.modules.x402_scan.services.concurrency import ConcurrencyLimitError
from app.modules.x402_scan.services.scan_service import FetchError, SandboxError

_URL = "https://example.com/file.zip"
_RESOURCE = "x402-scan-url"


def _request(*, body: bytes | None = None) -> Request:
    return Request(
        method="POST",
        headers={},
        query_params=QueryParams({}),
        path_params={},
        body=body if body is not None else json.dumps({"url": _URL}).encode(),
        url=SimpleNamespace(scheme="http", host="localhost", path="/api/v1/x402/scan/url"),
    )


def _settled_result(*, is_promo: bool = False) -> PaymentResult:
    return PaymentResult(
        error=None,
        payer="P" * 58,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="10000",
        payment_txid="" if is_promo else "TX-SCAN-1",
        asset_id="10458941",
        network="algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
        is_promo=is_promo,
    )


def _never_paid(*_a: object, **_kw: object) -> Never:
    raise AssertionError("require_paid_request must not be called while the breaker is tripped")


def _stub_report() -> dict[str, object]:
    return {"schema_version": 1, "status": "ok", "risk": {"score": 0.0}}


@pytest.fixture(autouse=True)
def _already_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the pre-parse 402 for header-less requests: every route test here models a request that already carries a payment (the gate is stubbed), so the unpaid challenge is out of scope. Its ordering has its own tests in tests/test_x402_unpaid_challenge.py."""
    monkeypatch.setattr(scan_routes, "challenge_if_unpaid", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _no_mark_fulfilled_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """mark_fulfilled talks to a real store by default -- no-op it, these tests only check control flow."""
    monkeypatch.setattr(scan_routes, "mark_fulfilled", lambda *_a, **_kw: True)


def test_circuit_breaker_tripped_refuses_before_the_payment_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tripped resource never reaches require_paid_request -- no money at risk."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: True)
    monkeypatch.setattr(scan_routes, "require_paid_request", _never_paid)

    response = scan_routes.x402_scan_url(_request())

    assert response.status_code == 503
    assert "temporarily_disabled" in response.description


def test_real_payment_failure_triggers_a_refund_not_a_bare_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A SandboxError, for a REAL payment, goes through run_with_refund instead of a plain error response."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(
        scan_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(is_promo=False)
    )

    refund_calls: list[dict[str, object]] = []

    def _fake_run_with_refund(
        result: PaymentResult, *, resource: str, product_write: Callable[[], object], **_kw: object
    ) -> Response:
        # Exercise the real product_write to prove it actually raises the
        # expected exception type, then return run_with_refund's own real
        # failure shape (a 503 mentioning refund) without needing a live
        # algod/Redis -- run_with_refund's own unit tests already cover its
        # internals (test_x402_refund.py).
        with pytest.raises(SandboxError):
            product_write()
        refund_calls.append({"resource": resource, "payer": result.payer})
        return Response(
            status_code=503,
            headers={},
            description=json.dumps({"error": "product_failed_refunded"}),
        )

    monkeypatch.setattr(scan_routes, "run_with_refund", _fake_run_with_refund)
    # acquire succeeds (a slot was available), scan_url is the one that
    # fails inside the sandbox -- still must release the slot afterward.
    monkeypatch.setattr(scan_routes, "acquire_scan_slot", lambda: None)
    monkeypatch.setattr(
        scan_routes, "scan_url", lambda _url: (_ for _ in ()).throw(SandboxError("sandbox crashed"))
    )
    released: list[bool] = []
    monkeypatch.setattr(scan_routes, "release_scan_slot", lambda: released.append(True))

    response = scan_routes.x402_scan_url(_request())

    assert response.status_code == 503
    assert "refund" in response.description.lower()
    assert refund_calls == [{"resource": _RESOURCE, "payer": "P" * 58}]
    assert released == [True]  # the slot was released even though scan_url raised


def test_concurrency_limit_at_capacity_never_reaches_payment_or_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test (2026-09-07 security review, finding 9): before this fix, the concurrency slot was acquired INSIDE the paid product write, so a caller who lost the race for a slot had already paid, then got refunded, and that refund counted against the circuit breaker -- five ordinary concurrent requests past MAX_CONCURRENT_SCANS could trip the no-TTL latch and take the endpoint offline for everyone until an admin reset it, for free. The slot is now acquired BEFORE the payment gate: a caller who can't get one is turned away with a plain 503 and is never charged, so require_paid_request/run_with_refund/the breaker must never be touched."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(
        scan_routes, "acquire_scan_slot", lambda: (_ for _ in ()).throw(ConcurrencyLimitError())
    )
    monkeypatch.setattr(scan_routes, "require_paid_request", _never_paid)

    def _must_not_refund(*_a: object, **_kw: object) -> Never:
        raise AssertionError("a capacity-only rejection must never reach run_with_refund")

    monkeypatch.setattr(scan_routes, "run_with_refund", _must_not_refund)

    def _breaker_must_not_be_touched(*_a: object, **_kw: object) -> Never:
        raise AssertionError("a capacity-only rejection must never count against the breaker")

    monkeypatch.setattr(circuit_breaker, "record_refund_failure", _breaker_must_not_be_touched)

    response = scan_routes.x402_scan_url(_request())

    assert response.status_code == 503
    assert "scan_unavailable" in response.description


def test_fetch_error_on_a_real_payment_is_never_refunded(monkeypatch: pytest.MonkeyPatch) -> None:
    """FetchError is caller-controlled (their url) -- must get the direct 422, payment kept, never a refund attempt (found-in-audit gap, 2026-09-02).

    Uses the REAL run_with_refund (not faked, unlike the parametrized test
    above) specifically to prove _scan_product_write's FetchError->
    PlatformError conversion actually reaches run_with_refund's own
    PlatformError exemption -- no Redis/algod involved on this path since
    that branch never touches the circuit breaker or the refund wallet.
    """
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(
        scan_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(is_promo=False)
    )
    monkeypatch.setattr(scan_routes, "acquire_scan_slot", lambda: None)
    monkeypatch.setattr(
        scan_routes,
        "scan_url",
        lambda _url: (_ for _ in ()).throw(FetchError("download failed")),
    )
    released: list[bool] = []
    monkeypatch.setattr(scan_routes, "release_scan_slot", lambda: released.append(True))

    def _breaker_must_not_be_touched(*_a: object, **_kw: object) -> Never:
        raise AssertionError("a caller-fault FetchError must never count against the breaker")

    monkeypatch.setattr(circuit_breaker, "record_refund_failure", _breaker_must_not_be_touched)

    response = scan_routes.x402_scan_url(_request())

    assert response.status_code == 422
    body = json.loads(response.description)
    assert body["error"]["code"] == "fetch_failed"
    assert response.headers["PAYMENT-RESPONSE"] == "ok"  # payment kept, receipt still served
    assert released == [True]  # the slot was still released


def test_real_payment_success_marks_fulfilled_and_returns_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No failure at all: unchanged happy path, product_write's result flows through untouched."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(
        scan_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(is_promo=False)
    )
    monkeypatch.setattr(scan_routes, "acquire_scan_slot", lambda: None)
    released: list[bool] = []
    monkeypatch.setattr(scan_routes, "release_scan_slot", lambda: released.append(True))
    monkeypatch.setattr(scan_routes, "scan_url", lambda _url: _stub_report())
    fulfilled: list[str] = []
    monkeypatch.setattr(
        scan_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append(txid),  # noqa: ARG005
    )

    response = scan_routes.x402_scan_url(_request())

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["status"] == "ok"
    assert body["settlement_tx_id"] == "TX-SCAN-1"
    assert fulfilled == ["TX-SCAN-1"]
    assert released == [True]


def test_promo_failure_keeps_the_original_specific_error_and_never_refunds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A promo call settles nothing, so a failure must NOT go through run_with_refund -- same 422 fetch_failed as before this retrofit."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(
        scan_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(is_promo=True)
    )
    monkeypatch.setattr(scan_routes, "acquire_scan_slot", lambda: None)
    released: list[bool] = []
    monkeypatch.setattr(scan_routes, "release_scan_slot", lambda: released.append(True))
    monkeypatch.setattr(
        scan_routes, "scan_url", lambda _url: (_ for _ in ()).throw(FetchError("nope"))
    )

    def _must_not_refund(*_a: object, **_kw: object) -> Never:
        raise AssertionError("run_with_refund must never be called for a promo result")

    monkeypatch.setattr(scan_routes, "run_with_refund", _must_not_refund)

    response = scan_routes.x402_scan_url(_request())

    assert response.status_code == 422
    assert "fetch_failed" in response.description
    assert released == [True]


def test_promo_success_is_unaffected_by_the_retrofit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a clean promo scan still returns via=promo with an empty settlement_tx_id, no mark_fulfilled."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(
        scan_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(is_promo=True)
    )
    monkeypatch.setattr(scan_routes, "acquire_scan_slot", lambda: None)
    monkeypatch.setattr(scan_routes, "release_scan_slot", lambda: None)
    monkeypatch.setattr(scan_routes, "scan_url", lambda _url: _stub_report())
    fulfilled: list[str] = []
    monkeypatch.setattr(
        scan_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append(txid),  # noqa: ARG005
    )

    response = scan_routes.x402_scan_url(_request())

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["via"] == "promo"
    assert body["settlement_tx_id"] == ""
    assert fulfilled == []
