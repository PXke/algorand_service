"""x402 feature-request board tests: paid file, paid vote, free browse, paid demand.

Fully offline. The facilitator is a stub that never touches the network (same
shape as test_x402_board.py's), Redis is a fake at the get_redis seam, and the
store is the module's own in-memory backend. Nothing here settles a real
payment or reaches TestNet.

Replay protection and the settlement ledger are shared infrastructure
(modules/x402/) already covered by test_x402_directory.py -- they are not
re-tested here. What IS feature-board-specific and tested here: the free/paid
split (the free browse must never carry a vote total), the vote counter's
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
# POST /features — the 402 offer and pre-payment validation
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
def test_submit_without_payment_returns_402_with_correct_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No payment header yields a 402 whose offer carries the configured payTo, TestNet CAIP-2 id, USDC TestNet asset id, the feature board's own request price and the challenge tag."""
    from x402.http.utils import decode_payment_required_header
    from x402.mechanisms.avm.constants import USDC_TESTNET_ASA_ID

    monkeypatch.setattr(settings, "x402_features_request_price", "$0.05")

    response = feature_routes.x402_features_submit(
        _request(body=b'{"title":"Candles endpoint","description":"OHLCV"}')
    )

    assert response.status_code == 402
    offer = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"]).accepts[0]
    assert offer.pay_to == _PAY_TO
    assert offer.network == ALGORAND_TESTNET_CAIP2
    assert offer.asset == str(USDC_TESTNET_ASA_ID)
    # 0.05 USDC in atomic units at 6 decimals.
    assert offer.amount == "50000"
    assert offer.extra["tag"] == x402_client.CHALLENGE_TAG


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
def test_submit_402_declares_a_json_body_discovery_extension() -> None:
    """The submit 402 declares the Bazaar discovery extension as a JSON-body one, since a POST takes its input as a body rather than query params."""
    from x402.http.utils import decode_payment_required_header

    response = feature_routes.x402_features_submit(
        _request(body=b'{"title":"Candles endpoint","description":"OHLCV"}')
    )

    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    bazaar = (payment_required.extensions or {}).get("bazaar")
    assert bazaar is not None
    assert "body" in json.dumps(bazaar)


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
@pytest.mark.parametrize(
    "bad_body",
    [
        b"{not json",
        b"{}",  # title is required
        b'{"title":""}',  # and must not be empty
        b'{"description":"no title here"}',
    ],
)
def test_a_malformed_submit_body_is_rejected_before_the_payment_gate(bad_body: bytes) -> None:
    """A malformed body is a 400, not a 402 — nobody is charged to submit an invalid request."""
    response = feature_routes.x402_features_submit(_request(body=bad_body))

    assert response.status_code == 400
    assert "invalid_request" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "wired")
def test_an_over_long_title_is_rejected_before_the_payment_gate() -> None:
    """A title beyond the 120-character cap is a 400, not a charged request."""
    response = feature_routes.x402_features_submit(
        _request(body=json.dumps({"title": "z" * 121}).encode())
    )

    assert response.status_code == 400
    assert "invalid_request" in response.description


# --------------------------------------------------------------------------- #
# POST /features — the paid path
# --------------------------------------------------------------------------- #
def test_a_settled_payment_stores_the_request_and_returns_its_txid(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once payment settles, the request is stored and returned with the settlement txid and headers."""
    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    response = feature_routes.x402_features_submit(
        _request(
            body=json.dumps(
                {"title": "  Candles endpoint  ", "description": "  OHLCV for any ASA.  "}
            ).encode()
        )
    )

    assert response.status_code == 200
    assert response.headers["PAYMENT-RESPONSE"] == "ok"
    body = json.loads(response.description)
    assert body["settlement_tx_id"] == "TX123"
    item = body["request"]
    assert item["title"] == "Candles endpoint"
    assert item["description"] == "OHLCV for any ASA."
    # Durably stored under the id derived from the settling payment.
    stored = wired.get(request_id_for(settlement_tx_id="TX123"))
    assert stored is not None
    # The submitter comes from the settled payment, never from the request body.
    assert stored.submitter == _PAYER


def test_the_submitter_in_the_body_cannot_override_the_settled_payer(
    wired: InMemoryFeatureStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller cannot file a request in someone else's wallet's name."""
    monkeypatch.setattr(
        feature_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    feature_routes.x402_features_submit(
        _request(body=json.dumps({"title": "X", "submitter": _OTHER_PAYER}).encode())
    )

    stored = wired.get(request_id_for(settlement_tx_id="TX123"))
    assert stored is not None
    assert stored.submitter == _PAYER


def test_the_same_wallet_filing_the_same_title_twice_gets_two_requests(
    service: FeatureService,
) -> None:
    """A feature request is an event, not a renewable slot — paying twice states demand twice and must not collapse onto one row."""
    first = _file_request(service, title="Candles endpoint", txid="TX-1")
    second = _file_request(service, title="Candles endpoint", txid="TX-2")

    assert first != second
    assert len(service.list_recent(limit=50)) == 2


def test_unattributable_payments_do_not_collide_onto_one_request(
    service: FeatureService,
) -> None:
    """With no txid to key on, two requests must still get distinct ids rather than overwriting each other."""
    first = _file_request(service, title="One", submitter="", txid="")
    second = _file_request(service, title="Two", submitter="", txid="")

    assert first != second
    assert len(service.list_recent(limit=50)) == 2


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
    assert set(item) == {"request_id", "title", "description", "created_at_epoch"}
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
    # The paid surface carries what the free one withholds.
    assert body["items"][0]["submitter"] == _PAYER
    assert body["settlement_tx_id"] == "TXD1"


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
