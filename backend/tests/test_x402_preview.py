"""Preview-mode tests for the shared x402 payment gate (modules/x402/preview.py).

Fully offline: the facilitator is a stub that never touches the network
(same shape as test_x402_directory.py's), Redis is a fake at the get_redis
seam. Exercises guard.require_payment's `preview` kwarg, paid_request.
require_paid_request's `preview` kwarg, and the worked reference wiring in
x402_catalog's x402_ping route.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Never

import pytest

pytest.importorskip("x402")

from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2
from x402.schemas.payments import PaymentRequirements
from x402.schemas.responses import SupportedKind, SupportedResponse
from x402.schemas.v1 import PaymentRequirementsV1
from x402.server import x402ResourceServerSync

from app.core import rate_limit as rate_limit_core
from app.core import serialization
from app.core.config import settings
from app.core.http import QueryParams, Request
from app.modules.x402 import circuit_breaker
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import paid_request as payment_service
from app.modules.x402 import preview as preview_module
from app.modules.x402 import replay as replay_module
from app.modules.x402.settlement import InMemorySettlementStore
from app.modules.x402_catalog.api import routes as catalog_routes

_PAY_TO = "A" * 58


# --------------------------------------------------------------------------- #
# Fakes (same shape as test_x402_directory.py's)
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Enough of the Redis API for the preview rate limiter and the refund circuit breaker."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True

    def get(self, key: str) -> str | None:
        return self.store.get(key)


class _BrokenRedis:
    """Every operation fails, to exercise the fail-open rate-limit path."""

    def incr(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def expire(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")


class _StubFacilitator:
    """Canned /supported. verify()/settle() raise -- proves preview never reaches them."""

    def get_supported(self) -> SupportedResponse:
        return SupportedResponse(
            kinds=[SupportedKind(x402_version=2, scheme="exact", network=ALGORAND_TESTNET_CAIP2)]
        )

    def verify(
        self, _payload: dict, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("verify() must not be called for a preview request")

    def settle(
        self, _payload: dict, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("settle() must not be called for a preview request")


def _stub_resource_server() -> x402ResourceServerSync:
    server = x402ResourceServerSync(_StubFacilitator())
    x402_client.register_tagged_exact_avm_scheme(server, ALGORAND_TESTNET_CAIP2)
    server.initialize()
    return server


def _request(
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    query: dict[str, Any] | None = None,
    path: str = "/api/v1/x402/ping",
) -> Request:
    return Request(
        method=method,
        headers=headers or {},
        query_params=QueryParams(query or {}),
        path_params={},
        body=b"",
        url=SimpleNamespace(scheme="http", host="localhost", path=path),
    )


@pytest.fixture
def testnet_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the gate at TestNet and the offline stub facilitator."""
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", _PAY_TO)
    monkeypatch.setattr(x402_guard, "get_resource_server", _stub_resource_server)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Swap the Redis seams the preview rate limiter and replay claim read."""
    client = _FakeRedis()
    # preview.py's rate limiter goes through the shared incr_with_expiry
    # primitive, which reads app.core.rate_limit's own get_redis seam --
    # preview.py itself imports no Redis client directly.
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(replay_module, "get_redis", lambda **_kw: client)
    # circuit_breaker.is_tripped reads app.core.redis_client's get_redis
    # directly (not through incr_with_expiry), so it needs its own seam --
    # same one-binding-per-importer convention every other x402 module here
    # already follows. Without this, ping's now-mandatory pre-gate breaker
    # check fails closed against the real (blocked) Redis connection and
    # every ping test gets a 503 regardless of what it is actually testing.
    monkeypatch.setattr(circuit_breaker, "get_redis", lambda **_kw: client)
    return client


@pytest.fixture
def settlement_store() -> InMemorySettlementStore:
    """A fresh in-memory settlement ledger per test."""
    return InMemorySettlementStore()


# --------------------------------------------------------------------------- #
# preview_requested: the query-param trigger
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", ["true", "True", "1", "yes", "YES"])
def test_preview_requested_recognizes_truthy_values(raw: str) -> None:
    """?preview=true/True/1/yes/YES are all recognized, case-insensitively."""
    assert preview_module.preview_requested(_request(query={"preview": raw})) is True


@pytest.mark.parametrize("raw", ["", "false", "0", "no", "maybe"])
def test_preview_requested_rejects_everything_else(raw: str) -> None:
    """Any other value (including the empty string) is not a preview request."""
    assert preview_module.preview_requested(_request(query={"preview": raw})) is False


def test_preview_not_requested_when_absent() -> None:
    """No ?preview= query param at all is not a preview request."""
    assert preview_module.preview_requested(_request()) is False


# --------------------------------------------------------------------------- #
# guard.require_payment: the bypass never reaches the facilitator
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings")
def test_preview_bypasses_the_gate_entirely_at_the_guard_level() -> None:
    """preview=True short-circuits before build_payment_offer or the resource server are ever touched."""
    result = x402_guard.require_payment(
        _request(), price="$0.10", resource="x402-test", preview=True
    )

    assert result.error is None
    assert result.is_preview is True
    assert result.is_promo is False
    assert result.payer is None
    assert result.payment_txid is None
    assert result.asset_id is None
    assert result.network is None
    assert result.settlement_headers == {}


@pytest.mark.usefixtures("testnet_settings")
def test_preview_false_is_unaffected_by_the_new_kwarg() -> None:
    """Not passing preview (or passing False) is byte-for-byte the pre-existing 402 behaviour."""
    from x402.http.utils import decode_payment_required_header

    response = x402_guard.require_payment(_request(), price="$0.10", resource="x402-test").error

    assert response is not None
    assert response.status_code == 402
    offer = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"]).accepts[0]
    assert offer.pay_to == _PAY_TO


# --------------------------------------------------------------------------- #
# paid_request.require_paid_request: preview wiring
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_preview_never_calls_the_real_payment_gate(
    settlement_store: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """require_paid_request(preview=True) must never reach require_payment's non-preview path, the replay claim, or the ledger."""

    def _fail_if_not_preview(*_a: object, **kw: object) -> Never:
        raise AssertionError(f"require_payment must be called with preview=True, got {kw}")

    def _guarded_require_payment(*args: object, **kw: object) -> x402_guard.PaymentResult:
        if not kw.get("preview"):
            _fail_if_not_preview(*args, **kw)
        return x402_guard.PaymentResult(error=None, is_preview=True)

    monkeypatch.setattr(payment_service, "require_payment", _guarded_require_payment)

    result = payment_service.require_paid_request(
        _request(query={"preview": "true"}),
        price="$0.10",
        resource="x402-test",
        settlement_store=settlement_store,
        preview=True,
    )

    assert result.error is None
    assert result.is_preview is True
    assert settlement_store.settlements == []


