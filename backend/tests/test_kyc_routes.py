"""KYC route-level regressions: the shared paid gate, the free-endpoint limits, and the pre-gate wallet check.

Fully offline. The facilitator is never reached (the payment gate itself is
monkeypatched at modules/x402/paid_request.py's own seam, exactly as
test_x402_directory.py does), Redis is a fake at the get_redis seams, and the
settlement ledger is modules/x402's in-memory store installed at its shared
setter. Nothing here settles a real payment or touches the network.

What is covered:

  K-1  kyc_verify goes through require_paid_request, so a replayed payment
       header is refused and a settled payment lands in the SAME shared
       settlement ledger the other four paid modules write to. Both were
       missing entirely while the route called the bare require_payment.
  K-2  both free endpoints are rate-limited (per IP, and per wallet on
       enroll), the wallet limit runs before the outbound indexer call, and
       every limiter fails OPEN on a Redis outage.
  K-3a a malformed wallet is a 400 before the gate, so nobody is charged for
       a lookup that could never have matched.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Any, Never

import pytest

pytest.importorskip("x402")

from algosdk.encoding import encode_address
from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.kyc.api import routes as kyc_routes
from app.modules.kyc.models.domain import StoredEnrollment
from app.modules.kyc.services import rate_limit as kyc_rate_limit
from app.modules.kyc.services.enrollment_service import EnrollmentService
from app.modules.kyc.services.indexer_client import WalletSignals
from app.modules.kyc.services.lookup_service import LookupService
from app.modules.kyc.services.payout_service import PayoutResult
from app.modules.kyc.stores.memory import InMemoryEnrollmentStore
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import paid_request as payment_service
from app.modules.x402 import replay as replay_module
from app.modules.x402.settlement import InMemorySettlementStore, set_settlement_store

# Real, checksum-valid Algorand addresses. kyc_verify validates with algosdk's
# own is_valid_address, so the "X" * 58 placeholders the service-level KYC
# tests use would (correctly) be rejected by the route.
_WALLET = encode_address(bytes([1]) + bytes(31))
_PAYER = encode_address(bytes([2]) + bytes(31))
_PAY_TO = encode_address(bytes([3]) + bytes(31))


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Enough of the Redis API for the replay claim and the rate-limit counters."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expires: dict[str, int] = {}

    def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool | None:
        if nx and key in self.store:
            return None
        self.store[key] = value
        if ex is not None:
            self.expires[key] = ex
        return True

    def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        self.expires[key] = seconds
        return True


class _BrokenRedis:
    """Every operation fails, to exercise the fail-open paths."""

    def set(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def delete(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def incr(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def expire(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")


def _request(
    *,
    method: str = "GET",
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    query: dict[str, Any] | None = None,
    path: str = "/api/v1/kyc/verify",
) -> Request:
    return Request(
        method=method,
        headers=headers or {},
        query_params=QueryParams(query or {}),
        path_params={},
        body=body,
        url=SimpleNamespace(scheme="http", host="localhost", path=path),
    )


def _settled_result(payer: str = _PAYER, txid: str = "TX123") -> x402_guard.PaymentResult:
    return x402_guard.PaymentResult(
        error=None,
        payer=payer,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="50000",
        payment_txid=txid,
        asset_id="10458941",
        network=ALGORAND_TESTNET_CAIP2,
    )


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Swap both Redis seams for one in-process fake shared by replay and rate limiting."""
    client = _FakeRedis()
    monkeypatch.setattr(replay_module, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
    return client


@pytest.fixture
def ledger() -> Iterator[InMemorySettlementStore]:
    """The shared settlement ledger, installed at modules/x402's own seam and torn down.

    Installed at the shared seam rather than passed to the route: the point of
    K-1 is that KYC now writes to the SAME ledger the other paid modules use,
    and a test that handed the route its own store would not prove that.
    """
    store = InMemorySettlementStore()
    set_settlement_store(store)
    yield store
    set_settlement_store(None)


@pytest.fixture
def paid_lookup(monkeypatch: pytest.MonkeyPatch) -> InMemoryEnrollmentStore:
    """Point kyc_verify at an in-memory enrollment store with the payout leg stubbed out.

    The payout is not what these tests are about, and letting the real one run
    would reach algod. It is stubbed to a "skipped" result — the same shape an
    unconfigured payout wallet produces in production.
    """
    store = InMemoryEnrollmentStore()
    monkeypatch.setattr(
        kyc_routes,
        "lookup_service",
        LookupService(store=store, payout_fn=lambda **_kw: PayoutResult(status="skipped")),
    )
    return store


@pytest.fixture
def testnet_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the gate at TestNet with a real pay-to address."""
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", _PAY_TO)


# --------------------------------------------------------------------------- #
# K-1: the shared paid gate — replay protection and the settlement ledger
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "paid_lookup")
def test_a_replayed_payment_header_on_kyc_verify_is_rejected(
    fake_redis: _FakeRedis,
    ledger: InMemorySettlementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A payment header already claimed is rejected with 409 without the gate — and so the facilitator's settle — ever running again."""
    calls: list[str] = []

    def _never_gate(*_args: object, **_kwargs: object) -> Never:
        calls.append("gate")
        raise AssertionError("require_payment must not run for a replayed header")

    # Pre-claim the header, as a first, successful request would have.
    fake_redis.set(replay_module._replay_key("spent-header"), "1", nx=True, ex=900)
    monkeypatch.setattr(payment_service, "require_payment", _never_gate)

    response = kyc_routes.kyc_verify(
        _request(headers={"PAYMENT-SIGNATURE": "spent-header"}, query={"wallet": _WALLET})
    )

    assert response.status_code == 409
    assert "payment_replayed" in response.description
    assert calls == []
    # Nothing settled, so nothing may be billed for.
    assert ledger.settlements == []


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "paid_lookup")
def test_a_settled_kyc_lookup_writes_a_row_to_the_shared_settlement_ledger(
    ledger: InMemorySettlementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A settled kyc-verify payment lands in the shared ledger with asset id, amount, txid, payer, resource, network and a UTC timestamp."""
    monkeypatch.setattr(payment_service, "require_payment", lambda *_a, **_kw: _settled_result())

    response = kyc_routes.kyc_verify(
        _request(headers={"PAYMENT-SIGNATURE": "fresh-header"}, query={"wallet": _WALLET})
    )

    assert response.status_code == 200
    assert len(ledger.settlements) == 1
    record = ledger.settlements[0]
    assert record.tx_id == "TX123"
    assert record.asset_id == "10458941"
    assert record.amount_atomic == "50000"
    assert record.payer == _PAYER
    # The resource id KYC's payments are booked under in the shared ledger.
    assert record.resource == "kyc-verify"
    assert record.network == ALGORAND_TESTNET_CAIP2
    assert record.settled_at_epoch > 0


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "paid_lookup")
def test_kyc_verify_uses_the_shared_paid_wrapper_not_the_bare_gate(
    ledger: InMemorySettlementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for the original bug: patching the BARE gate in modules/x402.guard must not reach the route, because the route goes through require_paid_request.

    Written as a negative because that is the actual defect shape — the route
    called guard.require_payment directly, which is why it had neither replay
    protection nor a ledger row. If someone reverts to the bare gate, this
    fails: the guard patch would take effect and no ledger row would appear.
    """
    monkeypatch.setattr(x402_guard, "require_payment", lambda *_a, **_kw: _settled_result())
    monkeypatch.setattr(payment_service, "require_payment", lambda *_a, **_kw: _settled_result())

    kyc_routes.kyc_verify(
        _request(headers={"PAYMENT-SIGNATURE": "another-header"}, query={"wallet": _WALLET})
    )

    assert [item.resource for item in ledger.settlements] == ["kyc-verify"]


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "paid_lookup")
def test_a_kyc_payment_that_never_settles_releases_its_claim(
    fake_redis: _FakeRedis,
    ledger: InMemorySettlementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A header whose payment failed is un-claimed and un-billed, so the payer can retry it."""
    from app.core.http_errors import json_error_response

    monkeypatch.setattr(
        payment_service,
        "require_payment",
        lambda *_a, **_kw: x402_guard.PaymentResult(
            error=json_error_response(402, "settlement_failed", "nope")
        ),
    )

    kyc_routes.kyc_verify(
        _request(headers={"PAYMENT-SIGNATURE": "unlucky-header"}, query={"wallet": _WALLET})
    )

    assert replay_module._replay_key("unlucky-header") not in fake_redis.store
    assert ledger.settlements == []


# --------------------------------------------------------------------------- #
# K-3a: the pre-gate wallet format check
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "wallet",
    [
        "not-an-address",
        # 58 characters, so a bare length check would wave it through, but the
        # base32 checksum does not verify — nobody's address, ever.
        "A" * 58,
        _WALLET[:-1] + ("B" if _WALLET[-1] != "B" else "C"),
    ],
)
@pytest.mark.usefixtures("testnet_settings", "fake_redis", "paid_lookup")
def test_a_malformed_wallet_is_rejected_before_the_payment_gate(
    wallet: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A syntactically impossible wallet is a 400, not a 402 — nobody is charged for a lookup that could never have matched."""

    def _never_gate(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("the payment gate must not run for a malformed wallet")

    monkeypatch.setattr(kyc_routes, "require_paid_request", _never_gate)

    response = kyc_routes.kyc_verify(_request(query={"wallet": wallet}))

    assert response.status_code == 400
    assert "invalid_request" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "paid_lookup")
def test_a_missing_wallet_is_still_rejected_before_the_payment_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-existing empty-wallet 400 still runs, and still runs before the gate."""

    def _never_gate(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("the payment gate must not run for a missing wallet")

    monkeypatch.setattr(kyc_routes, "require_paid_request", _never_gate)

    response = kyc_routes.kyc_verify(_request(query={}))

    assert response.status_code == 400


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "paid_lookup")
def test_a_valid_but_unenrolled_wallet_is_still_charged(
    ledger: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A miss stays a chargeable answer — the format check must not turn "not enrolled" into a refusal."""
    monkeypatch.setattr(payment_service, "require_payment", lambda *_a, **_kw: _settled_result())

    response = kyc_routes.kyc_verify(
        _request(headers={"PAYMENT-SIGNATURE": "miss-header"}, query={"wallet": _WALLET})
    )

    assert response.status_code == 200
    assert '"enrolled":false' in response.description.replace(" ", "")
    assert len(ledger.settlements) == 1


# --------------------------------------------------------------------------- #
# K-2: free-endpoint rate limiting
# --------------------------------------------------------------------------- #
@pytest.fixture
def free_enroll(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Wire kyc_enroll to an in-memory store with the indexer stubbed, recording every wallet whose signals were fetched."""
    fetched: list[str] = []

    def _fetch(wallet: str) -> WalletSignals:
        fetched.append(wallet)
        return WalletSignals(wallet_age_round=1000, recent_tx_count=5)

    monkeypatch.setattr(
        kyc_routes,
        "enrollment_service",
        EnrollmentService(
            store=InMemoryEnrollmentStore(),
            signature_verifier=lambda *_a: True,
            signals_fetcher=_fetch,
            current_round_fetcher=lambda: 2_000_000,
        ),
    )
    return fetched


def _enroll_body(wallet: str = _WALLET) -> bytes:
    from app.core import serialization

    return serialization.dumps(
        {"wallet_address": wallet, "consent_signature_b64": "c2lnbmF0dXJlLWJ5dGVz"}
    ).encode("utf-8")


@pytest.mark.usefixtures("fake_redis", "free_enroll")
def test_the_consent_message_endpoint_is_rate_limited_per_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past the hourly budget one IP gets a 429, and a different IP is unaffected."""
    monkeypatch.setattr(settings, "kyc_consent_rate_limit_per_hour", 2)
    request = _request(
        headers={"X-Real-IP": "203.0.113.7"},
        query={"wallet_address": _WALLET},
        path="/api/v1/kyc/consent-message",
    )

    assert "message" in kyc_routes.kyc_consent_message(request)
    assert "message" in kyc_routes.kyc_consent_message(request)
    limited = kyc_routes.kyc_consent_message(request)

    assert limited.status_code == 429
    assert "rate_limited" in limited.description
    # A different caller has their own bucket.
    other = kyc_routes.kyc_consent_message(
        _request(
            headers={"X-Real-IP": "198.51.100.9"},
            query={"wallet_address": _WALLET},
            path="/api/v1/kyc/consent-message",
        )
    )
    assert "message" in other


@pytest.mark.usefixtures("fake_redis")
def test_enroll_is_rate_limited_per_ip_before_the_indexer_is_called(
    free_enroll: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Past the hourly per-IP budget enroll is a 429, and the outbound indexer fetch never happens."""
    monkeypatch.setattr(settings, "kyc_enroll_rate_limit_per_hour", 1)
    monkeypatch.setattr(settings, "kyc_enroll_wallet_rate_limit_per_day", 1000)

    def _post(wallet: str) -> Response | dict:
        return kyc_routes.kyc_enroll(
            _request(
                method="POST",
                headers={"X-Real-IP": "203.0.113.7"},
                body=_enroll_body(wallet),
                path="/api/v1/kyc/enroll",
            )
        )

    assert _post(_WALLET)["wallet_address"] == _WALLET
    limited = _post(_PAYER)

    assert limited.status_code == 429
    # The second, refused request must not have reached the indexer.
    assert free_enroll == [_WALLET]


@pytest.mark.usefixtures("fake_redis")
def test_enroll_is_rate_limited_per_wallet_independently_of_the_ip(
    free_enroll: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """One wallet cannot be re-enrolled past its daily budget even from a fresh IP each time, and the refused attempts never reach the indexer."""
    monkeypatch.setattr(settings, "kyc_enroll_rate_limit_per_hour", 1000)
    monkeypatch.setattr(settings, "kyc_enroll_wallet_rate_limit_per_day", 2)

    def _post(ip: str) -> Response | dict:
        return kyc_routes.kyc_enroll(
            _request(
                method="POST",
                headers={"X-Real-IP": ip},
                body=_enroll_body(_WALLET),
                path="/api/v1/kyc/enroll",
            )
        )

    assert _post("203.0.113.1")["wallet_address"] == _WALLET
    assert _post("203.0.113.2")["wallet_address"] == _WALLET
    limited = _post("203.0.113.3")

    assert limited.status_code == 429
    assert "rate_limited" in limited.description
    # Two fetches, not three: the per-wallet check runs before the enrollment
    # service, so the refused attempt cost no outbound request.
    assert free_enroll == [_WALLET, _WALLET]

    # A different wallet has its own bucket, even from an IP already used.
    other = kyc_routes.kyc_enroll(
        _request(
            method="POST",
            headers={"X-Real-IP": "203.0.113.1"},
            body=_enroll_body(_PAYER),
            path="/api/v1/kyc/enroll",
        )
    )
    assert other["wallet_address"] == _PAYER


@pytest.mark.usefixtures("fake_redis", "free_enroll")
def test_a_malformed_enroll_body_is_still_a_400_not_a_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Body decoding still fails closed with a 400, and an undecodable body never reaches the wallet limiter."""
    monkeypatch.setattr(settings, "kyc_enroll_rate_limit_per_hour", 1000)

    response = kyc_routes.kyc_enroll(
        _request(
            method="POST",
            headers={"X-Real-IP": "203.0.113.7"},
            body=b"{not json",
            path="/api/v1/kyc/enroll",
        )
    )

    assert response.status_code == 400
    assert "invalid_request" in response.description


# --------------------------------------------------------------------------- #
# K-2: fail-open on a Redis outage
# --------------------------------------------------------------------------- #
def test_every_kyc_rate_limit_fails_open_when_redis_is_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Redis outage must not take free enrollment offline — every limiter reads as "not limited"."""
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: _BrokenRedis())
    monkeypatch.setattr(settings, "kyc_consent_rate_limit_per_hour", 0)
    monkeypatch.setattr(settings, "kyc_enroll_rate_limit_per_hour", 0)
    monkeypatch.setattr(settings, "kyc_enroll_wallet_rate_limit_per_day", 0)

    request = _request(headers={"X-Real-IP": "203.0.113.7"})

    # A zero budget would refuse every request if Redis were answering, so
    # these only pass because the counter failed and the limiter waved them on.
    assert kyc_rate_limit.consent_message_rate_limited(request) is False
    assert kyc_rate_limit.enroll_ip_rate_limited(request) is False
    assert kyc_rate_limit.enroll_wallet_rate_limited(_WALLET) is False


@pytest.mark.usefixtures("free_enroll")
def test_enrollment_still_succeeds_end_to_end_with_redis_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-open is wired all the way through the route, not just the limiter helpers."""
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: _BrokenRedis())
    monkeypatch.setattr(settings, "kyc_enroll_rate_limit_per_hour", 0)
    monkeypatch.setattr(settings, "kyc_enroll_wallet_rate_limit_per_day", 0)

    response = kyc_routes.kyc_enroll(
        _request(
            method="POST",
            headers={"X-Real-IP": "203.0.113.7"},
            body=_enroll_body(),
            path="/api/v1/kyc/enroll",
        )
    )

    assert response["wallet_address"] == _WALLET


def test_an_unattributable_caller_is_not_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no X-Real-IP and no X-Forwarded-For (local dev) there is no key to bucket on, so the caller is waved through rather than sharing one starved counter with everyone else."""
    client = _FakeRedis()
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(settings, "kyc_consent_rate_limit_per_hour", 0)
    monkeypatch.setattr(settings, "kyc_enroll_rate_limit_per_hour", 0)

    request = _request(headers={})

    assert kyc_rate_limit.consent_message_rate_limited(request) is False
    assert kyc_rate_limit.enroll_ip_rate_limited(request) is False
    assert client.store == {}


# --------------------------------------------------------------------------- #
# K-3b: the throwaway paid test route is gone
# --------------------------------------------------------------------------- #
def test_no_throwaway_test_ping_route_is_registered() -> None:
    """The /_test/ping route — a live route charging a hardcoded price through the bare gate — is deleted, not merely unlinked."""
    registered: list[str] = []

    class _Recorder:
        def get(self, path: str) -> Callable[[object], object]:
            registered.append(path)
            return lambda handler: handler

        def post(self, path: str) -> Callable[[object], object]:
            registered.append(path)
            return lambda handler: handler

    kyc_routes.register_kyc_routes(_Recorder())

    assert not any("_test" in path or "ping" in path for path in registered)
    assert not hasattr(kyc_routes, "kyc_test_ping")
    assert "/api/v1/kyc/verify" in registered


def test_no_kyc_price_is_a_hardcoded_literal() -> None:
    """Every KYC price comes from Settings — the ping's hardcoded "$0.01" was the only price literal in the marketplace."""
    import inspect

    source = inspect.getsource(kyc_routes)

    assert "$0.01" not in source
    assert "settings.kyc_lookup_price" in source


@pytest.mark.usefixtures("fake_redis")
def test_an_enrolled_wallet_is_returned_by_a_paid_lookup(
    paid_lookup: InMemoryEnrollmentStore,
    ledger: InMemorySettlementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path still works through the new wrapper: an enrolled wallet is found, and the payment is booked once."""
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(payment_service, "require_payment", lambda *_a, **_kw: _settled_result())
    paid_lookup.upsert(
        StoredEnrollment(
            wallet_address=_WALLET,
            enrolled_at_epoch=1,
            updated_at_epoch=1,
            consent_signature_b64="c2ln",
            wallet_age_round=1000,
            recent_tx_count=5,
            kyc_level="established",
        )
    )

    response = kyc_routes.kyc_verify(
        _request(headers={"PAYMENT-SIGNATURE": "hit-header"}, query={"wallet": _WALLET})
    )

    assert response.status_code == 200
    assert '"enrolled":true' in response.description.replace(" ", "")
    assert len(ledger.settlements) == 1
