"""x402 feature-request board tests: free file, paid vote, free browse, paid demand.

Fully offline. The facilitator is a stub that never touches the network (same
shape as test_x402_board.py's), Redis is a fake at the get_redis seam, and the
store is the module's own in-memory backend. Nothing here settles a real
payment or reaches TestNet.

Replay protection and the settlement ledger are shared infrastructure
(modules/x402/) already covered by test_x402_directory.py -- they are not
re-tested here. What IS feature-board-specific and tested here: the free/paid
split (the free browse must never carry a vote total; filing is free and
anonymous while voting on the result is still paid), the vote counter's
behaviour under concurrency, voting on a missing request costing nothing, and
the demand ranking.
"""

from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Never

import pytest

pytest.importorskip("x402")

from x402.extensions.bazaar import validate_discovery_extension
from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2
from x402.schemas.payments import PaymentRequirements
from x402.schemas.responses import SupportedKind, SupportedResponse
from x402.schemas.v1 import PaymentRequirementsV1
from x402.server import x402ResourceServerSync

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.x402 import circuit_breaker
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import paid_request as paid_request_module
from app.modules.x402 import replay as replay_module
from app.modules.x402_features.api import routes as feature_routes
from app.modules.x402_features.services.feature_service import FeatureService, request_id_for
from app.modules.x402_features.stores import cassandra as feature_cassandra_store
from app.modules.x402_features.stores.memory import InMemoryFeatureStore

_PAY_TO = "A" * 58
_PAYER = "P" * 58
_OTHER_PAYER = "Q" * 58

# Captured before any test/fixture monkeypatches circuit_breaker.is_tripped,
# so the dedicated breaker test below can restore real breaker behaviour
# (this file's autouse _breaker_closed_by_default fixture stubs it for
# every other test).
_real_is_tripped = circuit_breaker.is_tripped


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Enough of the Redis API for the replay claim and the rate-limit counter."""

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

    def get(self, key: str) -> str | None:
        return self.store.get(key)


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


class _StubFacilitator:
    """Canned /supported. verify()/settle() raise unless a test opts into settling."""

    def get_supported(self) -> SupportedResponse:
        return SupportedResponse(
            kinds=[SupportedKind(x402_version=2, scheme="exact", network=ALGORAND_TESTNET_CAIP2)]
        )

    def verify(
        self, _payload: dict, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("verify() must not be called without a payment header")

    def settle(
        self, _payload: dict, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("settle() must not be called without a payment header")


def _stub_resource_server() -> x402ResourceServerSync:
    server = x402ResourceServerSync(_StubFacilitator())
    x402_client.register_tagged_exact_avm_scheme(server, ALGORAND_TESTNET_CAIP2)
    server.initialize()
    return server


def _request(
    *,
    method: str = "POST",
    body: bytes = b"",
    headers: dict[str, str] | None = None,
    query: dict[str, Any] | None = None,
    path_params: dict[str, str] | None = None,
    path: str = "/api/v1/x402/features",
) -> Request:
    return Request(
        method=method,
        headers=headers or {},
        query_params=QueryParams(query or {}),
        path_params=path_params or {},
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


@pytest.fixture
def store() -> InMemoryFeatureStore:
    """A fresh in-memory feature store per test."""
    return InMemoryFeatureStore()


@pytest.fixture
def service(store: InMemoryFeatureStore) -> FeatureService:
    """A service bound to the per-test store."""
    return FeatureService(store)


@pytest.fixture
def wired(store: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch) -> InMemoryFeatureStore:
    """Point the route module's service singleton at the per-test store."""
    monkeypatch.setattr(feature_routes, "feature_service", FeatureService(store))
    return store


@pytest.fixture
def testnet_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the gate at TestNet and the offline stub facilitator."""
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", _PAY_TO)
    monkeypatch.setattr(x402_guard, "get_resource_server", _stub_resource_server)


@pytest.fixture(autouse=True)
def _breaker_closed_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test in this file gets a not-tripped circuit breaker unless it says otherwise.

    Most tests here predate the refund circuit breaker and monkeypatch
    require_paid_request directly rather than pulling in fake_redis -- the
    breaker's own is_tripped check now runs BEFORE require_paid_request on
    every paid route, so without this it fails CLOSED (unreachable Redis) and
    every one of those tests gets a 503 instead of what they actually test.
    Tests that specifically exercise the breaker (tripped, or a real Redis
    seam) override this via their own fake_redis usage or an explicit
    monkeypatch of circuit_breaker.is_tripped.
    """
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)


