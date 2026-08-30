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

from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2
from x402.schemas.payments import PaymentRequirements
from x402.schemas.responses import SupportedKind, SupportedResponse
from x402.schemas.v1 import PaymentRequirementsV1
from x402.server import x402ResourceServerSync

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import replay as replay_module
from app.modules.x402_features.api import routes as feature_routes
from app.modules.x402_features.services.feature_service import FeatureService, request_id_for
from app.modules.x402_features.stores.memory import InMemoryFeatureStore

_PAY_TO = "A" * 58
_PAYER = "P" * 58
_OTHER_PAYER = "Q" * 58


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


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Swap both Redis seams for one in-process fake shared by replay and rate limiting."""
    client = _FakeRedis()
    monkeypatch.setattr(replay_module, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
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
        "claims_count",
        "latest_claimer",
    }
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
    offer = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"]).accepts[0]
    # 0.02 USDC at 6 decimals — the vote price, not the $0.05 request price.
    assert offer.amount == "20000"
    assert offer.extra["tag"] == x402_client.CHALLENGE_TAG


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
    assert "vote" not in json.dumps(browse)

    demand = json.loads(feature_routes.x402_features_demand(_request(method="GET")).description)
    assert demand["items"][0]["claims_count"] == 1
    assert demand["items"][0]["latest_claimer"] == _PAYER
    assert demand["items"][0]["vote_total"] == 0


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