@pytest.mark.usefixtures("fake_redis")
def test_preview_writes_nothing_to_the_settlement_ledger(
    settlement_store: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """record_settlement must never be called on the preview path -- not even to write a marked-preview row."""

    def _never_record(*_a: object, **_kw: object) -> Never:
        raise AssertionError("record_settlement must not be called for a preview response")

    monkeypatch.setattr(payment_service, "record_settlement", _never_record)
    monkeypatch.setattr(
        payment_service,
        "require_payment",
        lambda *_a, **_kw: x402_guard.PaymentResult(error=None, is_preview=True),
    )

    result = payment_service.require_paid_request(
        _request(query={"preview": "true"}),
        price="$0.10",
        resource="x402-test",
        settlement_store=settlement_store,
        preview=True,
    )

    assert result.is_preview is True
    assert settlement_store.settlements == []


def test_mark_fulfilled_on_a_preview_result_is_a_safe_noop(
    settlement_store: InMemorySettlementStore,
) -> None:
    """A route that accidentally calls mark_fulfilled on a preview result gets a clean no-op: no exception, no phantom row."""
    result = x402_guard.PaymentResult(error=None, is_preview=True)

    marked = payment_service.mark_fulfilled(
        result.payment_txid, resource="x402-test", store=settlement_store
    )

    assert marked is False
    assert settlement_store.settlements == []


# --------------------------------------------------------------------------- #
# Preview is rate-limited
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_preview_is_rate_limited_per_ip_and_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preview requests share a per-IP hourly budget, separate IPs get their own, and a Redis failure fails open."""
    monkeypatch.setattr(settings, "x402_preview_rate_limit_per_hour", 2)
    monkeypatch.setattr(
        payment_service,
        "require_payment",
        lambda *_a, **_kw: x402_guard.PaymentResult(error=None, is_preview=True),
    )
    headers = {"x-real-ip": "203.0.113.9"}

    r1 = payment_service.require_paid_request(
        _request(headers=headers), price="$0.10", resource="x402-test", preview=True
    )
    r2 = payment_service.require_paid_request(
        _request(headers=headers), price="$0.10", resource="x402-test", preview=True
    )
    r3 = payment_service.require_paid_request(
        _request(headers=headers), price="$0.10", resource="x402-test", preview=True
    )
    assert r1.is_preview is True
    assert r2.is_preview is True
    assert r3.error is not None
    assert r3.error.status_code == 429

    # A different IP has its own budget.
    other_ip = payment_service.require_paid_request(
        _request(headers={"x-real-ip": "203.0.113.10"}),
        price="$0.10",
        resource="x402-test",
        preview=True,
    )
    assert other_ip.is_preview is True

    # Redis down -> fails open, preview still goes through.
    monkeypatch.setattr(rate_limit_core, "get_redis", _BrokenRedis)
    broken = payment_service.require_paid_request(
        _request(headers=headers), price="$0.10", resource="x402-test", preview=True
    )
    assert broken.is_preview is True


# --------------------------------------------------------------------------- #
# x402_ping: the worked example, both branches
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_ping_preview_serves_a_redacted_response_with_no_facilitator_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """?preview=true on the worked example gets the redacted shape, never touching the stub facilitator's verify/settle."""
    monkeypatch.setattr(settings, "x402_ping_price", "$0.001")

    response = catalog_routes.x402_ping(_request(query={"preview": "true"}))

    assert response.status_code == 200
    body = serialization.loads(response.description)
    assert body == {"pong": True, "settlement_tx_id": "<preview>", "served_at_epoch": 0}


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_ping_non_preview_behavior_is_completely_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression: the exact same 402-without-payment shape as before preview/promo existed."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_ping_price", "$0.001")

    response = catalog_routes.x402_ping(_request())

    assert response.status_code == 402
    offer = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"]).accepts[0]
    assert offer.pay_to == _PAY_TO


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_ping_settled_payment_is_unaffected_by_preview_or_promo_plumbing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real settled payment still returns the real receipt and calls mark_fulfilled, exactly as before."""
    settled = x402_guard.PaymentResult(
        error=None,
        payer="P" * 58,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="1000",
        payment_txid="TX999",
        asset_id="10458941",
        network=ALGORAND_TESTNET_CAIP2,
    )
    monkeypatch.setattr(catalog_routes, "require_paid_request", lambda *_a, **_kw: settled)
    marked: list[str] = []
    monkeypatch.setattr(
        catalog_routes, "mark_fulfilled", lambda tx_id, **_kw: marked.append(tx_id) or True
    )

    response = catalog_routes.x402_ping(_request())

    assert response.status_code == 200
    body = serialization.loads(response.description)
    assert body["settlement_tx_id"] == "TX999"
    assert body["pong"] is True
    assert "via" not in body
    assert marked == ["TX999"]