@pytest.fixture(autouse=True)
def _already_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the pre-parse 402 for header-less requests to x402_features_demand: every test in this file that reaches that route models a request that already carries a payment (the gate is stubbed via require_paid_request or testnet_settings), so the unpaid challenge is out of scope here. Its ordering has its own test in tests/test_x402_unpaid_challenge.py."""
    monkeypatch.setattr(feature_routes, "challenge_if_unpaid", lambda *_a, **_kw: None)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Swap all Redis seams for one in-process fake shared by replay, rate limiting and the refund circuit breaker."""
    client = _FakeRedis()
    monkeypatch.setattr(replay_module, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(circuit_breaker, "get_redis", lambda **_kw: client)
    return client


def _file_request(
    service: FeatureService,
    *,
    title: str = "Candles endpoint",
    description: str = "OHLCV for any ASA.",
    submitter: str = _PAYER,
    txid: str = "TX-A",
    now: datetime | None = None,
) -> str:
    """File one request through the service and return its id."""
    return service.create(
        title=title,
        description=description,
        submitter=submitter,
        settlement_tx_id=txid,
        now=now,
    ).request_id


# --------------------------------------------------------------------------- #
# POST /features — free, anonymous, rate-limited filing
# --------------------------------------------------------------------------- #
def _never_paid(*_a: object, **_kw: object) -> Never:
    raise AssertionError("the free submit route must never reach the payment gate")


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_filing_is_free_and_needs_no_payment_header(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare POST with no payment header files the request: 201, no 402, no payment headers, and the gate is never consulted."""
    monkeypatch.setattr(feature_routes, "require_paid_request", _never_paid)

    response = feature_routes.x402_features_submit(
        _request(
            body=json.dumps(
                {"title": "  Candles endpoint  ", "description": "  OHLCV for any ASA.  "}
            ).encode()
        )
    )

    assert response.status_code == 201
    assert "PAYMENT-REQUIRED" not in response.headers
    assert "PAYMENT-RESPONSE" not in response.headers
    body = json.loads(response.description)
    assert set(body) == {"request"}
    item = body["request"]
    assert item["title"] == "Candles endpoint"
    assert item["description"] == "OHLCV for any ASA."
    assert set(item) == {
        "request_id",
        "title",
        "description",
        "created_at_epoch",
        "status",
        "claims_count",
        "latest_claimer",
    }
    assert item["status"] == "pending"
    stored = wired.get(item["request_id"])
    assert stored is not None


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_a_free_filing_is_anonymous_even_if_the_body_names_a_wallet(
    wired: InMemoryFeatureStore,
) -> None:
    """No self-declared submitter: a wallet in the body is ignored and the stored submitter and settlement txid are empty."""
    response = feature_routes.x402_features_submit(
        _request(body=json.dumps({"title": "X", "submitter": _OTHER_PAYER}).encode())
    )

    stored = wired.get(json.loads(response.description)["request"]["request_id"])
    assert stored is not None
    assert stored.submitter == ""
    assert stored.settlement_tx_id == ""
    assert _OTHER_PAYER not in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
@pytest.mark.parametrize(
    "bad_body",
    [
        b"{not json",
        b"{}",  # title is required
        b'{"title":""}',  # and must not be empty
        b'{"description":"no title here"}',
        json.dumps({"title": "z" * 121}).encode(),  # over the 120-char cap
        json.dumps({"title": "ok", "description": "d" * 2001}).encode(),  # over 2000
    ],
)
def test_a_malformed_submit_body_is_a_400(bad_body: bytes) -> None:
    """Title/description validation and bounds survive the move to free filing."""
    response = feature_routes.x402_features_submit(_request(body=bad_body))

    assert response.status_code == 400
    assert "invalid_request" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
def test_filing_is_rate_limited_per_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """An IP over the hourly filing budget gets a 429 and nothing is stored; a different IP is unaffected."""
    monkeypatch.setattr(settings, "x402_features_submit_rate_limit_per_hour", 2)

    def _file(ip: str) -> Response:
        return feature_routes.x402_features_submit(
            _request(body=b'{"title":"Candles"}', headers={"X-Real-IP": ip})
        )

    assert _file("203.0.113.7").status_code == 201
    assert _file("203.0.113.7").status_code == 201
    limited = _file("203.0.113.7")
    assert limited.status_code == 429
    assert "rate_limited" in limited.description
    assert _file("203.0.113.9").status_code == 201
    assert len(feature_routes.feature_service.list_recent(limit=50)) == 3


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
def test_the_filing_budget_is_separate_from_the_browse_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausting the filing budget must not lock the same IP out of browsing, and vice versa."""
    monkeypatch.setattr(settings, "x402_features_submit_rate_limit_per_hour", 1)
    monkeypatch.setattr(settings, "x402_features_rate_limit_per_hour", 1)
    headers = {"X-Real-IP": "203.0.113.7"}

    assert (
        feature_routes.x402_features_submit(
            _request(body=b'{"title":"Candles"}', headers=headers)
        ).status_code
        == 201
    )
    assert (
        feature_routes.x402_features_submit(
            _request(body=b'{"title":"Candles"}', headers=headers)
        ).status_code
        == 429
    )
    assert "items" in feature_routes.x402_features_browse(_request(method="GET", headers=headers))


@pytest.mark.usefixtures("testnet_settings", "wired")
def test_filing_fails_open_when_redis_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage must not take free filing offline."""
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: _BrokenRedis())

    response = feature_routes.x402_features_submit(
        _request(body=b'{"title":"Candles"}', headers={"X-Real-IP": "203.0.113.7"})
    )

    assert response.status_code == 201


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_a_free_filing_is_browsable_and_its_vote_still_costs_money(
    wired: InMemoryFeatureStore,
) -> None:
    """The free/paid split end to end: a freely filed request shows up on the free browse, ranks with vote_total 0, and voting on it is still a 402 at the vote price."""
    from x402.http.utils import decode_payment_required_header

    filed = feature_routes.x402_features_submit(_request(body=b'{"title":"Candles"}'))
    request_id = json.loads(filed.description)["request"]["request_id"]

    browse = feature_routes.x402_features_browse(_request(method="GET"))
    assert [item["request_id"] for item in browse["items"]] == [request_id]

    ranked = FeatureService(wired).rank_by_demand(limit=10)
    assert [(r.request.request_id, r.vote_total) for r in ranked] == [(request_id, 0)]

    vote = feature_routes.x402_features_vote(_request(path_params={"request_id": request_id}))
    assert vote.status_code == 402
    offer = decode_payment_required_header(vote.headers["PAYMENT-REQUIRED"]).accepts[0]
    assert offer.amount == "20000"
    assert wired.get_vote_total(request_id) == 0


def test_filing_the_same_title_twice_gets_two_requests(service: FeatureService) -> None:
    """A feature request is an event, not a renewable slot — restating a wish states it twice and must not collapse onto one row."""
    first = _file_request(service, title="Candles endpoint", txid="")
    second = _file_request(service, title="Candles endpoint", txid="")

    assert first != second
    assert len(service.list_recent(limit=50)) == 2


def test_a_settlement_txid_when_present_still_derives_the_request_id(
    service: FeatureService, store: InMemoryFeatureStore
) -> None:
    """A caller that does have a settled payment to attach gets the ledger-traceable id."""
    request_id = _file_request(service, txid="TX-A", submitter=_PAYER)

    assert request_id == request_id_for(settlement_tx_id="TX-A")
    assert store.get(request_id).submitter == _PAYER


# --------------------------------------------------------------------------- #
# POST /features/:id/vote
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
def test_voting_on_a_missing_request_is_a_404_and_never_reaches_the_gate() -> None:
    """A vote for an unknown request id costs nothing: existence is checked before the payment gate, so this is a 404 and not a 402."""
    response = feature_routes.x402_features_vote(
        _request(path_params={"request_id": "does-not-exist"})
    )

    assert response.status_code == 404
    assert "not_found" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_voting_on_a_real_request_without_payment_returns_402(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing request with no payment header yields a 402 priced at the vote fee, not the request fee."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_features_vote_price", "$0.02")
    request_id = _file_request(FeatureService(wired))

    response = feature_routes.x402_features_vote(_request(path_params={"request_id": request_id}))

    assert response.status_code == 402
    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    offer = payment_required.accepts[0]
    # 0.02 USDC at 6 decimals — the vote price, not the $0.05 request price.
    assert offer.amount == "20000"
    assert offer.extra["tag"] == x402_client.CHALLENGE_TAG
    # One Bazaar entry for the route template, not one per request id; and a
    # POST must declare a body extension or the facilitator's validator
    # rejects it and never catalogs the route (found live 2026-09-05).
    assert payment_required.resource.url.endswith("/api/v1/x402/features/{request_id}/vote")
    assert validate_discovery_extension(payment_required.extensions["bazaar"]).valid


def test_a_settled_vote_increments_the_total_and_records_the_voter(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settled vote adds one to the demand total, echoes it back, and appends an audit row naming the voter."""
    request_id = _file_request(FeatureService(wired))
    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXV1")
    )

    response = feature_routes.x402_features_vote(_request(path_params={"request_id": request_id}))

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["vote_total"] == 1
    assert body["settlement_tx_id"] == "TXV1"
    assert wired.get_vote_total(request_id) == 1
    audit = wired.votes_for(request_id)
    assert len(audit) == 1
    assert audit[0].voter == _PAYER
    assert audit[0].settlement_tx_id == "TXV1"


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_a_vote_write_failure_after_settlement_gets_refunded_not_500(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A product-write exception after payment settles triggers a refund response, never a bare 500."""
    request_id = _file_request(FeatureService(wired))
    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXV-FAIL")
    )

    def _boom(*_a: object, **_kw: object) -> Never:
        raise RuntimeError("simulated feature-store failure")

    monkeypatch.setattr(feature_routes.feature_service, "vote", _boom)
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: SimpleNamespace(status="sent", txid="REFUND1", error=None),
    )

    response = feature_routes.x402_features_vote(_request(path_params={"request_id": request_id}))

    assert response.status_code == 503
    body = json.loads(response.description)
    assert body["error"]["code"] in ("product_failed_refunded", "product_failed_refund_pending")


def test_the_vote_circuit_breaker_blocks_before_the_payment_gate_once_tripped(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once tripped, the resource is refused before require_paid_request ever runs -- no further money at risk."""
    request_id = _file_request(FeatureService(wired))

    def _must_not_charge(*_a: object, **_kw: object) -> Never:
        raise AssertionError("the payment gate must not run while the breaker is tripped")

    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: True)
    monkeypatch.setattr(feature_routes, "require_paid_request", _must_not_charge)

    response = feature_routes.x402_features_vote(_request(path_params={"request_id": request_id}))

    assert response.status_code == 503
    assert "temporarily_disabled" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_the_real_breaker_trips_after_enough_recorded_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end against the real (fake-Redis-backed) breaker, not a stub -- record_refund_failure enough times and is_tripped flips."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", _real_is_tripped)
    monkeypatch.setattr(settings, "x402_refund_breaker_max_failures", 2)

    resource = feature_routes._VOTE_RESOURCE
    assert circuit_breaker.is_tripped(resource) is False
    circuit_breaker.record_refund_failure(resource)
    assert circuit_breaker.is_tripped(resource) is False
    circuit_breaker.record_refund_failure(resource)
    assert circuit_breaker.is_tripped(resource) is True


