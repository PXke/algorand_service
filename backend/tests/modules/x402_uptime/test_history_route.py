"""x402_uptime_history route: gating order, preview is a FIXED shape (never a real read), refund plumbing.

Same convention test_routes.py uses: require_paid_request is monkeypatched
directly to a canned PaymentResult, and history_service is swapped for a
spy/fake so the route's OWN branching runs for real.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Never

import pytest

from app.core.http import QueryParams, Request
from app.modules.x402 import circuit_breaker
from app.modules.x402.guard import PaymentResult
from app.modules.x402_uptime.api import routes as uptime_routes

_URL = "https://example.com/"


def _request(*, url: str = _URL, days: str = "", preview: str = "") -> Request:
    params = {"url": url}
    if days:
        params["days"] = days
    if preview:
        params["preview"] = preview
    return Request(
        method="GET",
        headers={},
        query_params=QueryParams(params),
        path_params={},
        body=b"",
        url=SimpleNamespace(scheme="http", host="localhost", path="/api/v1/x402/uptime/history"),
    )


def _settled_result(*, is_promo: bool = False) -> PaymentResult:
    return PaymentResult(
        error=None,
        payer="P" * 58,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="2000",
        payment_txid="" if is_promo else "TX-UPTIME-HIST-1",
        asset_id="10458941",
        network="algorand:SGO1GKSzyE7IEPItTxCByw9x8FmnrCDexi9/cOUJOiI=",
        is_promo=is_promo,
    )


def _preview_result() -> PaymentResult:
    return PaymentResult(error=None, is_preview=True)


def _never_called(*_a: object, **_kw: object) -> Never:
    raise AssertionError("must not be called")


@pytest.fixture(autouse=True)
def _already_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uptime_routes, "challenge_if_unpaid", lambda *_a, **_kw: None)


@pytest.fixture(autouse=True)
def _no_real_ip_rate_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uptime_routes, "ip_rate_limited", lambda _request: False)


def test_circuit_breaker_tripped_refuses_before_the_payment_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tripped resource never reaches require_paid_request -- no money at risk."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: True)
    monkeypatch.setattr(uptime_routes, "require_paid_request", _never_called)

    response = uptime_routes.x402_uptime_history(_request())

    assert response.status_code == 503


def test_bad_scheme_is_rejected_after_the_offer_but_before_charging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """normalize_url's rejection is a plain 400 -- reached only after challenge_if_unpaid (stubbed here), never charged."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)

    def _must_not_charge(*_a: object, **_kw: object) -> Never:
        raise AssertionError("a malformed url must never reach require_paid_request")

    monkeypatch.setattr(uptime_routes, "require_paid_request", _must_not_charge)

    response = uptime_routes.x402_uptime_history(_request(url="ftp://example.com/"))

    assert response.status_code == 400


def test_bad_days_is_a_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-integer `days` is a free 400 -- require_paid_request must never be reached."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", _never_called)

    response = uptime_routes.x402_uptime_history(_request(days="not-a-number"))

    assert response.status_code == 400


def test_days_is_clamped_to_the_configured_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLAUDE.md section 4: no unbounded listings -- an oversized `days=` is clamped, not honored."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes.settings, "x402_uptime_history_max_days", 30)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())
    captured: dict[str, object] = {}

    def _fake_read(_url: str, *, days: int) -> dict[str, object]:
        captured["days"] = days
        return {"history": []}

    monkeypatch.setattr(uptime_routes.history_service, "read", _fake_read)
    monkeypatch.setattr(uptime_routes, "mark_fulfilled", lambda *_a, **_kw: True)

    uptime_routes.x402_uptime_history(_request(days="999"))

    assert captured["days"] == 30


def test_real_paid_read_calls_history_service_and_marks_fulfilled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal paid call reads through history_service.read and settles for real."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())
    read_calls: list[tuple[str, int]] = []
    report = {
        "target_url": _URL,
        "days": 30,
        "checks_recorded": 3,
        "uptime_pct": 100.0,
        "latency_p50_ms": 100,
        "latency_p90_ms": 100,
        "latency_p99_ms": 100,
        "latency_min_ms": 100,
        "latency_max_ms": 100,
        "history": [],
    }
    monkeypatch.setattr(
        uptime_routes.history_service,
        "read",
        lambda url, *, days: (read_calls.append((url, days)), report)[1],
    )
    fulfilled: list[str] = []
    monkeypatch.setattr(
        uptime_routes, "mark_fulfilled", lambda txid, **_kw: fulfilled.append(txid) or True
    )

    response = uptime_routes.x402_uptime_history(_request(days="30"))

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["checks_recorded"] == 3
    assert body["settlement_tx_id"] == "TX-UPTIME-HIST-1"
    assert read_calls == [(_URL, 30)]
    assert fulfilled == ["TX-UPTIME-HIST-1"]


def test_preview_is_a_fixed_shape_and_never_calls_history_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike the check route's preview, this one must NOT run a real read -- see the route's own docstring."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(uptime_routes, "require_paid_request", lambda *_a, **_kw: _preview_result())
    monkeypatch.setattr(uptime_routes.history_service, "read", _never_called)

    response = uptime_routes.x402_uptime_history(_request(preview="true"))

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["target_url"] == "<preview>"
    assert body["checks_recorded"] == -1
    assert body["settlement_tx_id"] == "<preview>"


def test_promo_never_marks_fulfilled_and_carries_no_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean promo read returns via=promo with an empty settlement_tx_id and no mark_fulfilled call."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(
        uptime_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(is_promo=True)
    )
    monkeypatch.setattr(
        uptime_routes.history_service,
        "read",
        lambda _url, *, days: {"checks_recorded": 0, "history": []},  # noqa: ARG005
    )
    fulfilled: list[str] = []
    monkeypatch.setattr(
        uptime_routes, "mark_fulfilled", lambda txid, **_kw: fulfilled.append(txid) or True
    )

    response = uptime_routes.x402_uptime_history(_request())

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["via"] == "promo"
    assert body["settlement_tx_id"] == ""
    assert fulfilled == []
