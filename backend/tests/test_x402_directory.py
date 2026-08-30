"""x402 endpoint directory tests: paid listing, free search, and the gate's guarantees.

Fully offline. The facilitator is a stub that never touches the network (same
shape as test_x402_kyc_ping.py's), Redis is a fake at the get_redis seam, and
the store is the module's own in-memory backend. Nothing here settles a real
payment or reaches TestNet.
"""

from __future__ import annotations

import json
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
from app.modules.x402 import paid_request as payment_service
from app.modules.x402 import replay as replay_module
from app.modules.x402 import settlement as settlement_service
from app.modules.x402.settlement import InMemorySettlementStore, SettlementRecord
from app.modules.x402_directory.api import routes as directory_routes
from app.modules.x402_directory.models.domain import DirectoryError, StoredListing
from app.modules.x402_directory.services.listing_service import (
    ListingService,
    normalize_url,
    url_hash,
)
from app.modules.x402_directory.stores.memory import InMemoryListingStore

_PAY_TO = "A" * 58


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
    path: str = "/api/v1/x402/list",
) -> Request:
    return Request(
        method=method,
        headers=headers or {},
        query_params=QueryParams(query or {}),
        path_params={},
        body=body,
        url=SimpleNamespace(scheme="http", host="localhost", path=path),
    )


@pytest.fixture
def store() -> InMemoryListingStore:
    """A fresh in-memory listing store per test."""
    return InMemoryListingStore()