def test_the_same_wallet_may_vote_repeatedly_by_paying_repeatedly(
    service: FeatureService, store: InMemoryFeatureStore
) -> None:
    """Paying again votes again. This is a costly-signal board, not one-vote-per-wallet: each settled payment adds a unit of demand."""
    request_id = _file_request(service)

    for index in range(3):
        service.vote(request_id=request_id, voter=_PAYER, settlement_tx_id=f"TXV{index}")

    assert store.get_vote_total(request_id) == 3
    # Every vote is individually recorded for abuse forensics — the audit log
    # is what makes "one wallet manufactured this demand" detectable.
    assert len(store.votes_for(request_id)) == 3


def test_concurrent_votes_do_not_lose_an_increment(
    service: FeatureService, store: InMemoryFeatureStore
) -> None:
    """Votes landing at the same moment are separate payments and must all count.

    The memory store guards its total with a lock and the Cassandra store uses
    a counter column, precisely so this cannot silently merge two paid votes
    into one. Real threads, hammering one request id through the same service
    the route calls.

    The switch interval is driven to its floor for the duration, and restored
    after. Without that this test does not discriminate: CPython's default 5ms
    interval means an unguarded read-modify-write almost never gets preempted
    between the read and the write over a short loop, so a broken store would
    pass and the test would be theatre. Measured on this interpreter: with the
    lock removed and the interval at its floor, ~400 of 3200 paid votes are
    lost. The Cassandra path has no equivalent hazard -- its counter column is
    atomic at the replica -- so this covers the backend that could actually
    regress.
    """
    request_id = _file_request(service)
    voters = 16
    votes_each = 200
    start = threading.Barrier(voters)

    def _vote(worker: int) -> None:
        start.wait()
        for index in range(votes_each):
            service.vote(
                request_id=request_id,
                voter=_PAYER,
                settlement_tx_id=f"TXV-{worker}-{index}",
            )

    original_interval = sys.getswitchinterval()
    sys.setswitchinterval(1e-9)
    try:
        threads = [threading.Thread(target=_vote, args=(worker,)) for worker in range(voters)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        sys.setswitchinterval(original_interval)

    assert store.get_vote_total(request_id) == voters * votes_each
    assert len(store.votes_for(request_id)) == voters * votes_each


def test_an_audit_append_failure_does_not_lose_the_paid_vote(
    service: FeatureService, store: InMemoryFeatureStore
) -> None:
    """The increment is what the payer paid for: an audit-log failure is logged, not turned into a lost vote or a 5xx."""

    def _boom(_vote: object) -> Never:
        raise ConnectionError("cassandra down")

    store.append_vote = _boom  # type: ignore[method-assign]
    request_id = _file_request(service)

    total = service.vote(request_id=request_id, voter=_PAYER, settlement_tx_id="TXV1")

    assert total == 1
    assert store.get_vote_total(request_id) == 1


# --------------------------------------------------------------------------- #
# GET /features — the free browse surface
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_browse_returns_requests_newest_first(wired: InMemoryFeatureStore) -> None:
    """The free browse returns JSON requests ordered newest first."""
    service = FeatureService(wired)
    base = datetime.now(tz=UTC) - timedelta(hours=3)
    for index in range(3):
        _file_request(
            service,
            title=f"Request {index}",
            txid=f"TX{index}",
            now=base + timedelta(hours=index),
        )

    result = feature_routes.x402_features_browse(_request(method="GET"))

    assert [item["title"] for item in result["items"]] == ["Request 2", "Request 1", "Request 0"]


@pytest.mark.usefixtures("fake_redis")
def test_the_free_browse_never_exposes_the_demand_signal(
    wired: InMemoryFeatureStore,
) -> None:
    """Free is existence, paid is demand. The browse surface must carry no vote total and no submitter, however many votes a request has."""
    service = FeatureService(wired)
    request_id = _file_request(service)
    for index in range(5):
        service.vote(request_id=request_id, voter=_PAYER, settlement_tx_id=f"TXV{index}")

    result = feature_routes.x402_features_browse(_request(method="GET"))

    assert len(result["items"]) == 1
    item = result["items"][0]
    assert set(item) == {
        "request_id",
        "title",
        "description",
        "created_at_epoch",
        "status",
        "claims_count",
        "latest_claimer",
    }
    # Belt and braces: the number must not appear anywhere in the payload under
    # any other key name either.
    assert "vote" not in json.dumps(result)
    assert _PAYER not in json.dumps(result)


@pytest.mark.usefixtures("fake_redis")
def test_browse_limit_is_clamped_to_the_configured_maximum(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller cannot ask for an unbounded listing — the limit is clamped."""
    monkeypatch.setattr(settings, "x402_features_max_results", 2)
    service = FeatureService(wired)
    for index in range(5):
        _file_request(service, title=f"Request {index}", txid=f"TX{index}")

    result = feature_routes.x402_features_browse(_request(method="GET", query={"limit": "9999"}))

    assert len(result["items"]) == 2


@pytest.mark.usefixtures("fake_redis", "wired")
def test_a_non_integer_browse_limit_is_a_400() -> None:
    """A non-integer limit is rejected rather than silently ignored."""
    result = feature_routes.x402_features_browse(_request(method="GET", query={"limit": "lots"}))

    assert result.status_code == 400
    assert "invalid_request" in result.description


@pytest.mark.usefixtures("fake_redis", "wired")
def test_browse_is_rate_limited_per_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    """An IP over the hourly budget gets a 429; a different IP is unaffected."""
    monkeypatch.setattr(settings, "x402_features_rate_limit_per_hour", 2)

    def _read(ip: str) -> Response | dict:
        return feature_routes.x402_features_browse(
            _request(method="GET", headers={"X-Real-IP": ip})
        )

    assert "items" in _read("203.0.113.7")
    assert "items" in _read("203.0.113.7")
    limited = _read("203.0.113.7")
    assert limited.status_code == 429
    assert "items" in _read("203.0.113.9")


@pytest.mark.usefixtures("fake_redis", "wired")
def test_browse_rate_limit_is_separate_from_the_board_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausting the feature board's budget must not also lock the caller out of the visibility board."""
    from app.modules.x402_board.api import routes as board_routes
    from app.modules.x402_board.services.board_service import BoardService
    from app.modules.x402_board.stores.memory import InMemoryPlacementStore

    monkeypatch.setattr(settings, "x402_features_rate_limit_per_hour", 1)
    monkeypatch.setattr(settings, "x402_board_rate_limit_per_hour", 10)
    monkeypatch.setattr(board_routes, "board_service", BoardService(InMemoryPlacementStore()))

    headers = {"X-Real-IP": "203.0.113.7"}
    assert "items" in feature_routes.x402_features_browse(_request(method="GET", headers=headers))
    assert (
        feature_routes.x402_features_browse(_request(method="GET", headers=headers)).status_code
        == 429
    )
    # The board's own counter is untouched.
    assert "items" in board_routes.x402_board_read(
        _request(method="GET", headers=headers, path="/api/v1/x402/board")
    )


@pytest.mark.usefixtures("wired")
def test_browse_fails_open_when_redis_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage must not take the free browse offline."""
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: _BrokenRedis())

    result = feature_routes.x402_features_browse(
        _request(method="GET", headers={"X-Real-IP": "203.0.113.7"})
    )

    assert "items" in result


# --------------------------------------------------------------------------- #
# GET /features/demand — the paid demand surface
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
def test_demand_without_payment_returns_402_at_the_demand_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reading demand is paid: no payment header yields a 402 priced at the demand fee, with a query-params discovery extension since it is a GET."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_features_demand_price", "$0.25")

    response = feature_routes.x402_features_demand(_request(method="GET"))

    assert response.status_code == 402
    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    offer = payment_required.accepts[0]
    # 0.25 USDC at 6 decimals — the aggregated-signal read, not a write fee.
    assert offer.amount == "250000"
    assert offer.extra["tag"] == x402_client.CHALLENGE_TAG
    bazaar = (payment_required.extensions or {}).get("bazaar")
    assert bazaar is not None
    # A GET's input is query params, so this must NOT be a body declaration.
    assert "body" not in json.dumps(bazaar)


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
def test_a_bad_demand_limit_is_rejected_before_the_payment_gate() -> None:
    """A non-integer limit is a 400, not a 402 — nobody is charged for a request that cannot be served."""
    response = feature_routes.x402_features_demand(_request(method="GET", query={"limit": "lots"}))

    assert response.status_code == 400
    assert "invalid_request" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
def test_demand_preview_serves_a_redacted_response_never_ranking_the_real_requests(
    wired: InMemoryFeatureStore,
) -> None:
    """?preview=true gets one redacted exemplar row, never the real vote-ranked order or totals."""
    service = FeatureService(wired)
    base = datetime.now(tz=UTC) - timedelta(hours=5)
    ids = {}
    for index, title in enumerate(["low", "high"]):
        ids[title] = _file_request(service, title=title, txid=f"TX{index}", now=base)
    for title, votes in (("low", 1), ("high", 9)):
        for vote_index in range(votes):
            service.vote(
                request_id=ids[title], voter=_PAYER, settlement_tx_id=f"TXV-{title}-{vote_index}"
            )

    response = feature_routes.x402_features_demand(
        _request(method="GET", query={"preview": "true"})
    )

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["items"] == [
        {
            **feature_routes._REQUEST_EXAMPLE,
            "request_id": "<preview>",
            "submitter": None,
            "created_at_epoch": 0,
            "vote_total": -1,
            "status": "pending",
            "claims_count": 0,
            "latest_claimer": None,
        }
    ]
    assert body["settlement_tx_id"] == "<preview>"


def test_the_paid_demand_read_ranks_by_vote_total_and_shows_the_counts(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The paid surface is the ranking: highest demand first, with the actual numbers the free browse withholds."""
    service = FeatureService(wired)
    base = datetime.now(tz=UTC) - timedelta(hours=5)
    ids = {}
    for index, title in enumerate(["low", "high", "middle"]):
        ids[title] = _file_request(
            service, title=title, txid=f"TX{index}", now=base + timedelta(hours=index)
        )
    for title, votes in (("low", 1), ("high", 9), ("middle", 4)):
        for vote_index in range(votes):
            service.vote(
                request_id=ids[title], voter=_PAYER, settlement_tx_id=f"TXV-{title}-{vote_index}"
            )

    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXD1")
    )
    response = feature_routes.x402_features_demand(_request(method="GET"))

    assert response.status_code == 200
    assert response.headers["PAYMENT-RESPONSE"] == "ok"
    body = json.loads(response.description)
    assert [item["title"] for item in body["items"]] == ["high", "middle", "low"]
    assert [item["vote_total"] for item in body["items"]] == [9, 4, 1]
    # The paid surface carries what the free one withholds; the submitter of
    # a paid-attributed request is served, and a request's own settlement is
    # not (it is not the demand signal).
    assert body["items"][0]["submitter"] == _PAYER
    assert "settlement_tx_id" not in body["items"][0]
    assert body["settlement_tx_id"] == "TXD1"


@pytest.mark.usefixtures("wired")
def test_the_paid_demand_read_serves_an_anonymous_submitter_as_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A freely filed request has no submitter, and the paid read says so with null rather than an empty or invented value."""
    feature_routes.x402_features_submit(_request(body=b'{"title":"Candles"}'))
    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    body = json.loads(feature_routes.x402_features_demand(_request(method="GET")).description)

    assert body["items"][0]["submitter"] is None
    assert body["items"][0]["vote_total"] == 0


def test_an_unvoted_request_still_appears_in_the_demand_ranking_with_zero(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A request nobody has voted on reads as 0, not as missing — a builder paying for demand needs to see what has no demand too."""
    service = FeatureService(wired)
    _file_request(service, title="ignored", txid="TX1")
    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    body = json.loads(feature_routes.x402_features_demand(_request(method="GET")).description)

    assert [(item["title"], item["vote_total"]) for item in body["items"]] == [("ignored", 0)]


def test_the_demand_ranking_is_clamped_to_the_configured_maximum(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The paid read is a bounded listing too — a caller cannot ask for the whole board at once."""
    monkeypatch.setattr(settings, "x402_features_max_results", 2)
    service = FeatureService(wired)
    for index in range(6):
        _file_request(service, title=f"Request {index}", txid=f"TX{index}")
    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    body = json.loads(
        feature_routes.x402_features_demand(
            _request(method="GET", query={"limit": "9999"})
        ).description
    )

    assert len(body["items"]) == 2


def test_the_demand_scan_is_bounded(
    service: FeatureService, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ranking scan is LIMITed, so the in-memory sort can never become an unbounded read.

    With a scan limit of 2, a heavily-voted request older than the two most
    recent is outside the window and does not appear — the documented, accepted
    degradation of ranking without a denormalized rank table, asserted here so
    it stays a known limit rather than a surprise.
    """
    monkeypatch.setattr(settings, "x402_features_demand_scan_limit", 2)
    base = datetime.now(tz=UTC) - timedelta(hours=5)
    oldest = _file_request(service, title="old but wanted", txid="TX0", now=base)
    for index in (1, 2):
        _file_request(
            service, title=f"newer {index}", txid=f"TX{index}", now=base + timedelta(hours=index)
        )
    service.vote(request_id=oldest, voter=_PAYER, settlement_tx_id="TXV1")

    ranked = service.rank_by_demand(limit=50)

    assert len(ranked) == 2
    assert [r.request.title for r in ranked] == ["newer 2", "newer 1"]


# --------------------------------------------------------------------------- #
# POST /features/:id/claim — paid "I'm building this"
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
def test_claiming_a_missing_request_is_a_404_that_never_reaches_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existence is checked before the gate: a claim on an unknown id costs nothing."""

    def _must_not_charge(*_a: object, **_kw: object) -> Never:
        raise AssertionError("the payment gate must not run for an unknown request")

    monkeypatch.setattr(feature_routes, "require_paid_request", _must_not_charge)

    response = feature_routes.x402_features_claim(
        _request(path_params={"request_id": "does-not-exist"})
    )

    assert response.status_code == 404
    assert "not_found" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_claiming_without_payment_is_a_402_at_the_vote_price_with_discovery(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A claim is priced at the vote fee and the 402 declares Bazaar discovery and the non-exclusive rule."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_features_vote_price", "$0.02")
    request_id = _file_request(FeatureService(wired))

    response = feature_routes.x402_features_claim(_request(path_params={"request_id": request_id}))

    assert response.status_code == 402
    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    assert payment_required.accepts[0].amount == "20000"
    assert (payment_required.extensions or {}).get("bazaar") is not None
    assert "Not exclusive" in (payment_required.resource.description or "")
    # Same two Bazaar rules as the vote route: template URL, body-shaped
    # declaration for a POST (found live 2026-09-05: this route advertised
    # one concrete URL per request id and a query-shaped declaration).
    assert payment_required.resource.url.endswith("/api/v1/x402/features/{request_id}/claim")
    assert validate_discovery_extension(payment_required.extensions["bazaar"]).valid


def test_a_settled_claim_is_stored_surfaced_on_both_reads_and_marked_fulfilled(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim row is stored under the settled payer, the free browse and the paid demand read both show the count and latest claimer, and the settlement is marked fulfilled after the write."""
    service = FeatureService(wired)
    request_id = _file_request(service)
    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXC1")
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        feature_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)) or True,
    )

    response = feature_routes.x402_features_claim(_request(path_params={"request_id": request_id}))

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body == {
        "request_id": request_id,
        "claims_count": 1,
        "latest_claimer": _PAYER,
        "status": "claimed",
        "settlement_tx_id": "TXC1",
    }
    claims = wired.claims_for(request_id)
    assert len(claims) == 1
    assert claims[0].claimer == _PAYER
    assert claims[0].settlement_tx_id == "TXC1"
    assert fulfilled == [("TXC1", "x402-features-claim")]

    browse = feature_routes.x402_features_browse(_request(method="GET"))
    assert browse["items"][0]["claims_count"] == 1
    assert browse["items"][0]["latest_claimer"] == _PAYER
    assert browse["items"][0]["status"] == "claimed"
    assert "vote" not in json.dumps(browse)

    demand = json.loads(feature_routes.x402_features_demand(_request(method="GET")).description)
    assert demand["items"][0]["claims_count"] == 1
    assert demand["items"][0]["latest_claimer"] == _PAYER
    assert demand["items"][0]["vote_total"] == 0
    assert demand["items"][0]["status"] == "claimed"


