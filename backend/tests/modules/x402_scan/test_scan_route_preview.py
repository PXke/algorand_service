"""x402_scan_url's `?preview=true` support (modules/x402/preview.py).

Same convention `test_scan_route_refund.py` uses: `require_paid_request` is
monkeypatched directly to a canned PaymentResult (the shared preview gate
itself is already covered by test_x402_preview.py), so this file only
exercises what's new here: the sandbox is never invoked for a preview, the
response is the fixed redacted shape, nothing is settled or marked
fulfilled, and a non-preview request is unaffected.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Never

import pytest

from app.core.http import QueryParams, Request
from app.modules.x402 import circuit_breaker
from app.modules.x402.guard import PaymentResult
from app.modules.x402_scan.api import routes as scan_routes

_URL = "https://example.com/file.zip"


def _request(*, preview: str = "true", body: bytes | None = None) -> Request:
    return Request(
        method="POST",
        headers={},
        query_params=QueryParams({"preview": preview} if preview else {}),
        path_params={},
        body=body if body is not None else json.dumps({"url": _URL}).encode(),
        url=SimpleNamespace(scheme="http", host="localhost", path="/api/v1/x402/scan/url"),
    )


def _preview_result() -> PaymentResult:
    return PaymentResult(error=None, is_preview=True)


@pytest.fixture(autouse=True)
def _already_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the pre-parse 402 for header-less requests -- every test here models a request past that gate, same convention as test_scan_route_refund.py."""
    monkeypatch.setattr(scan_routes, "challenge_if_unpaid", lambda *_a, **_kw: None)


def _never_scans(*_a: object, **_kw: object) -> Never:
    raise AssertionError("a preview request must never invoke the real scan")


def test_preview_never_runs_the_sandbox_or_fetches_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A preview call short-circuits at the payment gate; acquire_scan_slot/scan_url are never touched."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    captured: dict = {}

    def _spy_require_paid_request(*_a: object, **kwargs: object) -> PaymentResult:
        captured.update(kwargs)
        return _preview_result()

    monkeypatch.setattr(scan_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(scan_routes, "acquire_scan_slot", _never_scans)
    monkeypatch.setattr(scan_routes, "scan_url", _never_scans)

    response = scan_routes.x402_scan_url(_request())

    assert response.status_code == 200
    assert captured["preview"] is True


def test_preview_returns_the_fixed_redacted_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """The preview body is the fixed `_PREVIEW_OUTPUT` shape: same keys as a real report, all values sentinel/neutral, never a real verdict."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(scan_routes, "require_paid_request", lambda *_a, **_kw: _preview_result())

    response = scan_routes.x402_scan_url(_request())

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body == scan_routes._PREVIEW_OUTPUT
    assert set(body) == set(scan_routes._OUTPUT_EXAMPLE) | {"settlement_tx_id"}
    # Never a real safety verdict -- explicitly neither clean nor malicious.
    assert body["clamav"]["clean"] is None
    assert body["risk"]["malicious"] is None
    assert body["settlement_tx_id"] == "<preview>"


def test_preview_never_marks_fulfilled_or_carries_settlement_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing settled for a preview: no PAYMENT-RESPONSE header, mark_fulfilled never called."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(scan_routes, "require_paid_request", lambda *_a, **_kw: _preview_result())
    fulfilled: list[str] = []
    monkeypatch.setattr(
        scan_routes, "mark_fulfilled", lambda txid, **_kw: fulfilled.append(txid) or True
    )

    response = scan_routes.x402_scan_url(_request())

    assert "PAYMENT-RESPONSE" not in response.headers
    assert fulfilled == []


def test_preview_is_rate_limited_by_the_endpoints_own_ip_limiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The route's own scan_rate_limited runs before the offer is even built, so it still gates a preview call."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(scan_routes, "scan_rate_limited", lambda _request: True)
    monkeypatch.setattr(scan_routes, "require_paid_request", _never_scans)

    response = scan_routes.x402_scan_url(_request())

    assert response.status_code == 429


def test_non_preview_request_is_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: a plain (non-preview) request still goes through the real gate/scan path untouched."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    captured: dict = {}

    def _spy_require_paid_request(*_a: object, **kwargs: object) -> PaymentResult:
        captured.update(kwargs)
        return PaymentResult(
            error=None,
            payer="P" * 58,
            settlement_headers={"PAYMENT-RESPONSE": "ok"},
            amount_atomic="10000",
            payment_txid="TX-SCAN-NP",
            asset_id="10458941",
            network="algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
        )

    monkeypatch.setattr(scan_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(scan_routes, "acquire_scan_slot", lambda: None)
    monkeypatch.setattr(scan_routes, "release_scan_slot", lambda: None)
    monkeypatch.setattr(scan_routes, "scan_url", lambda _url: {"schema_version": 1, "status": "ok"})
    monkeypatch.setattr(scan_routes, "mark_fulfilled", lambda *_a, **_kw: True)

    response = scan_routes.x402_scan_url(_request(preview=""))

    assert captured["preview"] is False
    body = json.loads(response.description)
    assert body["settlement_tx_id"] == "TX-SCAN-NP"
