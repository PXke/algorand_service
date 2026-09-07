"""x402 visibility-board tests: paid placement, free feed, and the gate's guarantees.

Fully offline. The facilitator is a stub that never touches the network (same
shape as test_x402_directory.py's), Redis is a fake at the get_redis seam, and
the store is the module's own in-memory backend. Nothing here settles a real
payment or reaches TestNet.

Replay protection and the settlement ledger are shared infrastructure
(modules/x402/) already covered by test_x402_directory.py -- they are not
re-tested here. What IS board-specific and tested here: the placement's
pair-key identity, the term-expiry filter, and the board's own price/term/
rate-limit settings.
"""

from __future__ import annotations

import json
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
from app.modules.x402_board.api import routes as board_routes
from app.modules.x402_board.models.domain import StoredPlacement
from app.modules.x402_board.services.board_service import (
    BoardService,
    normalize_link,
    placement_id,
)
from app.modules.x402_board.stores.memory import InMemoryPlacementStore

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

    def get(self, key: str) -> str | None:
        # circuit_breaker.is_tripped's plain read of the failure-count key.
        return self.store.get(key)

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
    path: str = "/api/v1/x402/board",
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


@pytest.fixture(autouse=True)
def _already_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the pre-parse 402 for header-less requests: every route test here models a request that already carries a payment (the gate is stubbed, or run against the offline facilitator), so the unpaid challenge is out of scope. Its ordering has its own tests in tests/test_x402_unpaid_challenge.py."""
    monkeypatch.setattr(board_routes, "challenge_if_unpaid", lambda *_a, **_kw: None)


@pytest.fixture
def store() -> InMemoryPlacementStore:
    """A fresh in-memory placement store per test."""
    return InMemoryPlacementStore()


@pytest.fixture
def testnet_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the gate at TestNet and the offline stub facilitator."""
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", _PAY_TO)
    monkeypatch.setattr(x402_guard, "get_resource_server", _stub_resource_server)


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    """Swap all three Redis seams for one in-process fake shared by replay, rate limiting, and the refund circuit breaker."""
    client = _FakeRedis()
    monkeypatch.setattr(replay_module, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
    # circuit_breaker.is_tripped reads app.core.redis_client's get_redis
    # directly (imported at module scope in circuit_breaker.py), not
    # rate_limit_core's -- patch it separately or it fails CLOSED (503) on
    # every board_place/board_renew test, same seam every other module's
    # tests needed this same fix for after migration 102's retrofit.
    monkeypatch.setattr(circuit_breaker, "get_redis", lambda **_kw: client)
    return client


# --------------------------------------------------------------------------- #
# The 402 offer
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_place_without_payment_returns_402_with_correct_fields(
    store: InMemoryPlacementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No payment header yields a 402 whose offer carries the configured payTo, TestNet CAIP-2 id, USDC TestNet asset id, the board's own price and the challenge tag."""
    from x402.http.utils import decode_payment_required_header
    from x402.mechanisms.avm.constants import USDC_TESTNET_ASA_ID

    monkeypatch.setattr(settings, "x402_board_price", "$0.05")
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    response = board_routes.x402_board_place(
        _request(body=b'{"link":"https://agent.example/x","pitch":"hi"}')
    )

    assert response.status_code == 402
    offer = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"]).accepts[0]
    assert offer.pay_to == _PAY_TO
    assert offer.network == ALGORAND_TESTNET_CAIP2
    assert offer.asset == str(USDC_TESTNET_ASA_ID)
    # 0.05 USDC in atomic units at 6 decimals — the board's price, not the
    # directory's $0.10.
    assert offer.amount == "50000"
    assert offer.extra["tag"] == x402_client.CHALLENGE_TAG


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_place_402_declares_bazaar_discovery_and_states_the_term(
    store: InMemoryPlacementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 402 declares the Bazaar discovery extension as a JSON-body one, and states the placement term where the payer sees it before committing."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_board_term_days", 14)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    response = board_routes.x402_board_place(
        _request(body=b'{"link":"https://agent.example/x","pitch":"hi"}')
    )

    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    bazaar = (payment_required.extensions or {}).get("bazaar")
    assert bazaar is not None
    # POST takes its input as a body, not query params — a query-shaped
    # declaration would describe this route's input incorrectly to the Bazaar.
    assert "body" in json.dumps(bazaar)
    # The 14-day term must reach the payer before they commit.
    assert "14 days" in (payment_required.resource.description or "")


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_malformed_body_is_rejected_before_the_payment_gate(
    store: InMemoryPlacementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed body is a 400, not a 402 — nobody is charged to submit invalid JSON."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    response = board_routes.x402_board_place(_request(body=b"{not json"))

    assert response.status_code == 400
    assert "invalid_request" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_a_bad_scheme_link_is_rejected_before_the_payment_gate(
    store: InMemoryPlacementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A length-valid but non-http(s) link is a 400 taken BEFORE the gate — the schema only bounds the link's length, so scheme/host rejection used to land after the payer had already been charged."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    def _must_not_charge(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("the payment gate must not run for an unplaceable link")

    monkeypatch.setattr(board_routes, "require_paid_request", _must_not_charge)

    response = board_routes.x402_board_place(
        _request(body=json.dumps({"link": "ftp://example.com", "name": "x"}).encode())
    )

    # 400, not the 402 an un-vetted link would have produced, and not a 200
    # after a settled payment.
    assert response.status_code == 400
    assert "invalid_request" in response.description
    assert "http or https" in response.description
    # Nothing was written either.
    assert board_routes.board_service.list_active(limit=50) == []


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_an_over_long_pitch_is_rejected_before_the_payment_gate(
    store: InMemoryPlacementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pitch beyond the board's 280-character cap is a 400, not a charged request."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    response = board_routes.x402_board_place(
        _request(body=json.dumps({"link": "https://a.example/x", "pitch": "z" * 281}).encode())
    )

    assert response.status_code == 400
    assert "invalid_request" in response.description


# --------------------------------------------------------------------------- #
# The paid placement path
# --------------------------------------------------------------------------- #
def test_a_settled_payment_stores_the_placement_and_returns_its_txid(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once payment settles, the placement is stored and returned with the settlement txid and headers."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(board_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())

    response = board_routes.x402_board_place(
        _request(
            body=json.dumps(
                {
                    "link": "HTTPS://Agent.Example.com/Home#frag",
                    "name": "Example Agent",
                    "pitch": "Autonomous FX arbitrage agent.",
                }
            ).encode()
        )
    )

    assert response.status_code == 200
    assert response.headers["PAYMENT-RESPONSE"] == "ok"
    body = json.loads(response.description)
    assert body["settlement_tx_id"] == "TX123"
    placement = body["placement"]
    # Scheme and host lowercased, fragment dropped, path case preserved.
    assert placement["link"] == "https://agent.example.com/Home"
    assert placement["name"] == "Example Agent"
    assert placement["pitch"] == "Autonomous FX arbitrage agent."
    # The payer comes from the settled payment, never from the request body.
    assert placement["payer"] == _PAYER
    assert placement["term_end_epoch"] > placement["created_at_epoch"]
    # And it is durably stored under its pair-key, not just echoed.
    assert (
        store.get(placement_id(owner=_PAYER, normalized_link="https://agent.example.com/Home"))
        is not None
    )


def test_the_payer_in_the_body_cannot_override_the_settled_payer(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller cannot claim someone else's wallet by putting a payer in the body."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(board_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())

    response = board_routes.x402_board_place(
        _request(body=json.dumps({"link": "https://a.example/x", "payer": _OTHER_PAYER}).encode())
    )

    assert response.status_code == 200
    assert json.loads(response.description)["placement"]["payer"] == _PAYER


def test_the_same_payer_replacing_their_own_link_renews_rather_than_duplicating(
    store: InMemoryPlacementStore,
) -> None:
    """Re-placing a link you already have on the board replaces your tile and re-stamps its term."""
    service = BoardService(store)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    service.create(
        normalized_link="https://agent.example.com/home",
        name="Agent",
        pitch="first",
        payer=_PAYER,
        settlement_tx_id="TX1",
        now=base,
    )
    service.create(
        # Normalized the way the route normalizes it, pre-gate: a differently
        # cased host is the SAME tile, not a second one the payer must buy again.
        normalized_link=normalize_link("https://AGENT.example.com/home"),
        name="Agent",
        pitch="second",
        payer=_PAYER,
        settlement_tx_id="TX2",
        now=base + timedelta(days=1),
    )

    items = service.list_active(limit=50, now=base + timedelta(days=1))
    assert len(items) == 1
    assert items[0].pitch == "second"
    assert items[0].settlement_tx_id == "TX2"
    # The renewed term runs from the second payment, not the first.
    assert items[0].created_at_epoch == int((base + timedelta(days=1)).timestamp())


def test_a_different_payer_cannot_overwrite_someone_elses_tile_for_the_same_link(
    store: InMemoryPlacementStore,
) -> None:
    """Two payers advertising the same link each get their own tile — paying the small fee must not hijack another payer's pitch text."""
    service = BoardService(store)
    service.create(
        normalized_link="https://agent.example.com/home",
        name="Real Agent",
        pitch="the genuine pitch",
        payer=_PAYER,
        settlement_tx_id="TX1",
    )
    service.create(
        normalized_link="https://agent.example.com/home",
        name="Impostor",
        pitch="defaced",
        payer=_OTHER_PAYER,
        settlement_tx_id="TX2",
    )

    items = service.list_active(limit=50)
    assert len(items) == 2
    # The original payer's tile is untouched.
    original = [item for item in items if item.payer == _PAYER]
    assert len(original) == 1
    assert original[0].pitch == "the genuine pitch"


def test_an_unattributable_payment_gets_its_own_tile_rather_than_colliding(
    store: InMemoryPlacementStore,
) -> None:
    """When the gate cannot attribute a payer, the txid stands in — two such payments must not overwrite each other."""
    service = BoardService(store)
    service.create(
        normalized_link="https://agent.example.com/home",
        name="",
        pitch="first",
        payer="",
        settlement_tx_id="TXA",
    )
    service.create(
        normalized_link="https://agent.example.com/home",
        name="",
        pitch="second",
        payer="",
        settlement_tx_id="TXB",
    )

    assert len(service.list_active(limit=50)) == 2


@pytest.mark.parametrize(
    "bad_link",
    ["ftp://example.com/x", "not-a-url", "https://", "   ", "https://x.example/" + "a" * 2100],
)
def test_invalid_links_are_rejected(bad_link: str) -> None:
    """Only bounded http/https links with a host may be placed."""
    from app.modules.x402_board.models.domain import BoardError

    with pytest.raises(BoardError):
        normalize_link(bad_link)


# --------------------------------------------------------------------------- #
# The free board feed
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_board_returns_placements_newest_first(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The free board returns JSON placements ordered newest first."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    # Anchored to real now, not a fixed date: this route reads the live clock,
    # so a hardcoded base would eventually fall outside the term and the feed
    # would correctly come back empty.
    base = datetime.now(tz=UTC) - timedelta(hours=3)
    service = BoardService(store)
    for index in range(3):
        service.create(
            normalized_link=f"https://agent{index}.example.com/x",
            name=f"Agent {index}",
            pitch=f"pitch {index}",
            payer=_PAYER,
            settlement_tx_id=f"TX{index}",
            now=base + timedelta(hours=index),
        )

    result = board_routes.x402_board_read(_request(method="GET"))

    assert [item["pitch"] for item in result["items"]] == ["pitch 2", "pitch 1", "pitch 0"]


def test_a_placement_whose_term_has_ended_is_no_longer_advertised(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paid term buys N days of visibility, not permanent placement."""
    monkeypatch.setattr(settings, "x402_board_term_days", 14)
    service = BoardService(store)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    service.create(
        normalized_link="https://agent.example.com/x",
        name="Agent",
        pitch="expiring",
        payer=_PAYER,
        settlement_tx_id="TX1",
        now=base,
    )

    assert len(service.list_active(limit=50, now=base + timedelta(days=13))) == 1
    assert service.list_active(limit=50, now=base + timedelta(days=15)) == []


@pytest.mark.usefixtures("fake_redis")
def test_board_limit_is_clamped_to_the_configured_maximum(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller cannot ask for an unbounded listing — the limit is clamped."""
    monkeypatch.setattr(settings, "x402_board_max_results", 2)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    service = BoardService(store)
    for index in range(5):
        service.create(
            normalized_link=f"https://agent{index}.example.com/x",
            name=f"Agent {index}",
            pitch=f"pitch {index}",
            payer=_PAYER,
            settlement_tx_id=f"TX{index}",
        )

    result = board_routes.x402_board_read(_request(method="GET", query={"limit": "9999"}))

    assert len(result["items"]) == 2


@pytest.mark.usefixtures("fake_redis")
def test_a_non_integer_limit_is_a_400(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-integer limit is rejected rather than silently ignored."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    result = board_routes.x402_board_read(_request(method="GET", query={"limit": "lots"}))

    assert result.status_code == 400
    assert "invalid_request" in result.description


@pytest.mark.usefixtures("fake_redis")
def test_board_read_is_rate_limited_per_ip(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An IP over the hourly budget gets a 429; a different IP is unaffected."""
    monkeypatch.setattr(settings, "x402_board_rate_limit_per_hour", 2)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    def _read(ip: str) -> Response | dict:
        return board_routes.x402_board_read(_request(method="GET", headers={"X-Real-IP": ip}))

    assert "items" in _read("203.0.113.7")
    assert "items" in _read("203.0.113.7")
    limited = _read("203.0.113.7")
    assert limited.status_code == 429
    assert "items" in _read("203.0.113.9")


@pytest.mark.usefixtures("fake_redis")
def test_board_read_rate_limit_is_separate_from_the_directory_search_budget(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exhausting the board's budget must not also lock the caller out of directory search."""
    from app.modules.x402_directory.api import routes as directory_routes
    from app.modules.x402_directory.services.listing_service import ListingService
    from app.modules.x402_directory.stores.memory import InMemoryListingStore

    monkeypatch.setattr(settings, "x402_board_rate_limit_per_hour", 1)
    monkeypatch.setattr(settings, "x402_search_rate_limit_per_hour", 10)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(InMemoryListingStore()))

    headers = {"X-Real-IP": "203.0.113.7"}
    assert "items" in board_routes.x402_board_read(_request(method="GET", headers=headers))
    assert board_routes.x402_board_read(_request(method="GET", headers=headers)).status_code == 429
    # The directory's own counter is untouched.
    assert "items" in directory_routes.x402_search(
        _request(method="GET", headers=headers, path="/api/v1/x402/search")
    )


def test_board_read_fails_open_when_redis_is_down(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Redis outage must not take the free board read offline."""
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: _BrokenRedis())
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    result = board_routes.x402_board_read(
        _request(method="GET", headers={"X-Real-IP": "203.0.113.7"})
    )

    assert "items" in result


# --------------------------------------------------------------------------- #
# POST /board/:entry_id/renew — paid, owner only
# --------------------------------------------------------------------------- #
def _placed(
    store: InMemoryPlacementStore,
    *,
    payer: str = _PAYER,
    category: str = "other",
    now: datetime | None = None,
) -> StoredPlacement:
    return BoardService(store).create(
        normalized_link="https://agent.example.com/home",
        name="Agent",
        pitch="first",
        payer=payer,
        settlement_tx_id="TX1",
        category=category,
        now=now,
    )


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_renewing_an_unknown_entry_is_a_404_that_never_reaches_the_gate(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existence is checked before the gate: a renewal of a missing entry costs nothing."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    def _must_not_charge(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("the payment gate must not run for an unknown entry")

    monkeypatch.setattr(board_routes, "require_paid_request", _must_not_charge)

    response = board_routes.x402_board_renew(
        _request(path_params={"entry_id": "nope"}, path="/api/v1/x402/board/nope/renew")
    )

    assert response.status_code == 404
    assert "not_found" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_renew_without_payment_is_a_402_at_the_boost_price_with_discovery(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing entry with no payment header yields a 402 at the board's boost price, declaring Bazaar discovery and the owner-only rule."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_board_boost_price", "$0.05")
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    placement = _placed(store)

    response = board_routes.x402_board_renew(_request(path_params={"entry_id": placement.entry_id}))

    assert response.status_code == 402
    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    assert payment_required.accepts[0].amount == "50000"
    assert (payment_required.extensions or {}).get("bazaar") is not None
    assert "Only the wallet that placed" in (payment_required.resource.description or "")
    # One Bazaar entry for the route template, not one per entry id; and a
    # POST must declare a body extension or the facilitator's validator
    # rejects it and never catalogs the route (found live 2026-09-05).
    assert payment_required.resource.url.endswith("/api/v1/x402/board/{entry_id}/renew")
    assert validate_discovery_extension(payment_required.extensions["bazaar"]).valid


def test_a_settled_boost_stacks_from_its_current_end_and_never_touches_the_term_and_marks_fulfilled(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Boosting early adds a full boost window on top of what is left, keeps created_at AND term_end untouched, records the new txid, and marks the settlement fulfilled after the store write.

    Regression coverage for the 2026-09-06 repurposing: renew() used to
    extend term_end_epoch (survival); the board is not probed, so nothing
    refreshes term_end any more -- it now stacks onto boosted_until_epoch
    (search-ranking priority) and must never move term_end_epoch.
    """
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(settings, "x402_board_term_days", 14)
    monkeypatch.setattr(settings, "x402_board_boost_days", 3)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(
        board_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXR")
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        board_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)) or True,
    )
    # Placed an hour ago: the term still has ~14 days left.
    placed_at = datetime.now(tz=UTC) - timedelta(hours=1)
    placement = _placed(store, now=placed_at)

    response = board_routes.x402_board_renew(_request(path_params={"entry_id": placement.entry_id}))

    assert response.status_code == 200
    body = json.loads(response.description)["placement"]
    assert body["entry_id"] == placement.entry_id
    assert body["term_end_epoch"] == placement.term_end_epoch
    assert body["created_at_epoch"] == placement.created_at_epoch
    assert body["settlement_tx_id"] == "TXR"
    now_epoch = int(datetime.now(tz=UTC).timestamp())
    assert abs(body["boosted_until_epoch"] - (now_epoch + 3 * 86400)) < 5
    stored = store.get(placement.entry_id)
    assert stored is not None
    assert stored.term_end_epoch == placement.term_end_epoch
    assert stored.boosted_until_epoch == body["boosted_until_epoch"]
    assert fulfilled == [("TXR", "x402-board-boost")]


def test_boosting_an_expired_placement_starts_a_fresh_boost_window_from_now(
    store: InMemoryPlacementStore,
) -> None:
    """After the placement's term has lapsed, a boost still starts from now (not the long-past boost end) and does not resurrect term_end."""
    service = BoardService(store)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    placement = _placed(store, now=base)
    later = base + timedelta(days=100)

    renewed = service.renew(placement=placement, payer=_PAYER, settlement_tx_id="TXR", now=later)

    assert renewed.boosted_until_epoch == int(
        (later + timedelta(days=settings.x402_board_boost_days)).timestamp()
    )
    assert renewed.term_end_epoch == placement.term_end_epoch


def test_a_different_wallet_cannot_renew_someone_elses_tile(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ownership is only knowable post-settlement: the other wallet's payment settles, gets a 403 with its receipt headers, the tile is untouched, and nothing is marked fulfilled."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(
        board_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_OTHER_PAYER, txid="TXX"),
    )
    monkeypatch.setattr(
        board_routes,
        "mark_fulfilled",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not mark fulfilled")),
    )
    placement = _placed(store)

    response = board_routes.x402_board_renew(_request(path_params={"entry_id": placement.entry_id}))

    assert response.status_code == 403
    assert "placement_owned_by_another_payer" in response.description
    assert response.headers["PAYMENT-RESPONSE"] == "ok"
    assert store.get(placement.entry_id) == placement


def test_an_unattributable_payment_cannot_renew(store: InMemoryPlacementStore) -> None:
    """No payer, no proof of ownership."""
    from app.modules.x402_board.models.domain import BoardError

    placement = _placed(store)
    with pytest.raises(BoardError) as excinfo:
        BoardService(store).renew(placement=placement, payer="", settlement_tx_id="TXR")
    assert excinfo.value.http_status == 403


# --------------------------------------------------------------------------- #
# GET /board/:entry_id/go — free click-through
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_click_through_redirects_counts_and_shows_in_the_feed(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A click is a 302 to the placement link, bumps the counter, and the feed exposes the total; the feed read itself never bumps it."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    placement = _placed(store)

    for _ in range(2):
        response = board_routes.x402_board_go(
            _request(method="GET", path_params={"entry_id": placement.entry_id})
        )
        assert response.status_code == 302
        assert response.headers["Location"] == "https://agent.example.com/home"
        assert response.headers["Cache-Control"] == "no-store"

    feed = board_routes.x402_board_read(_request(method="GET"))
    assert feed["items"][0]["entry_id"] == placement.entry_id
    assert feed["items"][0]["clicks"] == 2
    feed = board_routes.x402_board_read(_request(method="GET"))
    assert feed["items"][0]["clicks"] == 2
    assert store.get_click_counts([placement.entry_id]) == {placement.entry_id: 2}


@pytest.mark.usefixtures("fake_redis")
def test_click_through_on_an_unknown_or_expired_entry_is_a_404(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ended term stops being advertised, redirects included; nothing is counted."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    expired = _placed(store, now=datetime(2026, 1, 1, tzinfo=UTC))

    assert (
        board_routes.x402_board_go(
            _request(method="GET", path_params={"entry_id": "nope"})
        ).status_code
        == 404
    )
    assert (
        board_routes.x402_board_go(
            _request(method="GET", path_params={"entry_id": expired.entry_id})
        ).status_code
        == 404
    )
    assert store.get_click_counts([expired.entry_id]) == {}


@pytest.mark.usefixtures("fake_redis")
def test_click_through_is_rate_limited_per_ip_on_its_own_counter(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An IP over the hourly click budget gets a 429, and that budget is separate from the feed's."""
    monkeypatch.setattr(settings, "x402_board_rate_limit_per_hour", 1)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    placement = _placed(store)
    headers = {"X-Real-IP": "203.0.113.7"}

    def _go() -> Response:
        return board_routes.x402_board_go(
            _request(method="GET", headers=headers, path_params={"entry_id": placement.entry_id})
        )

    assert _go().status_code == 302
    assert _go().status_code == 429
    assert "items" in board_routes.x402_board_read(_request(method="GET", headers=headers))


def test_a_counter_failure_does_not_break_the_redirect(store: InMemoryPlacementStore) -> None:
    """The visitor asked for the link; a bookkeeping blip is logged, not a 5xx."""

    def _boom(_entry_id: str) -> Never:
        raise ConnectionError("cassandra down")

    store.increment_clicks = _boom  # type: ignore[method-assign]
    placement = _placed(store)

    assert BoardService(store).click(placement.entry_id) == "https://agent.example.com/home"


# --------------------------------------------------------------------------- #
# Renew: an unattributable placement is refused BEFORE the gate
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_renewing_an_ownerless_placement_is_a_409_that_never_reaches_the_gate(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tile with no payer can never pass the owner check, so nobody is charged to find that out."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    ownerless = BoardService(store).create(
        normalized_link="https://agent.example.com/home",
        name="Agent",
        pitch="anon",
        payer="",
        settlement_tx_id="TXANON",
    )
    assert ownerless.payer == ""

    def _must_not_charge(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("the payment gate must not run for an unrenewable placement")

    monkeypatch.setattr(board_routes, "require_paid_request", _must_not_charge)

    response = board_routes.x402_board_renew(_request(path_params={"entry_id": ownerless.entry_id}))

    assert response.status_code == 409
    assert "not_renewable" in response.description
    assert store.get(ownerless.entry_id) == ownerless


# --------------------------------------------------------------------------- #
# /go redirect hygiene
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_click_through_is_noindex_and_sends_no_referrer(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The open redirect must not lend ranking to, or leak visitor paths to, the tile's link."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    placement = _placed(store)

    response = board_routes.x402_board_go(
        _request(method="GET", path_params={"entry_id": placement.entry_id})
    )

    assert response.status_code == 302
    assert response.headers["X-Robots-Tag"] == "noindex"
    assert response.headers["Referrer-Policy"] == "no-referrer"
    assert response.headers["Cache-Control"] == "no-store"


# --------------------------------------------------------------------------- #
# Probe / self wallets are never served
# --------------------------------------------------------------------------- #
_PROBE = "B" * 58


@pytest.mark.usefixtures("fake_redis")
def test_a_probe_payers_placement_is_stored_but_never_served_on_the_board(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Our own wallet's tile exists (the probe's round-trip works) but is dropped from the feed in code."""
    monkeypatch.setattr(settings, "x402_probe_payers", f"{_PROBE.lower()},{_OTHER_PAYER}")
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    probe_tile = _placed(store, payer=_PROBE)
    real_tile = _placed(store, payer=_PAYER)

    assert store.get(probe_tile.entry_id) is not None
    feed = board_routes.x402_board_read(_request(method="GET"))
    assert [item["entry_id"] for item in feed["items"]] == [real_tile.entry_id]
    assert [p.entry_id for p in BoardService(store).list_active(limit=10)] == [real_tile.entry_id]


# --------------------------------------------------------------------------- #
# Admin delete
# --------------------------------------------------------------------------- #
def _delete_request(entry_id: str, headers: dict[str, str] | None = None) -> Request:
    return _request(
        method="DELETE",
        headers=headers,
        query={"entry_id": entry_id},
        path="/api/v1/admin/x402/board",
    )


def test_admin_board_delete_without_admin_session_is_rejected(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real require_admin_wallet runs first; a self-asserted header proves nothing."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    placement = _placed(store)

    response = board_routes.x402_admin_delete_placement(
        _delete_request(placement.entry_id, headers={"X-Admin-Wallet": _PAYER})
    )

    assert getattr(response, "status_code", 200) != 200
    assert store.get(placement.entry_id) is not None


@pytest.mark.usefixtures("fake_redis")
def test_admin_board_delete_removes_the_tile_from_get_and_feed(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An authorized delete removes the placement outright; a repeat is a 404."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(board_routes, "require_admin_wallet", lambda _request: None)
    placement = _placed(store)
    store.increment_clicks(placement.entry_id)

    response = board_routes.x402_admin_delete_placement(_delete_request(placement.entry_id))

    assert response == {"deleted": True, "entry_id": placement.entry_id}
    assert store.get(placement.entry_id) is None
    assert board_routes.x402_board_read(_request(method="GET"))["items"] == []
    # The click counter is deliberately left behind (unreachable, harmless).
    assert store.get_click_counts([placement.entry_id]) == {placement.entry_id: 1}
    assert (
        board_routes.x402_admin_delete_placement(_delete_request(placement.entry_id)).status_code
        == 404
    )
    assert board_routes.x402_admin_delete_placement(_delete_request("")).status_code == 400


# --------------------------------------------------------------------------- #
# Auto-refund + circuit breaker (migration 102 retrofit)
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_a_place_write_failure_after_settlement_gets_refunded_not_500(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A product-write exception after payment settles triggers a refund response, never a bare 500."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(
        board_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXP-FAIL")
    )

    def _boom(*_a: object, **_kw: object) -> Never:
        raise RuntimeError("simulated board-store failure")

    monkeypatch.setattr(board_routes.board_service, "create", _boom)
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: SimpleNamespace(status="sent", txid="REFUND1", error=None),
    )

    response = board_routes.x402_board_place(
        _request(
            body=json.dumps(
                {"link": "https://agent.example.com", "name": "A", "pitch": "p"}
            ).encode()
        )
    )

    assert response.status_code == 503
    body = json.loads(response.description)
    assert body["error"]["code"] in ("product_failed_refunded", "product_failed_refund_pending")


def test_the_place_circuit_breaker_blocks_before_the_payment_gate_once_tripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once tripped, x402-board-place is refused before require_paid_request ever runs -- no further money at risk."""

    def _must_not_charge(*_a: object, **_kw: object) -> Never:
        raise AssertionError("the payment gate must not run while the breaker is tripped")

    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: True)
    monkeypatch.setattr(board_routes, "require_paid_request", _must_not_charge)

    response = board_routes.x402_board_place(_request(body=b"{}"))

    assert response.status_code == 503
    assert "temporarily_disabled" in response.description


def test_the_renew_circuit_breaker_blocks_before_the_payment_gate_once_tripped(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same guarantee on renew: tripped means refused before the gate, before the existence/ownership checks even run."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: True)

    def _must_not_charge(*_a: object, **_kw: object) -> Never:
        raise AssertionError("the payment gate must not run while the breaker is tripped")

    monkeypatch.setattr(board_routes, "require_paid_request", _must_not_charge)

    response = board_routes.x402_board_renew(_request(path_params={"entry_id": "whatever"}))

    assert response.status_code == 503
    assert "temporarily_disabled" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_a_renew_ownership_rejection_is_never_refunded(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The deliberate, payment-keeping 403 must never trigger send_refund or count against the circuit breaker -- refunding it would silently undo the intended disincentive against renewing someone else's tile."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(
        board_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_OTHER_PAYER, txid="TXX"),
    )
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: (_ for _ in ()).throw(
            AssertionError("an ownership rejection must never attempt a refund")
        ),
    )
    monkeypatch.setattr(
        circuit_breaker,
        "record_refund_failure",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("an ownership rejection must never count as a refund-triggering failure")
        ),
    )
    placement = _placed(store)

    response = board_routes.x402_board_renew(_request(path_params={"entry_id": placement.entry_id}))

    assert response.status_code == 403
    assert "placement_owned_by_another_payer" in response.description
    assert store.get(placement.entry_id) == placement


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_a_renew_write_failure_after_ownership_confirmed_gets_refunded_not_500(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlike the ownership rejection, a genuinely unexpected failure (e.g. the store write) AFTER ownership is confirmed valid IS refunded."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    placement = _placed(store)
    monkeypatch.setattr(
        board_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_PAYER, txid="TXR-FAIL"),
    )

    def _boom(*_a: object, **_kw: object) -> Never:
        raise RuntimeError("simulated board-store failure during renew")

    monkeypatch.setattr(board_routes.board_service, "renew", _boom)
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: SimpleNamespace(status="sent", txid="REFUND2", error=None),
    )

    response = board_routes.x402_board_renew(_request(path_params={"entry_id": placement.entry_id}))

    assert response.status_code == 503
    body = json.loads(response.description)
    assert body["error"]["code"] in ("product_failed_refunded", "product_failed_refund_pending")


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_the_real_board_breaker_trips_after_enough_recorded_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end against the real (fake-Redis-backed) breaker, not a stub."""
    monkeypatch.setattr(settings, "x402_refund_breaker_max_failures", 2)

    resource = board_routes._RESOURCE_PLACE
    assert circuit_breaker.is_tripped(resource) is False
    circuit_breaker.record_refund_failure(resource)
    assert circuit_breaker.is_tripped(resource) is False
    circuit_breaker.record_refund_failure(resource)
    assert circuit_breaker.is_tripped(resource) is True


def test_cassandra_epoch_treats_a_naive_driver_datetime_as_utc() -> None:
    """_epoch must treat a timezone-naive datetime as UTC, not the interpreter's local zone.

    That's what the real Cassandra driver actually returns for a `timestamp` column. Same bug
    class root-caused 2026-09-03 live on prod (a CEST/UTC+2 host) in x402_social's identical
    _epoch helper: a value written correctly via _dt(epoch) = datetime.fromtimestamp(epoch,
    tz=UTC) read back exactly 2 hours earlier than it was written, because `value.timestamp()`
    on a naive datetime assumes the *local* system zone. This module's _epoch had the identical
    bug, unfixed at the time (flagged, not fixed, in the x402_social fix commit).

    Constructs the naive datetime explicitly rather than relying on this test's own execution
    environment happening to run in a non-UTC zone (which would make the bug invisible in CI).
    """
    from app.modules.x402_board.stores import cassandra as cassandra_store

    naive = datetime(2026, 9, 4, 13, 18, 17)  # noqa: DTZ001 -- naive on purpose, see docstring
    assert naive.tzinfo is None
    assert cassandra_store._epoch(naive) == 1788527897  # the correct UTC epoch
    assert cassandra_store._epoch(None) == 0


# --------------------------------------------------------------------------- #
# Promo-bypass identity spoofing (found 2026-09-04, same pattern as the
# x402_social reversal in commit f8d84a6): x402_board_place and
# x402_board_renew both feed `payer` in as the wallet that OWNS the
# placement (create()'s owner attribution, renew()'s
# `attributed != placement.payer` check, enforced identically inside
# board_service.renew()) -- not merely payment attribution.
# modules/x402/promo.py's own docstring says a promo redemption's wallet is
# checked for SYNTACTIC validity only ("a successful redemption is not proof
# the caller controls that wallet"), so a promo bypass would have let anyone
# place "as" any wallet, or free-renew (or probe the ownership check of) a
# placement they do not own, via `?promo_wallet=`. Closed by never reading
# promo params on these two routes at all. These tests lock in the opposite
# invariant: promo params in the query string are ignored, not honored.
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
@pytest.mark.parametrize("route_name", ["x402_board_place", "x402_board_renew"])
def test_place_and_renew_never_forward_promo_params(
    store: InMemoryPlacementStore,
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
) -> None:
    """Neither paid write route may pass ?promo=/?promo_wallet= into require_paid_request, even when present in the query string."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    routes_by_name = {
        "x402_board_place": (
            "/api/v1/x402/board",
            {"body": b'{"link":"https://agent.example.com/home"}'},
        ),
        "x402_board_renew": (
            "/api/v1/x402/board/{entry_id}/renew",
            {},
        ),
    }
    path, extra_kwargs = routes_by_name[route_name]
    if route_name == "x402_board_renew":
        placement = _placed(store)
        path = f"/api/v1/x402/board/{placement.entry_id}/renew"
        extra_kwargs = {"path_params": {"entry_id": placement.entry_id}}

    captured: dict = {}

    def _spy_require_paid_request(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return _settled_result()

    monkeypatch.setattr(board_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(board_routes, "mark_fulfilled", lambda *_a, **_kw: None)

    route = getattr(board_routes, route_name)
    route(
        _request(
            query={"promo": "LAUNCH1000-TEST", "promo_wallet": _PAYER},
            path=path,
            **extra_kwargs,
        )
    )

    assert "promo_code" not in captured
    assert "promo_wallet" not in captured


# --------------------------------------------------------------------------- #
# Category filter (migration 120)
# --------------------------------------------------------------------------- #
def test_an_omitted_category_defaults_to_other(store: InMemoryPlacementStore) -> None:
    """No `category` on create() stores DEFAULT_BOARD_CATEGORY ('other')."""
    placement = BoardService(store).create(
        normalized_link="https://agent.example.com/home",
        name="Agent",
        pitch="first",
        payer=_PAYER,
        settlement_tx_id="TX1",
    )
    assert placement.category == "other"


def test_category_filter_returns_only_matching_placements(
    store: InMemoryPlacementStore,
) -> None:
    """`list_active(category=...)` returns only placements in that category; unfiltered browse is unaffected."""
    service = BoardService(store)
    finance = service.create(
        normalized_link="https://finance.example.com/x",
        name="Finance Bot",
        pitch="fx",
        payer=_PAYER,
        settlement_tx_id="TX1",
        category="finance",
    )
    service.create(
        normalized_link="https://social.example.com/x",
        name="Social Bot",
        pitch="chat",
        payer=_OTHER_PAYER,
        settlement_tx_id="TX2",
        category="social",
    )

    filtered = service.list_active(limit=50, category="finance")
    assert [item.entry_id for item in filtered] == [finance.entry_id]

    unfiltered = service.list_active(limit=50)
    assert len(unfiltered) == 2


@pytest.mark.usefixtures("fake_redis")
def test_an_unknown_category_is_rejected_before_the_payment_gate(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bogus `category` on POST /board is a 400 taken BEFORE the gate — nobody is charged for it."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    def _must_not_charge(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("the payment gate must not run for an unplaceable category")

    monkeypatch.setattr(board_routes, "require_paid_request", _must_not_charge)

    response = board_routes.x402_board_place(
        _request(
            body=json.dumps(
                {"link": "https://agent.example.com/x", "category": "nonsense"}
            ).encode()
        )
    )

    assert response.status_code == 400
    assert "invalid_request" in response.description
    assert board_routes.board_service.list_active(limit=50) == []


def test_a_settled_placement_stores_and_serves_its_declared_category(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid `category` on POST /board is stored and served back on the placement."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(board_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())

    response = board_routes.x402_board_place(
        _request(
            body=json.dumps({"link": "https://agent.example.com/x", "category": "AI"}).encode()
        )
    )

    assert response.status_code == 200
    assert json.loads(response.description)["placement"]["category"] == "ai"


@pytest.mark.usefixtures("fake_redis")
def test_board_read_filters_by_category(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /board?category= only returns tiles in that category."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    service = BoardService(store)
    service.create(
        normalized_link="https://finance.example.com/x",
        name="Finance Bot",
        pitch="fx",
        payer=_PAYER,
        settlement_tx_id="TX1",
        category="finance",
    )
    service.create(
        normalized_link="https://social.example.com/x",
        name="Social Bot",
        pitch="chat",
        payer=_OTHER_PAYER,
        settlement_tx_id="TX2",
        category="social",
    )

    result = board_routes.x402_board_read(_request(method="GET", query={"category": "finance"}))

    assert [item["name"] for item in result["items"]] == ["Finance Bot"]


@pytest.mark.usefixtures("fake_redis")
def test_board_read_rejects_an_unknown_category(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /board?category=bogus is a 400, not an empty result — an agent should learn its filter is wrong."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    result = board_routes.x402_board_read(_request(method="GET", query={"category": "bogus"}))

    assert result.status_code == 400
    assert "invalid_request" in result.description


# --------------------------------------------------------------------------- #
# Click analytics (migration 120): the click-event log and its aggregation
# --------------------------------------------------------------------------- #
def test_click_records_a_real_event_and_history_buckets_it_by_day(
    store: InMemoryPlacementStore,
) -> None:
    """Each click() call writes a click event, and click_history() buckets them by UTC calendar day."""
    service = BoardService(store)
    placement = _placed(store, now=datetime(2026, 9, 1, tzinfo=UTC))

    day1 = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    day2 = datetime(2026, 9, 2, 9, 0, tzinfo=UTC)
    service.click(placement.entry_id, now=day1)
    service.click(placement.entry_id, now=day1)
    service.click(placement.entry_id, now=day2)

    # Anchored to "now" being day2 so the 2-day window covers exactly these clicks.
    history = service.click_history(placement.entry_id, days=2, limit=100, now=day2)

    assert history is not None
    assert history["entry_id"] == placement.entry_id
    assert history["total_clicks_in_window"] == 3
    assert history["capped"] is False
    by_date = {row["date"]: row["clicks"] for row in history["daily"]}
    assert by_date[day1.date().isoformat()] == 2
    assert by_date[day2.date().isoformat()] == 1


def test_click_history_zero_fills_days_with_no_clicks(store: InMemoryPlacementStore) -> None:
    """A day inside the window with no clicks is reported as 0, not omitted."""
    service = BoardService(store)
    base = datetime(2026, 9, 1, tzinfo=UTC)
    placement = _placed(store, now=base)
    service.click(placement.entry_id, now=base)

    history = service.click_history(placement.entry_id, days=3, limit=100, now=base)

    assert history is not None
    assert len(history["daily"]) == 3
    assert sum(row["clicks"] for row in history["daily"]) == 1


def test_click_history_respects_the_row_cap_and_reports_capped(
    store: InMemoryPlacementStore,
) -> None:
    """More click events than the configured row cap: the read stops at the cap and says so."""
    service = BoardService(store)
    now = datetime(2026, 9, 1, tzinfo=UTC)
    placement = _placed(store, now=now)
    for _ in range(5):
        service.click(placement.entry_id, now=now)

    history = service.click_history(placement.entry_id, days=1, limit=3, now=now)

    assert history is not None
    assert history["total_clicks_in_window"] == 3
    assert history["capped"] is True


def test_click_history_on_an_unknown_entry_is_none(store: InMemoryPlacementStore) -> None:
    """click_history() returns None for an unknown entry, same shape as get()."""
    assert BoardService(store).click_history("nope", days=7, limit=100) is None


def test_click_records_only_a_coarse_hostname_referrer(store: InMemoryPlacementStore) -> None:
    """A full referring URL (path, query string) is never stored — only the bare host."""
    base = datetime(2026, 9, 1, tzinfo=UTC)
    placement = _placed(store, now=base)
    service = BoardService(store)

    service.click(
        placement.entry_id,
        referrer="https://Example.COM/path?utm_source=abc&session=xyz",
        now=base,
    )

    history = service.click_history(placement.entry_id, days=1, limit=100, now=base)
    assert history is not None
    assert history["top_referrers"] == [{"referrer": "example.com", "clicks": 1}]


def test_click_through_route_captures_the_referer_header(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /board/:entry_id/go reads the caller's Referer header and records a coarse form of it."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    placement = _placed(store)

    board_routes.x402_board_go(
        _request(
            method="GET",
            path_params={"entry_id": placement.entry_id},
            headers={"Referer": "https://linking-site.example/some/page"},
        )
    )

    history = BoardService(store).click_history(placement.entry_id, days=1, limit=100)
    assert history is not None
    assert history["top_referrers"] == [{"referrer": "linking-site.example", "clicks": 1}]


def test_a_malformed_referer_header_does_not_break_the_redirect(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A garbage Referer header must not turn a real click-through into a failed redirect."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    placement = _placed(store)

    response = board_routes.x402_board_go(
        _request(
            method="GET",
            path_params={"entry_id": placement.entry_id},
            headers={"Referer": "::: not a url :::"},
        )
    )

    assert response.status_code == 302
    history = BoardService(store).click_history(placement.entry_id, days=1, limit=100)
    assert history is not None
    assert history["total_clicks_in_window"] == 1


# --------------------------------------------------------------------------- #
# GET /board/:entry_id/clicks — paid, owner-only click analytics
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_click_history_route_without_payment_returns_402_at_the_click_history_price(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No payment header yields a 402 at the configured click-history price."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_board_click_history_price", "$0.01")
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    placement = _placed(store)

    response = board_routes.x402_board_click_history(
        _request(method="GET", path_params={"entry_id": placement.entry_id})
    )

    assert response.status_code == 402
    offer = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"]).accepts[0]
    assert offer.amount == "10000"


@pytest.mark.usefixtures("fake_redis")
def test_click_history_route_unknown_entry_is_a_404_that_never_reaches_the_gate(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Existence is checked before the gate: reading analytics for a missing entry costs nothing."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))

    def _must_not_charge(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("the payment gate must not run for an unknown entry")

    monkeypatch.setattr(board_routes, "require_paid_request", _must_not_charge)

    response = board_routes.x402_board_click_history(
        _request(method="GET", path_params={"entry_id": "nope"})
    )

    assert response.status_code == 404
    assert "not_found" in response.description


@pytest.mark.usefixtures("fake_redis")
def test_click_history_route_on_an_ownerless_placement_is_a_409_that_never_reaches_the_gate(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tile with no attributed payer can never pass the ownership check, so nobody is charged to find that out."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    ownerless = BoardService(store).create(
        normalized_link="https://agent.example.com/home",
        name="Agent",
        pitch="anon",
        payer="",
        settlement_tx_id="TXANON",
    )

    def _must_not_charge(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("the payment gate must not run for an unreadable placement")

    monkeypatch.setattr(board_routes, "require_paid_request", _must_not_charge)

    response = board_routes.x402_board_click_history(
        _request(method="GET", path_params={"entry_id": ownerless.entry_id})
    )

    assert response.status_code == 409
    assert "not_readable" in response.description


def test_click_history_route_rejects_a_non_owner_payment_kept_not_refunded(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different wallet's payment settles, gets a 403 with its receipt headers, and no data is returned — the payment is kept, not refunded."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(
        board_routes,
        "require_paid_request",
        lambda *_a, **_kw: _settled_result(payer=_OTHER_PAYER, txid="TXX"),
    )
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: (_ for _ in ()).throw(
            AssertionError("an ownership rejection must never attempt a refund")
        ),
    )
    monkeypatch.setattr(
        board_routes,
        "mark_fulfilled",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must not mark fulfilled")),
    )
    placement = _placed(store)

    response = board_routes.x402_board_click_history(
        _request(method="GET", path_params={"entry_id": placement.entry_id})
    )

    assert response.status_code == 403
    assert "placement_owned_by_another_payer" in response.description
    assert response.headers["PAYMENT-RESPONSE"] == "ok"


def test_click_history_route_returns_the_owners_own_analytics_and_marks_fulfilled(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The placing wallet's own settled read returns the real analytics payload and marks fulfilled."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(
        board_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXH")
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        board_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)) or True,
    )
    placement = _placed(store)
    BoardService(store).click(placement.entry_id, now=datetime.now(tz=UTC))

    response = board_routes.x402_board_click_history(
        _request(method="GET", path_params={"entry_id": placement.entry_id}, query={"days": "7"})
    )

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["entry_id"] == placement.entry_id
    assert body["days"] == 7
    assert body["total_clicks_in_window"] == 1
    assert body["settlement_tx_id"] == "TXH"
    assert fulfilled == [("TXH", "x402-board-click-history")]


def test_click_history_route_clamps_days_to_the_configured_maximum(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller cannot ask for an unbounded window — `days` is clamped."""
    monkeypatch.setattr(settings, "x402_board_click_history_max_days", 5)
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    monkeypatch.setattr(board_routes, "require_paid_request", lambda *_a, **_kw: _settled_result())
    monkeypatch.setattr(board_routes, "mark_fulfilled", lambda *_a, **_kw: None)
    placement = _placed(store)

    response = board_routes.x402_board_click_history(
        _request(method="GET", path_params={"entry_id": placement.entry_id}, query={"days": "9999"})
    )

    assert response.status_code == 200
    assert json.loads(response.description)["days"] == 5


def test_click_history_route_never_forwards_promo_params(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same promo-off invariant as place/renew: the ownership check compares `payer` against the placement's owner."""
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    placement = _placed(store)
    captured: dict = {}

    def _spy_require_paid_request(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return _settled_result()

    monkeypatch.setattr(board_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(board_routes, "mark_fulfilled", lambda *_a, **_kw: None)

    board_routes.x402_board_click_history(
        _request(
            method="GET",
            path_params={"entry_id": placement.entry_id},
            query={"promo": "LAUNCH1000-TEST", "promo_wallet": _PAYER},
        )
    )

    assert "promo_code" not in captured
    assert "promo_wallet" not in captured