def test_multiple_claims_are_allowed_and_the_latest_claimer_wins_the_label(
    service: FeatureService, store: InMemoryFeatureStore
) -> None:
    """Claims are not exclusive: two builders and a repeat claim all count, and the newest claim names the latest claimer."""
    request_id = _file_request(service)
    base = datetime.now(tz=UTC) - timedelta(hours=3)
    service.claim(request_id=request_id, claimer=_PAYER, settlement_tx_id="C1", now=base)
    service.claim(
        request_id=request_id,
        claimer=_OTHER_PAYER,
        settlement_tx_id="C2",
        now=base + timedelta(hours=1),
    )
    summary = service.claim(
        request_id=request_id,
        claimer=_PAYER,
        settlement_tx_id="C3",
        now=base + timedelta(hours=2),
    )

    assert summary.count == 3
    assert summary.latest_claimer == _PAYER
    assert store.get_claim_summaries([request_id])[request_id].latest_claimer == _PAYER


def test_an_unclaimed_request_reads_as_zero_claims_and_a_null_claimer(
    wired: InMemoryFeatureStore,
) -> None:
    """No claims is a real answer: zero and null, never a placeholder wallet."""
    _file_request(FeatureService(wired))

    item = feature_routes.x402_features_browse(_request(method="GET"))["items"][0]

    assert item["claims_count"] == 0
    assert item["latest_claimer"] is None