@pytest.fixture
def settlement_store() -> InMemorySettlementStore:
    """A fresh in-memory settlement ledger per test.

    Separate from the listing store since 2026-08-30 (settlement.py moved out
    of x402_directory, see its own module docstring).
    """
    return InMemorySettlementStore()


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
def test_list_without_payment_returns_402_with_correct_fields(
    store: InMemoryListingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No payment header yields a 402 whose offer carries the configured payTo, TestNet CAIP-2 id, USDC TestNet asset id and the challenge tag."""
    from x402.http.utils import decode_payment_required_header
    from x402.mechanisms.avm.constants import USDC_TESTNET_ASA_ID

    monkeypatch.setattr(settings, "x402_listing_price", "$0.10")
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))

    response = directory_routes.x402_list(
        _request(body=b'{"url":"https://a.example/x","price":"$0.01"}')
    )

    assert response.status_code == 402
    offer = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"]).accepts[0]
    assert offer.pay_to == _PAY_TO
    assert offer.network == ALGORAND_TESTNET_CAIP2
    assert offer.asset == str(USDC_TESTNET_ASA_ID)
    # 0.10 USDC in atomic units at 6 decimals.
    assert offer.amount == "100000"
    assert offer.extra["tag"] == x402_client.CHALLENGE_TAG


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_list_402_declares_bazaar_discovery_and_states_the_term(
    store: InMemoryListingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 402 declares the Bazaar discovery extension as a JSON-body one, and states the listing term where the payer sees it before committing."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))

    response = directory_routes.x402_list(
        _request(body=b'{"url":"https://a.example/x","price":"$0.01"}')
    )

    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    bazaar = (payment_required.extensions or {}).get("bazaar")
    assert bazaar is not None
    # POST takes its input as a body, not query params — a query-shaped
    # declaration would describe this route's input incorrectly to the Bazaar.
    assert "body" in json.dumps(bazaar)
    # The 30-day term must reach the payer before they commit.
    assert "30 days" in (payment_required.resource.description or "")


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_malformed_body_is_rejected_before_the_payment_gate(
    store: InMemoryListingStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A malformed body is a 400, not a 402 — nobody is charged to submit invalid JSON."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))

    response = directory_routes.x402_list(_request(body=b"{not json"))

    assert response.status_code == 400
    assert "invalid_request" in response.description


# --------------------------------------------------------------------------- #
# Settlement ledger + replay protection
# --------------------------------------------------------------------------- #
def _settled_result() -> x402_guard.PaymentResult:
    return x402_guard.PaymentResult(
        error=None,
        payer="P" * 58,
        settlement_headers={"PAYMENT-RESPONSE": "ok"},
        amount_atomic="100000",
        payment_txid="TX123",
        asset_id="10458941",
        network=ALGORAND_TESTNET_CAIP2,
    )


def test_settlement_is_written_to_the_ledger_with_every_required_field(
    settlement_store: InMemorySettlementStore,
) -> None:
    """Every settled payment lands in the ledger with asset id, amount, txid, payer, resource, UTC timestamp and EUR value."""
    settlement_service.record_settlement(
        _settled_result(), resource="x402-directory-list", store=settlement_store
    )

    assert len(settlement_store.settlements) == 1
    record = settlement_store.settlements[0]
    assert record.tx_id == "TX123"
    assert record.asset_id == "10458941"
    assert record.amount_atomic == "100000"
    assert record.payer == "P" * 58
    assert record.resource == "x402-directory-list"
    assert record.network == ALGORAND_TESTNET_CAIP2
    assert record.settled_at_epoch > 0
    assert record.eur_value == 0.0


def test_a_ledger_write_failure_never_drops_the_paid_response(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A settled payment whose ledger write fails is logged loudly and does NOT raise — the paid response must still be served."""

    class _BrokenStore(InMemorySettlementStore):
        def record_settlement(self, item: SettlementRecord) -> Never:  # noqa: ARG002 -- signature must match the Protocol
            raise RuntimeError("cassandra down")

    with caplog.at_level("ERROR"):
        settlement_service.record_settlement(
            _settled_result(), resource="x402-directory-list", store=_BrokenStore()
        )

    assert "SETTLEMENT LEDGER WRITE FAILED" in caplog.text
    # The row must be reconstructible from the log line alone.
    assert "TX123" in caplog.text
    assert "10458941" in caplog.text


@pytest.mark.usefixtures("testnet_settings")
def test_a_replayed_payment_header_is_rejected_before_reaching_settle(
    fake_redis: _FakeRedis,
    settlement_store: InMemorySettlementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A payment header already claimed is rejected with 409 without the gate — and so the facilitator's settle — ever running again."""
    calls: list[str] = []

    def _never_gate(*_args: object, **_kwargs: object) -> Never:
        calls.append("gate")
        raise AssertionError("require_payment must not run for a replayed header")

    request = _request(headers={"PAYMENT-SIGNATURE": "spent-header"})
    # Pre-claim the header, as a first, successful request would have.
    fake_redis.set(replay_module._replay_key("spent-header"), "1", nx=True, ex=900)
    monkeypatch.setattr(payment_service, "require_payment", _never_gate)

    result = payment_service.require_paid_request(
        request, price="$0.10", resource="x402-directory-list", settlement_store=settlement_store
    )

    assert result.error is not None
    assert result.error.status_code == 409
    assert "payment_replayed" in result.error.description
    assert calls == []


def test_replay_claim_ttl_is_at_least_twice_the_facilitator_timeout(
    fake_redis: _FakeRedis,
    settlement_store: InMemorySettlementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The replay key outlives two full facilitator timeouts, so a header cannot be re-presented while the first settle is still in flight."""
    from x402.http.facilitator_client_base import FacilitatorConfig

    monkeypatch.setattr(payment_service, "require_payment", lambda *_a, **_kw: _settled_result())

    payment_service.require_paid_request(
        _request(headers={"PAYMENT-SIGNATURE": "fresh-header"}),
        price="$0.10",
        resource="x402-directory-list",
        settlement_store=settlement_store,
    )

    key = replay_module._replay_key("fresh-header")
    assert fake_redis.store[key] == "1"
    assert fake_redis.expires[key] >= 2 * FacilitatorConfig().timeout


def test_a_failed_payment_releases_its_claim_so_a_retry_is_not_burned(
    fake_redis: _FakeRedis,
    settlement_store: InMemorySettlementStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A header whose payment never settled is un-claimed, so the payer can retry it."""
    from app.core.http_errors import json_error_response

    monkeypatch.setattr(
        payment_service,
        "require_payment",
        lambda *_a, **_kw: x402_guard.PaymentResult(
            error=json_error_response(402, "settlement_failed", "nope")
        ),
    )

    payment_service.require_paid_request(
        _request(headers={"PAYMENT-SIGNATURE": "unlucky-header"}),
        price="$0.10",
        resource="x402-directory-list",
        settlement_store=settlement_store,
    )

    assert replay_module._replay_key("unlucky-header") not in fake_redis.store


def test_replay_check_fails_open_when_redis_is_down(
    settlement_store: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Redis outage must not take the paid endpoint offline — the payment still goes through."""
    monkeypatch.setattr(replay_module, "get_redis", lambda **_kw: _BrokenRedis())
    monkeypatch.setattr(payment_service, "require_payment", lambda *_a, **_kw: _settled_result())

    result = payment_service.require_paid_request(
        _request(headers={"PAYMENT-SIGNATURE": "any-header"}),
        price="$0.10",
        resource="x402-directory-list",
        settlement_store=settlement_store,
    )

    assert result.error is None
    assert len(settlement_store.settlements) == 1


# --------------------------------------------------------------------------- #
# The paid listing path
# --------------------------------------------------------------------------- #
def test_a_settled_payment_stores_the_listing_and_returns_its_txid(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once payment settles, the listing is stored and returned with the settlement txid and the settlement headers."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(
        directory_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    response = directory_routes.x402_list(
        _request(
            body=json.dumps(
                {
                    "url": "HTTPS://API.Example.com/v1/Quote#frag",
                    "price": "$0.01",
                    "description": "Live FX quote",
                    "assets": ["USDC"],
                    "tags": ["FX", "market-data"],
                    "schema": {"type": "object"},
                }
            ).encode()
        )
    )

    assert response.status_code == 200
    assert response.headers["PAYMENT-RESPONSE"] == "ok"
    body = json.loads(response.description)
    assert body["settlement_tx_id"] == "TX123"
    listing = body["listing"]
    # Scheme and host lowercased, fragment dropped, path case preserved.
    assert listing["url"] == "https://api.example.com/v1/Quote"
    assert listing["tags"] == ["fx", "market-data"]
    assert listing["schema"] == {"type": "object"}
    assert listing["term_end_epoch"] > listing["created_at_epoch"]
    # And it is durably stored under the normalized URL's hash, not just echoed.
    assert store.get(url_hash("https://api.example.com/v1/Quote")) is not None


def test_relisting_the_same_url_replaces_it_rather_than_duplicating(
    store: InMemoryListingStore,
) -> None:
    """Re-listing a URL replaces the existing entry — the directory holds one listing per endpoint."""
    service = ListingService(store)
    service.create(
        url="https://api.example.com/v1/quote",
        price="$0.01",
        description="first",
        assets=[],
        tags=[],
        schema=None,
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    service.create(
        url="https://API.example.com/v1/quote",
        price="$0.02",
        description="second",
        assets=[],
        tags=[],
        schema=None,
        settlement_tx_id="TX2",
        payer="AGENT1",
    )

    items = service.search(limit=50)
    assert len(items) == 1
    assert items[0].description == "second"
    assert items[0].settlement_tx_id == "TX2"


def test_relisting_by_a_different_payer_is_refused(store: InMemoryListingStore) -> None:
    """A listing already owned by one payer cannot be silently overwritten by another."""
    service = ListingService(store)
    service.create(
        url="https://api.example.com/v1/quote",
        price="$0.01",
        description="the real thing",
        assets=[],
        tags=[],
        schema=None,
        settlement_tx_id="TX1",
        payer="AGENT1",
    )

    with pytest.raises(DirectoryError, match="already listed by a different payer"):
        service.create(
            url="https://api.example.com/v1/quote",
            price="$999.00",
            description="hijacked",
            assets=[],
            tags=[],
            schema=None,
            settlement_tx_id="TX2",
            payer="AGENT2",
        )

    # The original listing is untouched — payment for the hijack attempt was
    # taken (that's the route's problem, not this check's), but the entry
    # other agents see is still the real one.
    items = service.search(limit=50)
    assert len(items) == 1
    assert items[0].description == "the real thing"
    assert items[0].settlement_tx_id == "TX1"


def test_an_unowned_legacy_listing_can_be_claimed_by_anyone(store: InMemoryListingStore) -> None:
    """A listing with no recorded payer (pre-ownership-tracking data) isn't locked forever."""
    service = ListingService(store)
    store.upsert(
        StoredListing(
            url_hash=url_hash("https://api.example.com/v1/quote"),
            url="https://api.example.com/v1/quote",
            price="$0.01",
            description="pre-migration listing",
            schema_json="",
            settlement_tx_id="OLDTX",
            term_end_epoch=0,
            created_at_epoch=0,
            payer="",
        )
    )

    service.create(
        url="https://api.example.com/v1/quote",
        price="$0.02",
        description="claimed",
        assets=[],
        tags=[],
        schema=None,
        settlement_tx_id="TX2",
        payer="AGENT1",
    )

    items = service.search(limit=50)
    assert len(items) == 1
    assert items[0].description == "claimed"


def test_an_unattributable_payer_cannot_overwrite_an_owned_listing(
    store: InMemoryListingStore,
) -> None:
    """An empty/unattributable new payer must not bypass the ownership check."""
    service = ListingService(store)
    service.create(
        url="https://api.example.com/v1/quote",
        price="$0.01",
        description="the real thing",
        assets=[],
        tags=[],
        schema=None,
        settlement_tx_id="TX1",
        payer="AGENT1",
    )

    with pytest.raises(DirectoryError, match="already listed by a different payer"):
        service.create(
            url="https://api.example.com/v1/quote",
            price="$0.01",
            description="unattributable overwrite attempt",
            assets=[],
            tags=[],
            schema=None,
            settlement_tx_id="TX2",
            payer="",
        )


@pytest.mark.parametrize(
    "bad_url",
    ["ftp://example.com/x", "not-a-url", "https://", "   ", "https://x.example/" + "a" * 2100],
)
def test_invalid_endpoint_urls_are_rejected(bad_url: str) -> None:
    """Only bounded http/https URLs with a host may be listed."""
    from app.modules.x402_directory.models.domain import DirectoryError

    with pytest.raises(DirectoryError):
        normalize_url(bad_url)


# --------------------------------------------------------------------------- #
# The free search path
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_search_returns_listings_newest_first(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Search returns JSON listings ordered newest first."""
    from datetime import UTC, datetime, timedelta

    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    base = datetime(2026, 8, 1, tzinfo=UTC)
    service = ListingService(store)
    for index in range(3):
        service.create(
            url=f"https://api{index}.example.com/x",
            price="$0.01",
            description=f"endpoint {index}",
            assets=[],
            tags=[],
            schema=None,
            settlement_tx_id=f"TX{index}",
            payer=f"AGENT{index}",
            now=base + timedelta(hours=index),
        )

    result = directory_routes.x402_search(_request(method="GET", path="/api/v1/x402/search"))

    assert [item["description"] for item in result["items"]] == [
        "endpoint 2",
        "endpoint 1",
        "endpoint 0",
    ]


@pytest.mark.usefixtures("fake_redis")
def test_search_limit_is_clamped_to_the_configured_maximum(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller cannot ask for an unbounded listing — the limit is clamped."""
    monkeypatch.setattr(settings, "x402_search_max_results", 2)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    service = ListingService(store)
    for index in range(5):
        service.create(
            url=f"https://api{index}.example.com/x",
            price="$0.01",
            description=f"endpoint {index}",
            assets=[],
            tags=[],
            schema=None,
            settlement_tx_id=f"TX{index}",
            payer=f"AGENT{index}",
        )

    result = directory_routes.x402_search(
        _request(method="GET", query={"limit": "9999"}, path="/api/v1/x402/search")
    )

    assert len(result["items"]) == 2


@pytest.mark.usefixtures("fake_redis")
def test_search_is_rate_limited_per_ip(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An IP over the hourly budget gets a 429; a different IP is unaffected."""
    monkeypatch.setattr(settings, "x402_search_rate_limit_per_hour", 2)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))

    def _search(ip: str) -> Response | dict:
        return directory_routes.x402_search(
            _request(method="GET", headers={"X-Real-IP": ip}, path="/api/v1/x402/search")
        )

    assert "items" in _search("203.0.113.7")
    assert "items" in _search("203.0.113.7")
    limited = _search("203.0.113.7")
    assert limited.status_code == 429
    assert "items" in _search("203.0.113.9")


@pytest.mark.usefixtures("fake_redis")
def test_search_rate_limit_uses_x_real_ip_over_a_spoofed_forwarded_for(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client-supplied X-Forwarded-For cannot win itself a fresh bucket while X-Real-IP is set."""
    monkeypatch.setattr(settings, "x402_search_rate_limit_per_hour", 1)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))

    first = directory_routes.x402_search(
        _request(
            method="GET",
            headers={"X-Real-IP": "203.0.113.7", "X-Forwarded-For": "1.1.1.1"},
            path="/api/v1/x402/search",
        )
    )
    second = directory_routes.x402_search(
        _request(
            method="GET",
            headers={"X-Real-IP": "203.0.113.7", "X-Forwarded-For": "2.2.2.2"},
            path="/api/v1/x402/search",
        )
    )

    assert "items" in first
    assert second.status_code == 429


def test_search_fails_open_when_redis_is_down(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Redis outage must not take the free directory read offline."""
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: _BrokenRedis())
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))

    result = directory_routes.x402_search(
        _request(method="GET", headers={"X-Real-IP": "203.0.113.7"}, path="/api/v1/x402/search")
    )

    assert "items" in result
