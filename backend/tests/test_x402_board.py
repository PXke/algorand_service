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
    """Swap both Redis seams for one in-process fake shared by replay and rate limiting."""
    client = _FakeRedis()
    monkeypatch.setattr(replay_module, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
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
    store: InMemoryPlacementStore, *, payer: str = _PAYER, now: datetime | None = None
) -> StoredPlacement:
    return BoardService(store).create(
        normalized_link="https://agent.example.com/home",
        name="Agent",
        pitch="first",
        payer=payer,
        settlement_tx_id="TX1",
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
def test_renew_without_payment_is_a_402_at_the_placement_price_with_discovery(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing entry with no payment header yields a 402 at the board price, declaring Bazaar discovery and the owner-only rule."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_board_price", "$0.05")
    monkeypatch.setattr(board_routes, "board_service", BoardService(store))
    placement = _placed(store)

    response = board_routes.x402_board_renew(_request(path_params={"entry_id": placement.entry_id}))

    assert response.status_code == 402
    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    assert payment_required.accepts[0].amount == "50000"
    assert (payment_required.extensions or {}).get("bazaar") is not None
    assert "Only the wallet that placed" in (payment_required.resource.description or "")


def test_a_settled_renewal_extends_the_term_from_its_current_end_and_marks_fulfilled(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Renewing early adds a full term on top of what is left, keeps created_at, records the new txid, and marks the settlement fulfilled after the store write."""
    monkeypatch.setattr(settings, "x402_board_term_days", 14)
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
    assert body["term_end_epoch"] == placement.term_end_epoch + 14 * 86400
    assert body["created_at_epoch"] == placement.created_at_epoch
    assert body["settlement_tx_id"] == "TXR"
    stored = store.get(placement.entry_id)
    assert stored is not None
    assert stored.term_end_epoch == body["term_end_epoch"]
    assert fulfilled == [("TXR", "x402-board-renew")]


def test_renewing_an_expired_placement_starts_a_fresh_term_from_now(
    store: InMemoryPlacementStore,
) -> None:
    """After expiry the new term runs from now, not from the long-past term end."""
    service = BoardService(store)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    placement = _placed(store, now=base)
    later = base + timedelta(days=100)

    renewed = service.renew(placement=placement, payer=_PAYER, settlement_tx_id="TXR", now=later)

    assert renewed.term_end_epoch == int(
        (later + timedelta(days=settings.x402_board_term_days)).timestamp()
    )


def test_a_different_wallet_cannot_renew_someone_elses_tile(
    store: InMemoryPlacementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ownership is only knowable post-settlement: the other wallet's payment settles, gets a 403 with its receipt headers, the tile is untouched, and nothing is marked fulfilled."""
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