def test_unreadable_claim_summaries_do_not_take_the_free_browse_down(
    wired: InMemoryFeatureStore,
) -> None:
    """A claims-table blip degrades the annotation, not the surface."""

    def _boom(_ids: list[str]) -> Never:
        raise ConnectionError("cassandra down")

    _file_request(FeatureService(wired))
    wired.get_claim_summaries = _boom  # type: ignore[method-assign]

    result = feature_routes.x402_features_browse(_request(method="GET"))

    assert len(result["items"]) == 1
    assert result["items"][0]["claims_count"] == 0


# --------------------------------------------------------------------------- #
# Status lifecycle (migration 119): pending -> claimed -> completed, reopened
# by a later claim. Distinct from the claim mechanism itself, which keeps its
# existing multiple-claims-allowed shape untouched (see the tests above).
# --------------------------------------------------------------------------- #
def test_a_freshly_filed_request_is_pending_on_both_reads(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No claim yet means pending -- on the free browse and the paid demand read."""
    _file_request(FeatureService(wired))
    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    browse = feature_routes.x402_features_browse(_request(method="GET"))
    demand = json.loads(feature_routes.x402_features_demand(_request(method="GET")).description)

    assert browse["items"][0]["status"] == "pending"
    assert demand["items"][0]["status"] == "pending"


def test_the_first_claim_moves_status_to_claimed(
    service: FeatureService, store: InMemoryFeatureStore
) -> None:
    """A single claim is enough to flip pending -> claimed."""
    request_id = _file_request(service)
    assert store.get_statuses([request_id]) == {}  # nothing set yet == pending by convention

    service.claim(request_id=request_id, claimer=_PAYER, settlement_tx_id="C1")

    assert store.get_statuses([request_id]) == {request_id: "claimed"}


def test_a_repeat_claim_keeps_the_status_claimed(
    service: FeatureService, store: InMemoryFeatureStore
) -> None:
    """Claiming again (allowed, non-exclusive) is a no-op on status, not a regression."""
    request_id = _file_request(service)
    service.claim(request_id=request_id, claimer=_PAYER, settlement_tx_id="C1")
    service.claim(request_id=request_id, claimer=_OTHER_PAYER, settlement_tx_id="C2")

    assert store.get_statuses([request_id]) == {request_id: "claimed"}


def test_mark_completed_requires_a_past_claimer(
    service: FeatureService, store: InMemoryFeatureStore
) -> None:
    """A wallet that never claimed cannot mark the request completed, and nothing changes."""
    request_id = _file_request(service)
    service.claim(request_id=request_id, claimer=_PAYER, settlement_tx_id="C1")

    with pytest.raises(feature_routes.FeatureError) as excinfo:
        service.mark_completed(request_id=request_id, claimer=_OTHER_PAYER)

    assert excinfo.value.code == "request_not_claimed_by_payer"
    assert store.get_statuses([request_id]) == {request_id: "claimed"}


def test_mark_completed_by_any_past_claimer_succeeds_not_only_the_latest(
    service: FeatureService, store: InMemoryFeatureStore
) -> None:
    """Any past claimer may mark completion -- not exclusively the most recent one."""
    request_id = _file_request(service)
    service.claim(request_id=request_id, claimer=_PAYER, settlement_tx_id="C1")
    service.claim(request_id=request_id, claimer=_OTHER_PAYER, settlement_tx_id="C2")

    # _PAYER is no longer the latest claimer, but has claimed at some point.
    service.mark_completed(request_id=request_id, claimer=_PAYER)

    assert store.get_statuses([request_id]) == {request_id: "completed"}


def test_a_new_claim_after_completed_reopens_to_claimed(
    service: FeatureService, store: InMemoryFeatureStore
) -> None:
    """Completed is not terminal: a fresh claim (from anyone) reopens the request."""
    request_id = _file_request(service)
    service.claim(request_id=request_id, claimer=_PAYER, settlement_tx_id="C1")
    service.mark_completed(request_id=request_id, claimer=_PAYER)
    assert store.get_statuses([request_id]) == {request_id: "completed"}

    service.claim(request_id=request_id, claimer=_OTHER_PAYER, settlement_tx_id="C2")

    assert store.get_statuses([request_id]) == {request_id: "claimed"}


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
def test_completing_a_missing_request_is_a_404_that_never_reaches_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Existence is checked before the gate: completing an unknown id costs nothing."""

    def _must_not_charge(*_a: object, **_kw: object) -> Never:
        raise AssertionError("the payment gate must not run for an unknown request")

    monkeypatch.setattr(feature_routes, "require_paid_request", _must_not_charge)

    response = feature_routes.x402_features_complete(
        _request(path_params={"request_id": "does-not-exist"})
    )

    assert response.status_code == 404
    assert "not_found" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_completing_without_payment_is_a_402_at_the_complete_price(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completion mark is priced at its own setting, and its 402 declares Bazaar discovery."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_features_complete_price", "$0.02")
    request_id = _file_request(FeatureService(wired))

    response = feature_routes.x402_features_complete(
        _request(path_params={"request_id": request_id})
    )

    assert response.status_code == 402
    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    assert payment_required.accepts[0].amount == "20000"
    assert payment_required.resource.url.endswith("/api/v1/x402/features/{request_id}/complete")
    assert validate_discovery_extension(payment_required.extensions["bazaar"]).valid


def test_a_settled_completion_by_a_past_claimer_updates_status_on_both_reads(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full paid route: claim, then complete, then check both read surfaces and mark_fulfilled."""
    service = FeatureService(wired)
    request_id = _file_request(service)
    service.claim(request_id=request_id, claimer=_PAYER, settlement_tx_id="C1")
    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXD1")
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        feature_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)) or True,
    )

    response = feature_routes.x402_features_complete(
        _request(path_params={"request_id": request_id})
    )

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body == {"request_id": request_id, "status": "completed", "settlement_tx_id": "TXD1"}
    assert fulfilled == [("TXD1", "x402-features-complete")]

    browse = feature_routes.x402_features_browse(_request(method="GET"))
    assert browse["items"][0]["status"] == "completed"
    demand = json.loads(feature_routes.x402_features_demand(_request(method="GET")).description)
    assert demand["items"][0]["status"] == "completed"


def test_completing_by_a_wallet_that_never_claimed_is_rejected_and_kept_not_refunded(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The settled payer never claimed: a 403, the status is untouched, and no refund is attempted.

    A FeatureError from the product write is run_with_refund's PlatformError
    path -- payment kept, no refund -- the same ownership-conflict shape the
    directory's relist-not-yours check uses. send_refund must never be
    called: this is a caller-fault rejection, not a delivery failure of ours.
    """
    request_id = _file_request(FeatureService(wired))
    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXD2")
    )

    def _must_not_refund(**_kw: object) -> Never:
        raise AssertionError("a caller-fault rejection must never trigger a refund")

    monkeypatch.setattr(paid_request_module, "send_refund", _must_not_refund)

    response = feature_routes.x402_features_complete(
        _request(path_params={"request_id": request_id})
    )

    assert response.status_code == 403
    body = json.loads(response.description)
    assert body["error"]["code"] == "request_not_claimed_by_payer"
    assert wired.get_statuses([request_id]) == {}


# --------------------------------------------------------------------------- #
# Promo-bypass identity spoofing (2026-09-07 security review, finding 10 --
# same pattern already found and fixed for x402_directory/x402_board, and for
# x402_grading's grade-submit): vote/claim/complete all feed `payer` in as a
# public identity statement (who voted, who's building it, who's declaring it
# done), not merely payment attribution. modules/x402/promo.py's own
# docstring says a promo redemption's wallet is checked for SYNTACTIC
# validity only ("a successful redemption is not proof the caller controls
# that wallet"), so a promo bypass would have let anyone vote/claim/complete
# "as" any real wallet via `?promo_wallet=` -- worst on complete, where an
# attacker could cite a real past claimer's own public address to falsely
# mark their work done. Closed by never reading promo params on any of these
# three routes. This test locks in the opposite invariant: promo params in
# the query string are ignored, not honored.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("route_name", "path", "extra_kwargs"),
    [
        ("x402_features_vote", "/api/v1/x402/features/{request_id}/vote", {}),
        ("x402_features_claim", "/api/v1/x402/features/{request_id}/claim", {}),
        ("x402_features_complete", "/api/v1/x402/features/{request_id}/complete", {}),
    ],
)
def test_vote_claim_and_complete_never_forward_promo_params(
    wired: InMemoryFeatureStore,
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
    path: str,
    extra_kwargs: dict[str, object],
) -> None:
    """None of the three paid write routes may pass ?promo=/?promo_wallet= into require_paid_request, even when present in the query string."""
    request_id = _file_request(FeatureService(wired))
    if route_name == "x402_features_complete":
        # complete requires a real prior claim to succeed past the gate --
        # not the point of this test, but keeps the happy path reachable.
        FeatureService(wired).claim(request_id=request_id, claimer=_PAYER, settlement_tx_id="TXC")

    captured: dict = {}

    def _spy_require_paid_request(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return _settled_result()

    monkeypatch.setattr(feature_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(feature_routes, "mark_fulfilled", lambda *_a, **_kw: None)

    route = getattr(feature_routes, route_name)
    route(
        _request(
            path_params={"request_id": request_id},
            query={"promo": "LAUNCH1000-TEST", "promo_wallet": "P" * 58},
            path=path,
            **extra_kwargs,
        )
    )

    assert "promo_code" not in captured
    assert "promo_wallet" not in captured


def test_completing_a_probe_payers_own_earlier_claim_is_not_special_cased(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Completion is not a ranking/demand signal, so unlike vote there is nothing to exclude here."""
    service = FeatureService(wired)
    monkeypatch.setattr(settings, "x402_probe_payers", f" {_PROBE.lower()} ")
    request_id = _file_request(service)
    service.claim(request_id=request_id, claimer=_PROBE, settlement_tx_id="C1")

    service.mark_completed(request_id=request_id, claimer=_PROBE)

    assert wired.get_statuses([request_id]) == {request_id: "completed"}


# --------------------------------------------------------------------------- #
# Probe / self wallets: charged, audited, never counted
# --------------------------------------------------------------------------- #
_PROBE = "B" * 58


def test_a_probe_payers_vote_is_audited_but_not_counted(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Our own wallet's paid vote keeps its audit row and its receipt, and moves no demand total."""
    monkeypatch.setattr(settings, "x402_probe_payers", f" {_PROBE.lower()} ")
    request_id = _file_request(FeatureService(wired))
    monkeypatch.setattr(
        feature_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_PROBE, txid="TXPROBE"),
    )
    fulfilled: list[str] = []
    monkeypatch.setattr(
        feature_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append(txid) or resource,
    )

    response = feature_routes.x402_features_vote(_request(path_params={"request_id": request_id}))

    assert response.status_code == 200
    assert json.loads(response.description)["vote_total"] == 0
    assert fulfilled == ["TXPROBE"]
    assert wired.get_vote_total(request_id) == 0
    assert [v.voter for v in wired.votes_for(request_id)] == [_PROBE]
    # A real wallet's vote right after still counts as one.
    FeatureService(wired).vote(request_id=request_id, voter=_PAYER, settlement_tx_id="TXV")
    assert wired.get_vote_total(request_id) == 1


# --------------------------------------------------------------------------- #
# Admin delete
# --------------------------------------------------------------------------- #
def _delete_request(request_id: str, headers: dict[str, str] | None = None) -> Request:
    return _request(
        method="DELETE",
        headers=headers,
        query={"request_id": request_id},
        path="/api/v1/admin/x402/features",
    )


def test_admin_feature_delete_without_admin_session_is_rejected(
    wired: InMemoryFeatureStore,
) -> None:
    """The real require_admin_wallet runs first; a self-asserted header proves nothing."""
    request_id = _file_request(FeatureService(wired))

    response = feature_routes.x402_admin_delete_feature_request(
        _delete_request(request_id, headers={"X-Admin-Wallet": _PAYER})
    )

    assert getattr(response, "status_code", 200) != 200
    assert wired.get(request_id) is not None


@pytest.mark.usefixtures("fake_redis")
def test_admin_feature_delete_removes_the_request_and_its_claims_but_keeps_vote_records(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An authorized delete drops the request and claims; the counter and audit log stay for forensics."""
    monkeypatch.setattr(feature_routes, "require_admin_wallet", lambda _request: None)
    service = FeatureService(wired)
    request_id = _file_request(service)
    service.vote(request_id=request_id, voter=_PAYER, settlement_tx_id="TXV")
    service.claim(request_id=request_id, claimer=_OTHER_PAYER, settlement_tx_id="TXC")

    response = feature_routes.x402_admin_delete_feature_request(_delete_request(request_id))

    assert response == {"deleted": True, "request_id": request_id}
    assert wired.get(request_id) is None
    assert wired.claims_for(request_id) == []
    assert feature_routes.x402_features_browse(_request(method="GET"))["items"] == []
    assert wired.get_vote_total(request_id) == 1
    assert len(wired.votes_for(request_id)) == 1
    assert (
        feature_routes.x402_admin_delete_feature_request(_delete_request(request_id)).status_code
        == 404
    )
    assert feature_routes.x402_admin_delete_feature_request(_delete_request("")).status_code == 400


def test_cassandra_epoch_treats_a_naive_driver_datetime_as_utc() -> None:
    """_epoch must treat a timezone-naive datetime as UTC, not the interpreter's local zone.

    That's what the real Cassandra driver actually returns for a `timestamp` column
    (x402_feature_requests.created_at). Same bug class root-caused 2026-09-03 in
    x402_social/stores/cassandra.py: `value.timestamp()` on a naive datetime assumes the
    *local* system zone -- on a UTC+2 host, "13:18:17 wall-clock, no tzinfo" is silently
    read as 11:18:17 UTC, 2 hours off from the real UTC value that was actually stored.

    Constructs the naive datetime explicitly rather than relying on this test's own
    execution environment happening to run in a non-UTC zone (which would make the bug
    invisible in CI).
    """
    naive = datetime(2026, 9, 4, 13, 18, 17)  # noqa: DTZ001 -- naive on purpose, see docstring
    assert naive.tzinfo is None
    assert feature_cassandra_store._epoch(naive) == 1788527897  # the correct UTC epoch
    assert feature_cassandra_store._epoch(None) == 0
