"""x402 endpoint directory tests: paid listing, free search, and the gate's guarantees.

Fully offline. The facilitator is a stub that never touches the network (same
shape as test_x402_kyc_ping.py's), Redis is a fake at the get_redis seam, and
the store is the module's own in-memory backend. Nothing here settles a real
payment or reaches TestNet.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Never

import httpx
import pytest

pytest.importorskip("x402")

from algorand_shared.x402_statements import PROBE_QUEUE_NEVER_PROBED
from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2
from x402.schemas.payments import PaymentRequirements
from x402.schemas.responses import SupportedKind, SupportedResponse
from x402.schemas.v1 import PaymentRequirementsV1
from x402.server import x402ResourceServerSync

from app.core import rate_limit as rate_limit_core
from app.core import serialization
from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.x402 import circuit_breaker as circuit_breaker_module
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import paid_request as payment_service
from app.modules.x402 import replay as replay_module
from app.modules.x402 import settlement as settlement_service
from app.modules.x402.settlement import InMemorySettlementStore, SettlementRecord
from app.modules.x402_directory.api import routes as directory_routes
from app.modules.x402_directory.models.domain import (
    LISTING_CATEGORIES,
    SOURCE_AUTO_DISCOVERED,
    SOURCE_PAID,
    DirectoryError,
    StoredListing,
    StoredProbe,
)
from app.modules.x402_directory.services.discovery_import import (
    DISCOVERY_SOURCE_LABEL,
    fetch_facilitator_resources,
    import_discovered_resources,
)
from app.modules.x402_directory.services.listing_service import (
    MAX_SCHEMA_JSON_BYTES,
    PROBE_LEADERBOARD_MIN_SAMPLES,
    ListingService,
    normalize_url,
    url_hash,
)
from app.modules.x402_directory.stores.memory import InMemoryListingStore
from app.schemas import X402ListingRequest

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

    def get(self, key: str) -> str | None:
        return self.store.get(key)

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

    def get(self, *_args: object, **_kwargs: object) -> Never:
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


@pytest.fixture(autouse=True)
def _already_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the pre-parse 402 for header-less requests: every route test here models a request that already carries a payment (the gate is stubbed, or run against the offline facilitator), so the unpaid challenge is out of scope. Its ordering has its own tests in tests/test_x402_unpaid_challenge.py."""
    monkeypatch.setattr(directory_routes, "challenge_if_unpaid", lambda *_a, **_kw: None)


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
    """Swap all three Redis seams for one in-process fake: replay, rate limiting, and the refund circuit breaker (2026-09-02 retrofit)."""
    client = _FakeRedis()
    monkeypatch.setattr(replay_module, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(circuit_breaker_module, "get_redis", lambda **_kw: client)
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
    # No oracle is reachable in this test, so the EUR value is the explicit
    # "unavailable" sentinel -- never a 0.0 that reads as a real valuation.
    assert record.eur_value == settlement_service.EUR_VALUE_UNAVAILABLE
    assert record.fulfilled is False


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
@pytest.mark.usefixtures("fake_redis")
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


# --------------------------------------------------------------------------- #
# Auto-refund + circuit breaker retrofit (2026-09-02)
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_list_unexpected_product_write_failure_attempts_a_refund_not_a_500(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine unexpected failure (not a DirectoryError -- e.g. a store write bug) on x402_list goes through run_with_refund, never a bare 500, and is never marked fulfilled."""

    class _BrokenStore(InMemoryListingStore):
        def insert_if_absent(self, *_a: object, **_kw: object) -> bool:
            raise RuntimeError("simulated store write failure")

    monkeypatch.setattr(directory_routes, "listing_service", ListingService(_BrokenStore()))
    monkeypatch.setattr(
        directory_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        directory_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    response = directory_routes.x402_list(
        _request(
            body=json.dumps({"url": "https://api.example.com/v1/quote", "price": "$0.01"}).encode()
        )
    )

    assert response.status_code == 503
    assert json.loads(response.description)["error"]["code"] == "product_failed_refund_pending"
    assert fulfilled == []


@pytest.mark.usefixtures("fake_redis")
def test_list_is_refused_before_the_gate_once_its_circuit_breaker_is_tripped(
    monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis
) -> None:
    """Once x402-directory-list's breaker is tripped, the route refuses BEFORE require_paid_request -- no further money is ever at risk."""
    fake_redis.store[f"algorand:x402:refund_breaker:{directory_routes._LIST_RESOURCE}"] = str(
        settings.x402_refund_breaker_max_failures
    )
    gate_called = False

    def _must_not_be_called(*_a: object, **_kw: object) -> Never:
        nonlocal gate_called
        gate_called = True
        raise AssertionError("require_paid_request must not run while the breaker is tripped")

    monkeypatch.setattr(directory_routes, "require_paid_request", _must_not_be_called)

    response = directory_routes.x402_list(
        _request(
            body=json.dumps({"url": "https://api.example.com/v1/quote", "price": "$0.01"}).encode()
        )
    )

    assert response.status_code == 503
    assert json.loads(response.description)["error"]["code"] == "temporarily_disabled"
    assert gate_called is False


@pytest.mark.usefixtures("fake_redis")
def test_renew_is_refused_before_the_gate_once_its_circuit_breaker_is_tripped(
    monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis
) -> None:
    """Same guard as x402_list, for x402_renew's own (separate) resource id."""
    fake_redis.store[f"algorand:x402:refund_breaker:{directory_routes._RENEW_RESOURCE}"] = str(
        settings.x402_refund_breaker_max_failures
    )

    def _must_not_be_called(*_a: object, **_kw: object) -> Never:
        raise AssertionError("require_paid_request must not run while the breaker is tripped")

    monkeypatch.setattr(directory_routes, "require_paid_request", _must_not_be_called)

    response = directory_routes.x402_renew(_renew_request("https://api.example.com/q"))

    assert response.status_code == 503
    assert json.loads(response.description)["error"]["code"] == "temporarily_disabled"


def test_relisting_the_same_url_replaces_it_rather_than_duplicating(
    store: InMemoryListingStore,
) -> None:
    """Re-listing a URL replaces the existing entry — the directory holds one listing per endpoint."""
    service = ListingService(store)
    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="first",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    service.create(
        normalized_url=normalize_url("https://API.example.com/v1/quote"),
        price="$0.02",
        description="second",
        assets=[],
        tags=[],
        schema_json="",
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
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="the real thing",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )

    with pytest.raises(DirectoryError, match="already listed by a different payer"):
        service.create(
            normalized_url="https://api.example.com/v1/quote",
            price="$999.00",
            description="hijacked",
            assets=[],
            tags=[],
            schema_json="",
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
        normalized_url="https://api.example.com/v1/quote",
        price="$0.02",
        description="claimed",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX2",
        payer="AGENT1",
    )

    items = service.search(limit=50)
    assert len(items) == 1
    assert items[0].description == "claimed"


def test_a_listing_whose_term_has_ended_can_be_relisted_by_a_different_payer(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired listing is unowned, the same way an empty-payer one is -- one paid term does not squat a url against every future payer forever.

    Regression: create()'s ownership check used to look only at
    existing.payer, ignoring existing.term_end_epoch, so a listing whose paid
    term had already ended still permanently blocked a different payer from
    relisting the same url even though search() had already stopped serving
    it.
    """
    term_days = 30
    monkeypatch.setattr(settings, "x402_listing_term_days", term_days)
    service = ListingService(store)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="the original, now-expired listing",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        now=base,
    )

    # AGENT1's term ended after 30 days; this relist happens well after that,
    # by a DIFFERENT payer, and must succeed rather than raise.
    after_expiry = base + timedelta(days=term_days + 1)
    relisted = service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.02",
        description="relisted after the original term lapsed",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX2",
        payer="AGENT2",
        now=after_expiry,
    )

    assert relisted.payer == "AGENT2"
    items = service.search(limit=50, now=after_expiry)
    assert len(items) == 1
    assert items[0].description == "relisted after the original term lapsed"
    assert items[0].payer == "AGENT2"


def test_an_unattributable_payer_cannot_overwrite_an_owned_listing(
    store: InMemoryListingStore,
) -> None:
    """An empty/unattributable new payer must not bypass the ownership check."""
    service = ListingService(store)
    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="the real thing",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )

    with pytest.raises(DirectoryError, match="already listed by a different payer"):
        service.create(
            normalized_url="https://api.example.com/v1/quote",
            price="$0.01",
            description="unattributable overwrite attempt",
            assets=[],
            tags=[],
            schema_json="",
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
# Nothing chargeable is charged for a request that was already doomed
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis")
@pytest.mark.parametrize(
    "bad_url",
    ["ftp://example.com/x", "not-a-url", "https://", "https://x.example/" + "a" * 2100],
)
def test_a_malformed_url_is_rejected_before_the_payment_gate(
    bad_url: str, store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A URL that create() would reject is a 400 BEFORE the gate — never a charged 400.

    Regression: the scheme/host check used to run only inside create(), after
    require_paid_request had already settled the payment, so listing
    "ftp://example.com/x" charged the payer and then handed them a 400.
    """
    gate_calls: list[str] = []

    def _record_gate(*_args: object, **_kwargs: object) -> Never:
        gate_calls.append("gate")
        raise AssertionError("the payment gate must not run for an invalid url")

    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(directory_routes, "require_paid_request", _record_gate)

    response = directory_routes.x402_list(
        _request(body=json.dumps({"url": bad_url, "price": "$0.01"}).encode())
    )

    assert response.status_code == 400
    assert "invalid_request" in response.description
    # The gate never ran, so nothing was settled and nothing was stored.
    assert gate_calls == []
    assert store.list_recent(limit=50) == []


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_an_oversized_schema_is_rejected_before_the_payment_gate(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A schema blob over the cap is a 400 before the gate — an unbounded paid write that the free search would then serve back inline is never accepted, and never charged for."""
    gate_calls: list[str] = []

    def _record_gate(*_args: object, **_kwargs: object) -> Never:
        gate_calls.append("gate")
        raise AssertionError("the payment gate must not run for an oversized schema")

    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(directory_routes, "require_paid_request", _record_gate)

    response = directory_routes.x402_list(
        _request(
            body=json.dumps(
                {
                    "url": "https://api.example.com/v1/quote",
                    "price": "$0.01",
                    "schema": {"blob": "x" * (MAX_SCHEMA_JSON_BYTES + 1)},
                }
            ).encode()
        )
    )

    assert response.status_code == 400
    assert "invalid_request" in response.description
    assert gate_calls == []
    assert store.list_recent(limit=50) == []


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_a_schema_within_the_cap_is_still_accepted(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap bounds the field, it does not disable it — a normal request schema still lists."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(
        directory_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    response = directory_routes.x402_list(
        _request(
            body=json.dumps(
                {
                    "url": "https://api.example.com/v1/quote",
                    "price": "$0.01",
                    "schema": {"type": "object", "properties": {"pair": {"type": "string"}}},
                }
            ).encode()
        )
    )

    assert response.status_code == 200
    listing = json.loads(response.description)["listing"]
    assert listing["schema"] == {"type": "object", "properties": {"pair": {"type": "string"}}}


@pytest.mark.parametrize("field_name", ["assets", "tags"])
def test_an_overlong_asset_or_tag_item_is_rejected_at_decode(field_name: str) -> None:
    """Each item of assets/tags is length-bounded, not just the item count — one paid listing cannot carry an unbounded string per item.

    Decoding is the pre-gate step, so this is a 400 the caller is never charged
    for, on the same pass as the malformed-body rejection.
    """
    body = json.dumps(
        {"url": "https://api.example.com/v1/quote", "price": "$0.01", field_name: ["x" * 65]}
    ).encode()

    with pytest.raises(serialization.DecodeError):
        serialization.decode(body, X402ListingRequest)

    # ... and the same field is fine at the bound.
    ok = json.dumps(
        {"url": "https://api.example.com/v1/quote", "price": "$0.01", field_name: ["x" * 64]}
    ).encode()
    assert getattr(serialization.decode(ok, X402ListingRequest), field_name) == ["x" * 64]


# --------------------------------------------------------------------------- #
# The free search path
# --------------------------------------------------------------------------- #
def test_a_listing_whose_term_has_ended_is_no_longer_served(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A paid term buys N days in the directory, not a permanent entry.

    Regression: nothing on the read path looked at term_end_epoch, so one
    payment listed a URL forever even though the 402 offer sells N days.
    """
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    service = ListingService(store)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="expiring",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        now=base,
    )

    assert len(service.search(limit=50, now=base + timedelta(days=29))) == 1
    assert service.search(limit=50, now=base + timedelta(days=31)) == []


@pytest.mark.usefixtures("fake_redis")
def test_search_serves_live_listings_and_drops_expired_ones(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The free search route itself excludes expired listings while still serving live ones."""
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    service = ListingService(store)
    now = datetime.now(tz=UTC)
    service.create(
        normalized_url="https://expired.example.com/x",
        price="$0.01",
        description="term ended",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        now=now - timedelta(days=31),
    )
    service.create(
        normalized_url="https://live.example.com/x",
        price="$0.01",
        description="still paid up",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX2",
        payer="AGENT2",
        now=now - timedelta(days=1),
    )

    result = directory_routes.x402_search(_request(method="GET", path="/api/v1/x402/search"))

    assert [item["description"] for item in result["items"]] == ["still paid up"]


@pytest.mark.usefixtures("fake_redis")
def test_search_results_expose_the_payer(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A search result includes the listing's payer wallet.

    Regression: _listing_json() omitted `payer` even though the arguably more
    sensitive settlement_tx_id was already included, so a search caller could
    not tell who currently owns a listing.
    """
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    service = ListingService(store)
    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="live",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )

    result = directory_routes.x402_search(_request(method="GET", path="/api/v1/x402/search"))

    assert result["items"][0]["payer"] == "AGENT1"


@pytest.mark.usefixtures("fake_redis")
def test_search_returns_listings_newest_first(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Search returns JSON listings ordered newest first."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    # Anchored to real now, not a fixed date: this route reads the live clock
    # to drop expired listings, so a hardcoded base would eventually fall
    # outside the term and the feed would correctly come back empty.
    base = datetime.now(tz=UTC) - timedelta(hours=3)
    service = ListingService(store)
    for index in range(3):
        service.create(
            normalized_url=f"https://api{index}.example.com/x",
            price="$0.01",
            description=f"endpoint {index}",
            assets=[],
            tags=[],
            schema_json="",
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
            normalized_url=f"https://api{index}.example.com/x",
            price="$0.01",
            description=f"endpoint {index}",
            assets=[],
            tags=[],
            schema_json="",
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


# --------------------------------------------------------------------------- #
# Admin delist
# --------------------------------------------------------------------------- #
def _delete_request(url: str) -> Request:
    return _request(method="DELETE", query={"url": url}, path="/api/v1/admin/x402/listings")


def test_admin_delist_without_admin_wallet_is_rejected(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No admin session at all -> whatever require_admin_wallet returns, never a check result.

    The real require_admin_wallet is left in place here (unlike the other
    admin-delist tests, which patch it to simulate an authorized caller): with
    no ADMIN_WALLET_ADDRESSES configured in the test environment it 503s, the
    same shape test_admin_health_checks.py's requires_admin_wallet test
    exercises for another admin route.
    """
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))

    response = directory_routes.x402_admin_delete_listing(
        _delete_request("https://api.example.com/v1/quote")
    )

    assert getattr(response, "status_code", 200) != 200


def test_admin_delist_wrong_wallet_is_rejected(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller whose session resolves to a non-admin wallet is refused, and nothing is deleted."""
    from app.core.http_errors import json_error_response

    service = ListingService(store)
    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="still here",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    monkeypatch.setattr(directory_routes, "listing_service", service)
    monkeypatch.setattr(
        directory_routes,
        "require_admin_wallet",
        lambda _request: json_error_response(403, "forbidden", "not an admin wallet"),
    )

    response = directory_routes.x402_admin_delete_listing(
        _delete_request("https://api.example.com/v1/quote")
    )

    assert response.status_code == 403
    assert store.get(url_hash("https://api.example.com/v1/quote")) is not None


def test_admin_delist_removes_the_listing_from_get_and_search(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An authorized admin delist removes the listing outright, not just hides it until term end."""
    service = ListingService(store)
    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="a squatted or disputed listing",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    monkeypatch.setattr(directory_routes, "listing_service", service)
    monkeypatch.setattr(directory_routes, "require_admin_wallet", lambda _request: None)

    response = directory_routes.x402_admin_delete_listing(
        _delete_request("https://api.example.com/v1/quote")
    )

    assert getattr(response, "status_code", 200) == 200
    assert store.get(url_hash("https://api.example.com/v1/quote")) is None
    assert service.search(limit=50) == []


def test_admin_delist_nonexistent_listing_returns_404(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a url that was never listed is a 404, not a silent no-op success."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(directory_routes, "require_admin_wallet", lambda _request: None)

    response = directory_routes.x402_admin_delete_listing(
        _delete_request("https://never-listed.example.com/x")
    )

    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# First-insert race (INSERT ... IF NOT EXISTS)
# --------------------------------------------------------------------------- #
class _RacingStore(InMemoryListingStore):
    """A store where a rival's first-time insert lands between our check and our write.

    On the FIRST insert_if_absent call it behaves as if a concurrent lister
    (`rival`) won the lightweight transaction a moment earlier: the rival's
    row is stored, the caller's is not, and False is returned -- exactly what
    Cassandra's `[applied] = false` means. Every later call is the plain
    in-memory behaviour. Nothing here goes through get() first, so a service
    that still did read-then-upsert would never see the rival's row.
    """

    def __init__(self, rival: StoredListing) -> None:
        super().__init__()
        self._rival = rival
        self.insert_attempts = 0

    def insert_if_absent(self, item: StoredListing) -> bool:
        self.insert_attempts += 1
        if self.insert_attempts == 1:
            super().upsert(self._rival)
            return False
        return super().insert_if_absent(item)


def _rival_listing(*, payer: str, now: datetime, term_days: int = 30) -> StoredListing:
    return StoredListing(
        url_hash=url_hash("https://api.example.com/v1/quote"),
        url="https://api.example.com/v1/quote",
        price="$0.01",
        description="the rival got there first",
        schema_json="",
        settlement_tx_id="TX-RIVAL",
        term_end_epoch=int((now + timedelta(days=term_days)).timestamp()),
        created_at_epoch=int(now.timestamp()),
        payer=payer,
    )


def test_a_first_time_lister_who_loses_the_insert_race_is_refused_not_overwriting() -> None:
    """Regression: two concurrent first-time listers of one url must not silently overwrite each other.

    Before the conditional insert, create() did get() -> None, then upsert():
    both racers passed the ownership check and the last write won, discarding
    the other payer's paid listing. Now the loser of the INSERT IF NOT EXISTS
    is held to the ownership rule against the winner's row.
    """
    now = datetime.now(tz=UTC)
    store = _RacingStore(_rival_listing(payer="AGENT1", now=now))
    service = ListingService(store)

    with pytest.raises(DirectoryError, match="already listed by a different payer"):
        service.create(
            normalized_url="https://api.example.com/v1/quote",
            price="$999.00",
            description="the loser's version",
            assets=[],
            tags=[],
            schema_json="",
            settlement_tx_id="TX-LOSER",
            payer="AGENT2",
            now=now,
        )

    stored = store.get(url_hash("https://api.example.com/v1/quote"))
    assert stored is not None
    assert stored.payer == "AGENT1"
    assert stored.settlement_tx_id == "TX-RIVAL"
    assert store.insert_attempts == 1


def test_losing_the_insert_race_to_your_own_wallet_still_relists() -> None:
    """The race loser proceeds through the relist path when the winner is the same payer (or the row is unowned)."""
    now = datetime.now(tz=UTC)
    store = _RacingStore(_rival_listing(payer="AGENT1", now=now))
    service = ListingService(store)

    listing = service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.02",
        description="my own retry",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX-RETRY",
        payer="AGENT1",
        now=now,
    )

    stored = store.get(url_hash("https://api.example.com/v1/quote"))
    assert stored == listing
    assert stored.settlement_tx_id == "TX-RETRY"


def test_losing_the_insert_race_to_an_expired_listing_still_relists() -> None:
    """A winner whose term has already ended is unowned, so the race loser may take the url."""
    now = datetime.now(tz=UTC)
    store = _RacingStore(_rival_listing(payer="AGENT1", now=now - timedelta(days=60)))
    service = ListingService(store)

    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.02",
        description="fresh term",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX-NEW",
        payer="AGENT2",
        now=now,
    )

    stored = store.get(url_hash("https://api.example.com/v1/quote"))
    assert stored is not None
    assert stored.payer == "AGENT2"


def test_a_first_time_listing_goes_through_the_conditional_insert(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first-time path never falls back to a blind upsert -- that is the race the LWT exists to close."""
    calls: list[str] = []
    real_insert = store.insert_if_absent
    real_upsert = store.upsert
    monkeypatch.setattr(
        store, "insert_if_absent", lambda item: calls.append("insert") or real_insert(item)
    )
    monkeypatch.setattr(store, "upsert", lambda item: calls.append("upsert") or real_upsert(item))

    ListingService(store).create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="first",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )

    assert calls == ["insert"]


# --------------------------------------------------------------------------- #
# Tag-filtered search
# --------------------------------------------------------------------------- #
def _seed_tagged(service: ListingService, *, now: datetime) -> None:
    service.create(
        normalized_url="https://fx.example.com/quote",
        price="$0.01",
        description="fx live",
        assets=[],
        tags=["FX", " Market-Data "],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        now=now - timedelta(days=1),
    )
    service.create(
        normalized_url="https://nft.example.com/floor",
        price="$0.01",
        description="nft live",
        assets=[],
        tags=["nft"],
        schema_json="",
        settlement_tx_id="TX2",
        payer="AGENT2",
        now=now - timedelta(hours=1),
    )
    service.create(
        normalized_url="https://old-fx.example.com/quote",
        price="$0.01",
        description="fx expired",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX3",
        payer="AGENT3",
        now=now - timedelta(days=31),
    )


def test_search_by_tag_returns_only_live_listings_carrying_that_tag(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`tag=` narrows to listings stored with that tag, still dropping expired ones."""
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    service = ListingService(store)
    now = datetime.now(tz=UTC)
    _seed_tagged(service, now=now)

    assert [i.description for i in service.search(limit=50, tag="fx", now=now)] == ["fx live"]
    # Normalized the same way the write side normalizes: case and whitespace insensitive.
    assert [i.description for i in service.search(limit=50, tag=" FX ", now=now)] == ["fx live"]
    assert [i.description for i in service.search(limit=50, tag="market-data", now=now)] == [
        "fx live"
    ]
    # Unfiltered search still serves every live listing, newest first.
    assert [i.description for i in service.search(limit=50, now=now)] == ["nft live", "fx live"]


def test_search_by_unknown_tag_is_empty(store: InMemoryListingStore) -> None:
    """A tag nobody listed under is an empty result, not an error."""
    service = ListingService(store)
    _seed_tagged(service, now=datetime.now(tz=UTC))

    assert service.search(limit=50, tag="nonexistent") == []


@pytest.mark.parametrize("bad_tag", ["   ", "x" * 65])
def test_search_rejects_blank_or_overlong_tags(store: InMemoryListingStore, bad_tag: str) -> None:
    """A blank or over-long tag is invalid_request, not a silent empty result or an unbounded key."""
    with pytest.raises(DirectoryError, match="tag must be"):
        ListingService(store).search(limit=50, tag=bad_tag)


@pytest.mark.usefixtures("fake_redis")
def test_search_route_accepts_a_tag_query_param(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /x402/search?tag= is wired through to the tag-filtered search, and a bad tag is a 400."""
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    _seed_tagged(ListingService(store), now=datetime.now(tz=UTC))

    result = directory_routes.x402_search(
        _request(method="GET", path="/api/v1/x402/search", query={"tag": "NFT"})
    )
    assert [item["description"] for item in result["items"]] == ["nft live"]

    empty = directory_routes.x402_search(
        _request(method="GET", path="/api/v1/x402/search", query={"tag": "nothing"})
    )
    assert empty["items"] == []

    bad = directory_routes.x402_search(
        _request(method="GET", path="/api/v1/x402/search", query={"tag": "x" * 65})
    )
    assert bad.status_code == 400


@pytest.mark.usefixtures("fake_redis")
def test_the_listing_offer_advertises_the_tag_filter(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 402 offer's resource description tells a payer that search takes ?tag=."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    captured: dict[str, Any] = {}

    def _capture(_request: Request, **kwargs: Any) -> Never:  # noqa: ANN401 -- mirrors the gate's kwargs
        captured.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(directory_routes, "require_paid_request", _capture)
    with pytest.raises(RuntimeError, match="stop"):
        directory_routes.x402_list(
            _request(
                body=json.dumps({"url": "https://api.example.com/v1/quote", "price": "$1"}).encode()
            )
        )

    assert "?tag=" in captured["description"]
    body_schema = captured["extensions"]["bazaar"]["schema"]["properties"]["input"]["properties"][
        "body"
    ]
    assert "?tag=" in body_schema["properties"]["tags"]["description"]


def test_relisting_with_different_tags_moves_the_tag_projection(
    store: InMemoryListingStore,
) -> None:
    """A relist drops the url from tags it no longer carries and adds it to the new ones."""
    service = ListingService(store)
    key = url_hash("https://api.example.com/v1/quote")
    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="v1",
        assets=[],
        tags=["fx", "old"],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="v2",
        assets=[],
        tags=["fx", "new"],
        schema_json="",
        settlement_tx_id="TX2",
        payer="AGENT1",
    )

    assert store.tag_rows("old") == []
    assert store.tag_rows("new") == [key]
    assert [i.description for i in service.search(limit=50, tag="fx")] == ["v2"]


def test_admin_delist_removes_the_tag_projection_rows(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a listing also removes every by-tag row for it, so a tag search cannot resurrect it."""
    service = ListingService(store)
    key = url_hash("https://api.example.com/v1/quote")
    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="tagged",
        assets=[],
        tags=["fx", "market-data"],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    assert store.tag_rows("fx") == [key]
    monkeypatch.setattr(directory_routes, "listing_service", service)
    monkeypatch.setattr(directory_routes, "require_admin_wallet", lambda _request: None)

    response = directory_routes.x402_admin_delete_listing(
        _delete_request("https://api.example.com/v1/quote")
    )

    assert getattr(response, "status_code", 200) == 200
    assert store.tag_rows("fx") == []
    assert store.tag_rows("market-data") == []
    assert service.search(limit=50, tag="fx") == []


# --------------------------------------------------------------------------- #
# Probe / verified badge read side (migration 097)
# --------------------------------------------------------------------------- #
def _probe(url_hash: str, **overrides: object) -> StoredProbe:
    base: dict[str, object] = {
        "url_hash": url_hash,
        "url": "https://api.example.com/q",
        "probed_at_epoch": 1_700_000_000,
        "reachable": True,
        "http_status": 402,
        "latency_ms": 120,
        "served_valid_402": True,
        "payto_seen": "AGENT1",
        "error": "",
    }
    base.update(overrides)
    return StoredProbe(**base)  # type: ignore[arg-type]


def _listed(store: InMemoryListingStore, url: str, payer: str = "AGENT1") -> StoredListing:
    return ListingService(store).create(
        normalized_url=url,
        price="$0.01",
        description="probe me",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX",
        payer=payer,
    )


@pytest.mark.usefixtures("fake_redis")
def test_search_serves_the_verified_badge_only_while_it_belongs_to_the_payer(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """verified_wallet is served when it equals payer and blanked when a new owner relisted."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    listing = _listed(store, "https://api.example.com/q")
    store.upsert(replace(listing, verified_wallet="AGENT1", verified_at_epoch=42))
    item = directory_routes.x402_search(_request(method="GET", path="/api/v1/x402/search"))[
        "items"
    ][0]
    assert item["verified_wallet"] == "AGENT1"
    assert item["verified_at_epoch"] == 42

    stale = replace(listing, payer="AGENT2", verified_wallet="AGENT1", verified_at_epoch=42)
    store.upsert(stale)
    item = directory_routes.x402_search(_request(method="GET", path="/api/v1/x402/search"))[
        "items"
    ][0]
    assert item["verified_wallet"] == ""
    assert item["verified_at_epoch"] == 0


@pytest.mark.usefixtures("fake_redis")
def test_probe_status_returns_the_latest_probe_for_a_listed_url(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /x402/directory/probe?url= serves the newest probe row plus the badge for that listing."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    listing = _listed(store, "https://api.example.com/q")
    store.record_probe(_probe(listing.url_hash, latency_ms=77))

    result = directory_routes.x402_probe_status(
        _request(
            method="GET",
            path="/api/v1/x402/directory/probe",
            query={"url": "HTTPS://API.example.com/q#frag"},
        )
    )
    assert result["url"] == "https://api.example.com/q"
    assert result["verified_wallet"] == ""
    assert result["probe"]["latency_ms"] == 77
    assert result["probe"]["served_valid_402"] is True
    assert result["probe"]["payto_seen"] == "AGENT1"


@pytest.mark.usefixtures("fake_redis")
def test_probe_status_is_null_before_the_first_probe_and_404_for_unlisted_urls(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listed-but-unprobed URL answers probe=null; an unlisted URL is a 404, a bad URL a 400."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    _listed(store, "https://api.example.com/q")

    def _status(url: str) -> Response | dict:
        return directory_routes.x402_probe_status(
            _request(method="GET", path="/api/v1/x402/directory/probe", query={"url": url})
        )

    assert _status("https://api.example.com/q")["probe"] is None
    assert _status("https://nobody.example.com/q").status_code == 404
    assert _status("ftp://api.example.com/q").status_code == 400
    assert _status("").status_code == 400


@pytest.mark.usefixtures("fake_redis")
def test_probe_status_is_rate_limited_per_ip(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe route shares the search route's per-IP hourly budget."""
    monkeypatch.setattr(settings, "x402_search_rate_limit_per_hour", 1)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    _listed(store, "https://api.example.com/q")

    def _status(ip: str) -> Response | dict:
        return directory_routes.x402_probe_status(
            _request(
                method="GET",
                headers={"X-Real-IP": ip},
                path="/api/v1/x402/directory/probe",
                query={"url": "https://api.example.com/q"},
            )
        )

    assert "probe" in _status("203.0.113.7")
    assert _status("203.0.113.7").status_code == 429
    assert "probe" in _status("203.0.113.9")


@pytest.mark.usefixtures("fake_redis")
def test_probe_history_serves_past_probes_newest_first(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /x402/directory/probe/history?url= serves every past probe row for that listing, newest first (free, no gate)."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    listing = _listed(store, "https://api.example.com/q")
    store.record_probe(_probe(listing.url_hash, probed_at_epoch=1_700_000_000, latency_ms=100))
    store.record_probe(_probe(listing.url_hash, probed_at_epoch=1_700_001_000, latency_ms=90))
    store.record_probe(_probe(listing.url_hash, probed_at_epoch=1_700_002_000, latency_ms=80))

    result = directory_routes.x402_probe_history(
        _request(
            method="GET",
            path="/api/v1/x402/directory/probe/history",
            query={"url": "HTTPS://API.example.com/q#frag"},
        )
    )

    assert result["url"] == "https://api.example.com/q"
    assert [row["latency_ms"] for row in result["history"]] == [80, 90, 100]


@pytest.mark.usefixtures("fake_redis")
def test_probe_history_is_empty_before_the_first_probe_and_404_for_unlisted_urls(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A listed-but-unprobed URL answers history=[]; an unlisted URL is a 404, a bad URL a 400."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    _listed(store, "https://api.example.com/q")

    def _history(url: str) -> Response | dict:
        return directory_routes.x402_probe_history(
            _request(method="GET", path="/api/v1/x402/directory/probe/history", query={"url": url})
        )

    assert _history("https://api.example.com/q")["history"] == []
    assert _history("https://nobody.example.com/q").status_code == 404
    assert _history("ftp://api.example.com/q").status_code == 400
    assert _history("").status_code == 400


@pytest.mark.usefixtures("fake_redis")
def test_probe_history_limit_is_clamped_to_the_configured_maximum(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller cannot ask for an unbounded history read -- the limit is clamped server-side."""
    monkeypatch.setattr(settings, "x402_probe_history_max_results", 2)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    listing = _listed(store, "https://api.example.com/q")
    for i in range(5):
        store.record_probe(_probe(listing.url_hash, probed_at_epoch=1_700_000_000 + i))

    result = directory_routes.x402_probe_history(
        _request(
            method="GET",
            path="/api/v1/x402/directory/probe/history",
            query={"url": "https://api.example.com/q", "limit": "9999"},
        )
    )

    assert len(result["history"]) == 2


@pytest.mark.usefixtures("fake_redis")
def test_probe_history_is_rate_limited_per_ip(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe-history route shares the search route's per-IP hourly budget."""
    monkeypatch.setattr(settings, "x402_search_rate_limit_per_hour", 1)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    _listed(store, "https://api.example.com/q")

    def _history(ip: str) -> Response | dict:
        return directory_routes.x402_probe_history(
            _request(
                method="GET",
                headers={"X-Real-IP": ip},
                path="/api/v1/x402/directory/probe/history",
                query={"url": "https://api.example.com/q"},
            )
        )

    assert "history" in _history("203.0.113.7")
    assert _history("203.0.113.7").status_code == 429
    assert "history" in _history("203.0.113.9")


# --------------------------------------------------------------------------- #
# Probe-MEASURED reliability leaderboard (roadmap item 7's trust-layer step 3)
# --------------------------------------------------------------------------- #
def _seed_probes(
    store: InMemoryListingStore,
    url_hash_value: str,
    *,
    count: int,
    healthy_count: int,
    latency_ms: int = 100,
    start_epoch: int = 1_700_000_000,
) -> None:
    """Record `count` probes, oldest call first, the first `healthy_count` of them healthy.

    record_probe() prepends, so calling with increasing probed_at_epoch (the
    loop below) leaves the store's own newest-first order matching real
    probe-beat cadence: the LAST call made here is the newest and therefore
    history[0].
    """
    for i in range(count):
        healthy = i < healthy_count
        store.record_probe(
            _probe(
                url_hash_value,
                probed_at_epoch=start_epoch + i,
                reachable=healthy,
                served_valid_402=healthy,
                latency_ms=latency_ms,
            )
        )


def test_probe_leaderboard_ranks_by_uptime_then_latency_then_recency(
    store: InMemoryListingStore,
) -> None:
    """Primary sort is uptime_pct desc, tiebreak avg_latency_ms asc, final tiebreak last_probed_at_epoch desc."""
    service = ListingService(store)
    lower_uptime = service.create(
        normalized_url="https://lower-uptime.example.com/x",
        price="$0.01",
        description="lower uptime",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    slower = service.create(
        normalized_url="https://slower.example.com/x",
        price="$0.01",
        description="full uptime, slower",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX2",
        payer="AGENT2",
    )
    faster = service.create(
        normalized_url="https://faster.example.com/x",
        price="$0.01",
        description="full uptime, faster",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX3",
        payer="AGENT3",
    )
    _seed_probes(store, lower_uptime.url_hash, count=48, healthy_count=40, latency_ms=1)
    _seed_probes(store, slower.url_hash, count=48, healthy_count=48, latency_ms=200)
    _seed_probes(store, faster.url_hash, count=48, healthy_count=48, latency_ms=50)

    entries, scanned = service.probe_leaderboard(limit=10)

    assert scanned == 3
    assert [e.url for e in entries] == [
        "https://faster.example.com/x",
        "https://slower.example.com/x",
        "https://lower-uptime.example.com/x",
    ]
    assert entries[0].avg_latency_ms == 50
    assert entries[1].avg_latency_ms == 200
    assert round(entries[2].uptime_pct, 2) == round(40 / 48 * 100, 2)


def test_probe_leaderboard_final_tiebreak_is_most_recently_probed(
    store: InMemoryListingStore,
) -> None:
    """Two candidates tied on uptime and latency are ordered by the more recently reprobed one first."""
    service = ListingService(store)
    stale = service.create(
        normalized_url="https://stale.example.com/x",
        price="$0.01",
        description="stale",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    fresh = service.create(
        normalized_url="https://fresh.example.com/x",
        price="$0.01",
        description="fresh",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX2",
        payer="AGENT2",
    )
    _seed_probes(store, stale.url_hash, count=48, healthy_count=48, start_epoch=1_700_000_000)
    _seed_probes(store, fresh.url_hash, count=48, healthy_count=48, start_epoch=1_800_000_000)

    entries, _ = service.probe_leaderboard(limit=10)

    assert [e.url for e in entries] == [
        "https://fresh.example.com/x",
        "https://stale.example.com/x",
    ]


def test_probe_leaderboard_excludes_candidates_below_the_minimum_sample_threshold(
    store: InMemoryListingStore,
) -> None:
    """A listing with fewer than PROBE_LEADERBOARD_MIN_SAMPLES probes is skipped entirely, not scored as 0% or 100%.

    Regression-shaped: a listing probed once, luckily healthy, must not
    outrank one with real history -- the concrete abuse this threshold
    exists to close.
    """
    service = ListingService(store)
    too_new = service.create(
        normalized_url="https://too-new.example.com/x",
        price="$0.01",
        description="one lucky probe",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    established = service.create(
        normalized_url="https://established.example.com/x",
        price="$0.01",
        description="real history",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX2",
        payer="AGENT2",
    )
    _seed_probes(
        store,
        too_new.url_hash,
        count=PROBE_LEADERBOARD_MIN_SAMPLES - 1,
        healthy_count=PROBE_LEADERBOARD_MIN_SAMPLES - 1,
    )
    _seed_probes(store, established.url_hash, count=PROBE_LEADERBOARD_MIN_SAMPLES, healthy_count=40)

    entries, scanned = service.probe_leaderboard(limit=10)

    assert scanned == 2
    assert [e.url for e in entries] == ["https://established.example.com/x"]


def test_probe_leaderboard_excludes_expired_listings(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An expired listing is not a ranking candidate at all, mirroring search()'s own filter."""
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    service = ListingService(store)
    now = datetime.now(tz=UTC)
    expired = service.create(
        normalized_url="https://expired.example.com/x",
        price="$0.01",
        description="term ended",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        now=now - timedelta(days=31),
    )
    _seed_probes(
        store,
        expired.url_hash,
        count=PROBE_LEADERBOARD_MIN_SAMPLES,
        healthy_count=PROBE_LEADERBOARD_MIN_SAMPLES,
    )

    entries, scanned = service.probe_leaderboard(limit=10, now=now)

    assert scanned == 0
    assert entries == []


def test_probe_leaderboard_healthy_requires_both_reachable_and_valid_402(
    store: InMemoryListingStore,
) -> None:
    """A probe that is reachable but never serves a valid 402 does not count toward uptime or latency."""
    service = ListingService(store)
    listing = service.create(
        normalized_url="https://reachable-but-invalid.example.com/x",
        price="$0.01",
        description="responds, but never a valid 402",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    for i in range(PROBE_LEADERBOARD_MIN_SAMPLES):
        store.record_probe(
            _probe(
                listing.url_hash,
                probed_at_epoch=1_700_000_000 + i,
                reachable=True,
                served_valid_402=False,
                latency_ms=10,
            )
        )

    entries, _ = service.probe_leaderboard(limit=10)

    assert entries[0].uptime_pct == 0.0
    assert entries[0].avg_latency_ms is None


def test_probe_leaderboard_is_empty_for_an_empty_directory(store: InMemoryListingStore) -> None:
    """Zero listings is a valid, honestly-reported empty leaderboard, not an error."""
    entries, scanned = ListingService(store).probe_leaderboard(limit=10)

    assert entries == []
    assert scanned == 0


def test_probe_leaderboard_caps_to_the_requested_limit(store: InMemoryListingStore) -> None:
    """More qualifying candidates than `limit` still only returns `limit` rows -- CLAUDE.md section 4."""
    service = ListingService(store)
    for i in range(5):
        listing = service.create(
            normalized_url=f"https://api{i}.example.com/x",
            price="$0.01",
            description=f"endpoint {i}",
            assets=[],
            tags=[],
            schema_json="",
            settlement_tx_id=f"TX{i}",
            payer=f"AGENT{i}",
        )
        _seed_probes(
            store,
            listing.url_hash,
            count=PROBE_LEADERBOARD_MIN_SAMPLES,
            healthy_count=PROBE_LEADERBOARD_MIN_SAMPLES,
        )

    entries, scanned = service.probe_leaderboard(limit=2)

    assert scanned == 5
    assert len(entries) == 2


# --------------------------------------------------------------------------- #
# Probe leaderboard route: payment gate, preview, refund, discovery
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
def test_probe_leaderboard_limit_must_be_an_integer_before_the_gate(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-integer `limit` is a 400 before the payment gate -- nobody is charged for a malformed request."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(directory_routes, "require_paid_request", _never_gate_factory([]))

    response = directory_routes.x402_probe_leaderboard(
        _request(
            method="GET",
            path="/api/v1/x402/directory/probe/leaderboard",
            query={"limit": "not-a-number"},
        )
    )

    assert response.status_code == 400
    assert "invalid_request" in response.description


@pytest.mark.usefixtures("fake_redis")
def test_probe_leaderboard_is_refused_before_the_gate_once_its_circuit_breaker_is_tripped(
    monkeypatch: pytest.MonkeyPatch, fake_redis: _FakeRedis
) -> None:
    """Once the resource's breaker is tripped, the route refuses BEFORE require_paid_request."""
    fake_redis.store[
        f"algorand:x402:refund_breaker:{directory_routes._PROBE_LEADERBOARD_RESOURCE}"
    ] = str(settings.x402_refund_breaker_max_failures)
    monkeypatch.setattr(directory_routes, "require_paid_request", _never_gate_factory([]))

    response = directory_routes.x402_probe_leaderboard(
        _request(method="GET", path="/api/v1/x402/directory/probe/leaderboard")
    )

    assert response.status_code == 503
    assert json.loads(response.description)["error"]["code"] == "temporarily_disabled"


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_probe_leaderboard_preview_is_redacted_unpaid_and_never_ranks_real_candidates(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """?preview=true serves a redacted exemplar row with no facilitator call and never calls probe_leaderboard()."""
    service = ListingService(store)
    monkeypatch.setattr(directory_routes, "listing_service", service)
    called = False

    def _must_not_rank(**_kw: object) -> Never:
        nonlocal called
        called = True
        raise AssertionError("probe_leaderboard must not run on a preview request")

    monkeypatch.setattr(service, "probe_leaderboard", _must_not_rank)

    response = directory_routes.x402_probe_leaderboard(
        _request(
            method="GET", path="/api/v1/x402/directory/probe/leaderboard", query={"preview": "true"}
        )
    )

    assert response.status_code == 200
    payload = json.loads(response.description)
    assert payload["candidates_scanned"] == -1
    assert payload["items"][0]["url"] == "<preview>"
    assert payload["settlement_tx_id"] == "<preview>"
    assert called is False


@pytest.mark.usefixtures("fake_redis")
def test_probe_leaderboard_paid_response_serves_the_measured_ranking_and_marks_fulfilled(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settled payment returns the real ranking, the measurement-only framing, and is marked fulfilled."""
    service = ListingService(store)
    listing = service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="probe me",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    _seed_probes(
        store,
        listing.url_hash,
        count=PROBE_LEADERBOARD_MIN_SAMPLES,
        healthy_count=PROBE_LEADERBOARD_MIN_SAMPLES,
    )
    monkeypatch.setattr(directory_routes, "listing_service", service)
    monkeypatch.setattr(
        directory_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        directory_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    response = directory_routes.x402_probe_leaderboard(
        _request(method="GET", path="/api/v1/x402/directory/probe/leaderboard")
    )

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["basis"] == "measured"
    assert body["candidates_scanned"] == 1
    assert body["items"][0]["url"] == "https://api.example.com/v1/quote"
    assert body["items"][0]["rank"] == 1
    assert body["settlement_tx_id"] == "TX123"
    assert fulfilled == [("TX123", directory_routes._PROBE_LEADERBOARD_RESOURCE)]


@pytest.mark.usefixtures("fake_redis")
def test_probe_leaderboard_paid_response_is_a_valid_200_for_an_empty_directory(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero listings (or none with enough probe history) is an honest, chargeable empty leaderboard -- never a 404 or an error envelope."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(
        directory_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    response = directory_routes.x402_probe_leaderboard(
        _request(method="GET", path="/api/v1/x402/directory/probe/leaderboard")
    )

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["items"] == []
    assert body["candidates_scanned"] == 0
    assert "error" not in body


@pytest.mark.usefixtures("fake_redis")
def test_probe_leaderboard_write_failure_after_payment_triggers_a_refund_not_a_500(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A genuine unexpected failure building the leaderboard after payment settled is refunded, never a bare 500."""
    service = ListingService(store)
    monkeypatch.setattr(directory_routes, "listing_service", service)
    monkeypatch.setattr(
        directory_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )
    monkeypatch.setattr(
        service,
        "probe_leaderboard",
        lambda **_kw: (_ for _ in ()).throw(RuntimeError("ranking blew up")),
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        directory_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    response = directory_routes.x402_probe_leaderboard(
        _request(method="GET", path="/api/v1/x402/directory/probe/leaderboard")
    )

    assert response.status_code == 503
    assert json.loads(response.description)["error"]["code"] == "product_failed_refund_pending"
    assert fulfilled == []


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_probe_leaderboard_402_declares_bazaar_discovery_and_the_measurement_framing(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 402 offer's own description states the measurement-not-opinion framing and declares query-params discovery."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_directory_probe_leaderboard_price", "$0.02")
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))

    response = directory_routes.x402_probe_leaderboard(
        _request(method="GET", path="/api/v1/x402/directory/probe/leaderboard")
    )

    assert response.status_code == 402
    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    assert payment_required.accepts[0].amount == "20000"
    assert "MEASURED" in (payment_required.resource.description or "")
    assert "never" in (payment_required.resource.description or "").lower()
    bazaar = (payment_required.extensions or {}).get("bazaar")
    assert bazaar is not None
    # GET takes its input in the query string, not a JSON body -- the
    # package's own query-params shape carries "queryParams", not "body"
    # (checked structurally, not by scanning the whole JSON for the
    # substring "body": the leaderboard's own framing text legitimately
    # says "nobody", which would give a plain substring check a false
    # positive).
    assert "queryParams" in bazaar["info"]["input"]
    assert "body" not in bazaar["info"]["input"]


def test_cassandra_store_reads_badge_and_latest_probe_columns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Cassandra row mappers pick up verified_wallet/verified_at and the x402_probe_latest columns."""
    from app.modules.x402_directory.stores import cassandra as cassandra_store

    now = datetime(2026, 8, 30, tzinfo=UTC)
    listing_row = SimpleNamespace(
        url_hash="h",
        url="https://api.example.com/q",
        price="$0.01",
        assets=set(),
        description="",
        schema_json="",
        tags=set(),
        term_end=now,
        settlement_tx_id="TX",
        created_at=now,
        payer="AGENT1",
        verified_wallet="AGENT1",
        verified_at=now,
    )
    probe_row = SimpleNamespace(
        url_hash="h",
        url="https://api.example.com/q",
        probed_at=now,
        reachable=True,
        http_status=402,
        latency_ms=9,
        served_valid_402=True,
        payto_seen="AGENT1",
        error=None,
    )
    executed: list[tuple[str, tuple]] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> SimpleNamespace:
            executed.append((stmt, params))
            return SimpleNamespace(one=lambda: probe_row)

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(cassandra_store, "get_cassandra_session", lambda: _Session())

    listing = cassandra_store._row_to_listing(listing_row)
    assert listing.is_verified
    assert listing.verified_at_epoch == int(now.timestamp())

    probe = cassandra_store.CassandraListingStore().latest_probe("h")
    assert probe is not None
    assert (probe.http_status, probe.latency_ms, probe.error) == (402, 9, "")
    assert "x402_probe_latest" in executed[0][0]
    assert executed[0][1] == ("h",)


def test_cassandra_store_probe_history_reads_x402_probe_results_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """probe_history binds (url_hash, limit) against x402_probe_results.LIST_HISTORY, never an unbounded scan."""
    from app.modules.x402_directory.stores import cassandra as cassandra_store

    now = datetime(2026, 8, 30, tzinfo=UTC)
    probe_row = SimpleNamespace(
        url_hash="h",
        url="https://api.example.com/q",
        probed_at=now,
        reachable=True,
        http_status=402,
        latency_ms=9,
        served_valid_402=True,
        payto_seen="AGENT1",
        error=None,
    )
    executed: list[tuple[str, tuple]] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> list[SimpleNamespace]:
            executed.append((stmt, params))
            return [probe_row]

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(cassandra_store, "get_cassandra_session", lambda: _Session())

    history = cassandra_store.CassandraListingStore().probe_history("h", limit=50)

    assert len(history) == 1
    assert history[0].latency_ms == 9
    assert "x402_probe_results" in executed[0][0]
    assert executed[0][1] == ("h", 50)


# --------------------------------------------------------------------------- #
# Category (migration 099)
# --------------------------------------------------------------------------- #
def _never_gate_factory(calls: list[str]) -> Any:  # noqa: ANN401 -- returns a gate stand-in
    def _never_gate(*_args: object, **_kwargs: object) -> Never:
        calls.append("gate")
        raise AssertionError("the payment gate must not run for a doomed request")

    return _never_gate


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_an_unknown_category_is_rejected_before_the_payment_gate(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A category outside the fixed enum is a 400 nobody pays for -- the gate never runs."""
    gate_calls: list[str] = []
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(directory_routes, "require_paid_request", _never_gate_factory(gate_calls))

    response = directory_routes.x402_list(
        _request(
            body=json.dumps(
                {"url": "https://api.example.com/v1/quote", "price": "$0.01", "category": "memes"}
            ).encode()
        )
    )

    assert response.status_code == 400
    assert "category must be one of" in response.description
    assert gate_calls == []
    assert store.list_recent(limit=50) == []


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_a_reserved_category_tag_is_rejected_before_the_payment_gate(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A user tag in the `category:` namespace is a 400 before the gate -- a listing cannot forge its way into a category partition."""
    gate_calls: list[str] = []
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(directory_routes, "require_paid_request", _never_gate_factory(gate_calls))

    response = directory_routes.x402_list(
        _request(
            body=json.dumps(
                {
                    "url": "https://api.example.com/v1/quote",
                    "price": "$0.01",
                    "tags": ["fx", " Category:AI "],
                }
            ).encode()
        )
    )

    assert response.status_code == 400
    assert "reserved" in response.description
    assert gate_calls == []
    assert store.tag_rows("category:ai") == []


def test_create_rejects_a_reserved_tag_as_the_durable_guard(store: InMemoryListingStore) -> None:
    """create() itself refuses the reserved namespace, so no caller can bypass the route's pre-gate check."""
    with pytest.raises(DirectoryError, match="reserved"):
        ListingService(store).create(
            normalized_url="https://api.example.com/v1/quote",
            price="$0.01",
            description="",
            assets=[],
            tags=["category:finance"],
            schema_json="",
            settlement_tx_id="TX1",
            payer="AGENT1",
        )
    assert store.get(url_hash("https://api.example.com/v1/quote")) is None


@pytest.mark.usefixtures("fake_redis")
def test_a_listed_category_is_stored_served_and_defaults_to_other(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The paid route persists a normalized category and serves it back; omitting it means `other`."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(
        directory_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    with_category = directory_routes.x402_list(
        _request(
            body=json.dumps(
                {"url": "https://a.example.com/x", "price": "$0.01", "category": " Finance "}
            ).encode()
        )
    )
    assert json.loads(with_category.description)["listing"]["category"] == "finance"
    assert store.tag_rows("category:finance") == [url_hash("https://a.example.com/x")]
    # The category is not one of the listing's tags on the wire.
    assert json.loads(with_category.description)["listing"]["tags"] == []

    without = directory_routes.x402_list(
        _request(body=json.dumps({"url": "https://b.example.com/x", "price": "$0.01"}).encode())
    )
    assert json.loads(without.description)["listing"]["category"] == "other"
    assert store.tag_rows("category:other") == [url_hash("https://b.example.com/x")]


def _seed_categorized(service: ListingService, *, now: datetime) -> None:
    for index, (host, category, days_ago) in enumerate(
        [
            ("fin1", "finance", 2),
            ("ai1", "ai", 1),
            ("fin2", "finance", 0),
            ("fin-old", "finance", 31),
        ]
    ):
        service.create(
            normalized_url=f"https://{host}.example.com/x",
            price="$0.01",
            description=host,
            assets=[],
            tags=["fx"],
            schema_json="",
            settlement_tx_id=f"TX{index}",
            payer=f"AGENT{index}",
            category=category,
            now=now - timedelta(days=days_ago),
        )


@pytest.mark.usefixtures("fake_redis")
def test_search_by_category_returns_only_live_listings_in_that_category(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`?category=` narrows to listings declared in that category, newest first, expired dropped; bad or combined filters are 400s."""
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    _seed_categorized(ListingService(store), now=datetime.now(tz=UTC))

    def _search(query: dict[str, str]) -> Response | dict:
        return directory_routes.x402_search(
            _request(method="GET", path="/api/v1/x402/search", query=query)
        )

    assert [i["description"] for i in _search({"category": "Finance"})["items"]] == [
        "fin2",
        "fin1",
    ]
    assert [i["description"] for i in _search({"category": "ai"})["items"]] == ["ai1"]
    assert _search({"category": "storage"})["items"] == []
    assert _search({"category": "memes"}).status_code == 400
    assert _search({"category": "ai", "tag": "fx"}).status_code == 400
    # The reserved namespace is not reachable through ?tag= either.
    assert _search({"tag": "category:ai"}).status_code == 400
    # Unfiltered search is unchanged and still carries the category.
    assert [i["category"] for i in _search({})["items"]] == ["finance", "ai", "finance"]


def test_relisting_with_a_different_category_moves_the_category_row(
    store: InMemoryListingStore,
) -> None:
    """A relist that changes the category drops the old category row exactly like a dropped tag."""
    service = ListingService(store)
    key = url_hash("https://api.example.com/v1/quote")
    for category in ("data", "ai"):
        service.create(
            normalized_url="https://api.example.com/v1/quote",
            price="$0.01",
            description=category,
            assets=[],
            tags=["fx"],
            schema_json="",
            settlement_tx_id="TX",
            payer="AGENT1",
            category=category,
        )

    assert store.tag_rows("category:data") == []
    assert store.tag_rows("category:ai") == [key]
    assert [i.description for i in service.search(limit=50, category="ai")] == ["ai"]


def test_admin_delist_removes_the_category_row(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleting a listing also removes its reserved category row, so a category search cannot resurrect it."""
    service = ListingService(store)
    key = url_hash("https://api.example.com/v1/quote")
    service.create(
        normalized_url="https://api.example.com/v1/quote",
        price="$0.01",
        description="categorized",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        category="identity",
    )
    assert store.tag_rows("category:identity") == [key]
    monkeypatch.setattr(directory_routes, "listing_service", service)
    monkeypatch.setattr(directory_routes, "require_admin_wallet", lambda _request: None)

    response = directory_routes.x402_admin_delete_listing(
        _delete_request("https://api.example.com/v1/quote")
    )

    assert getattr(response, "status_code", 200) == 200
    assert store.tag_rows("category:identity") == []
    assert store.tag_rows("fx") == []
    assert service.search(limit=50, category="identity") == []


@pytest.mark.usefixtures("fake_redis")
def test_the_listing_offer_advertises_category_and_the_category_filter(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 402 offer's description and discovery input_schema both tell a payer about `category` and `?category=`."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    captured: dict[str, Any] = {}

    def _capture(_request: Request, **kwargs: Any) -> Never:  # noqa: ANN401 -- mirrors the gate's kwargs
        captured.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(directory_routes, "require_paid_request", _capture)
    with pytest.raises(RuntimeError, match="stop"):
        directory_routes.x402_list(
            _request(
                body=json.dumps({"url": "https://api.example.com/v1/quote", "price": "$1"}).encode()
            )
        )

    assert "?category=" in captured["description"]
    body_schema = captured["extensions"]["bazaar"]["schema"]["properties"]["input"]["properties"][
        "body"
    ]
    assert body_schema["properties"]["category"]["enum"] == list(LISTING_CATEGORIES)
    assert "?category=" in body_schema["properties"]["category"]["description"]
    assert (
        captured["extensions"]["bazaar"]["schema"]["properties"]["input"]["properties"]["body"][
            "properties"
        ]["tags"]["description"].count("category:")
        == 1
    )


def test_cassandra_store_projects_the_category_row_and_reads_a_null_category_as_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every Cassandra write carries the category and one extra by-tag row for it; a pre-099 row reads as `other`; delete removes the category row."""
    from app.modules.x402_directory.stores import cassandra as cassandra_store

    now = datetime(2026, 8, 30, tzinfo=UTC)
    executed: list[tuple[str, tuple]] = []
    stored_row = SimpleNamespace(
        url_hash="h",
        url="https://api.example.com/q",
        price="$0.01",
        assets=set(),
        description="",
        schema_json="",
        tags={"fx"},
        term_end=now,
        settlement_tx_id="TX",
        created_at=now,
        payer="AGENT1",
        verified_wallet=None,
        verified_at=None,
        category=None,
    )

    class _Session:
        def execute(self, stmt: str, params: tuple) -> SimpleNamespace:
            executed.append((stmt, params))
            return SimpleNamespace(one=lambda: stored_row, was_applied=True)

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(cassandra_store, "get_cassandra_session", lambda: _Session())
    cass = cassandra_store.CassandraListingStore()

    listing = StoredListing(
        url_hash="h",
        url="https://api.example.com/q",
        price="$0.01",
        description="",
        schema_json="",
        settlement_tx_id="TX",
        term_end_epoch=int(now.timestamp()),
        created_at_epoch=int(now.timestamp()),
        tags=["fx"],
        payer="AGENT1",
        category="ai",
    )
    assert cass.insert_if_absent(listing) is True
    by_tag = [p for s, p in executed if "INSERT INTO algorand_platform.x402_listings_by_tag" in s]
    assert [p[0] for p in by_tag] == ["fx", "category:ai"]
    assert all(p[12] == "ai" for p in by_tag)
    canonical = next(p for s, p in executed if "IF NOT EXISTS" in s)
    assert canonical[11] == "ai"

    # A pre-099 row: null category reads back as the default.
    assert cass.get("h").category == "other"

    executed.clear()
    assert cass.delete("h") is True
    deleted_tags = [
        p[0] for s, p in executed if "DELETE FROM algorand_platform.x402_listings_by_tag" in s
    ]
    assert deleted_tags == ["fx", "category:other"]


# --------------------------------------------------------------------------- #
# Renew (POST /x402/list/renew)
# --------------------------------------------------------------------------- #
def _renew_request(url: str) -> Request:
    return _request(body=json.dumps({"url": url}).encode(), path="/api/v1/x402/list/renew")


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_renewing_an_unlisted_url_is_a_free_404(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unlisted url (and a malformed one) is refused before the gate -- nobody pays to renew nothing."""
    gate_calls: list[str] = []
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(directory_routes, "require_paid_request", _never_gate_factory(gate_calls))

    assert (
        directory_routes.x402_renew(_renew_request("https://nobody.example.com/x")).status_code
        == 404
    )
    assert (
        directory_routes.x402_renew(_renew_request("ftp://nobody.example.com/x")).status_code == 400
    )
    assert directory_routes.x402_renew(_request(body=b"{not json")).status_code == 400
    assert gate_calls == []


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_renew_402_declares_a_json_body_discovery_extension_and_states_the_rule(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boost offer reaches the gate with a body-shaped Bazaar declaration, the boost price and the ownership rule in its description."""
    monkeypatch.setattr(settings, "x402_listing_boost_price", "$0.10")
    monkeypatch.setattr(settings, "x402_listing_boost_days", 30)
    service = ListingService(store)
    _listed(store, "https://api.example.com/q")
    monkeypatch.setattr(directory_routes, "listing_service", service)
    captured: dict[str, Any] = {}

    def _capture(_request: Request, **kwargs: Any) -> Never:  # noqa: ANN401 -- mirrors the gate's kwargs
        captured.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(directory_routes, "require_paid_request", _capture)
    with pytest.raises(RuntimeError, match="stop"):
        directory_routes.x402_renew(_renew_request("https://api.example.com/q"))

    assert captured["price"] == "$0.10"
    assert captured["resource"] == "x402-directory-boost"
    assert "for 30 days" in captured["description"]
    assert "Only the wallet that listed" in captured["description"]
    assert "body" in json.dumps(captured["extensions"]["bazaar"])


def test_renew_by_the_owner_boosts_from_now_and_never_touches_term_end_or_created_at(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An early boost adds a full boost window on top of what is left; term_end, created_at, content and the badge are untouched, the txid is replaced.

    Regression coverage for the 2026-09-06 repurposing: renew() used to
    extend term_end_epoch (survival, now probe-fed); it now stacks onto
    boosted_until_epoch (search-ranking priority) and must never move
    term_end_epoch, which is unaffected by any payment since this change.
    """
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    monkeypatch.setattr(settings, "x402_listing_boost_days", 7)
    service = ListingService(store)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    original = service.create(
        normalized_url="https://api.example.com/q",
        price="$0.01",
        description="mine",
        assets=["USDC"],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        category="finance",
        now=base,
    )
    store.upsert(replace(original, verified_wallet="AGENT1", verified_at_epoch=7))

    renew_at = base + timedelta(days=10)
    renewed = service.renew(
        normalized_url="https://api.example.com/q",
        payer="AGENT1",
        settlement_tx_id="TX2",
        now=renew_at,
    )

    assert renewed.boosted_until_epoch == int((renew_at + timedelta(days=7)).timestamp())
    assert renewed.term_end_epoch == original.term_end_epoch
    assert renewed.created_at_epoch == original.created_at_epoch
    assert renewed.settlement_tx_id == "TX2"
    assert renewed.payer == "AGENT1"
    assert renewed.is_verified
    assert renewed.verified_at_epoch == 7
    assert (renewed.description, renewed.tags, renewed.category) == ("mine", ["fx"], "finance")
    assert store.get(original.url_hash) == renewed
    # Projections carry the new boost: the tag and category rows serve it
    # live for the rest of the (unaffected) original term.
    late = base + timedelta(days=20)
    assert [i.settlement_tx_id for i in service.search(limit=50, tag="fx", now=late)] == ["TX2"]
    assert [i.settlement_tx_id for i in service.search(limit=50, category="finance", now=late)] == [
        "TX2"
    ]
    assert service.search(limit=50, now=late)[0].boosted_until_epoch == renewed.boosted_until_epoch


def test_a_boosted_listing_sorts_before_a_newer_non_boosted_one_in_search(
    store: InMemoryListingStore,
) -> None:
    """search()'s paid tier is boosted-first, newest-first within each group -- a boost can override recency but never mixes with the auto-discovered tier or probe_leaderboard()."""
    service = ListingService(store)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    older = service.create(
        normalized_url="https://api.example.com/older",
        price="$0.01",
        description="older",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX-OLD",
        payer="AGENT1",
        now=base,
    )
    newer = service.create(
        normalized_url="https://api.example.com/newer",
        price="$0.01",
        description="newer",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX-NEW",
        payer="AGENT2",
        now=base + timedelta(days=1),
    )
    # Newer is unboosted and would otherwise sort first (newest-first).
    boosted_older = service.renew(
        normalized_url=older.url,
        payer="AGENT1",
        settlement_tx_id="TX-BOOST",
        now=base + timedelta(days=2),
    )
    assert boosted_older.boosted_until_epoch > 0

    now = base + timedelta(days=3)
    assert [i.url for i in service.search(limit=10, now=now)] == [older.url, newer.url]
    assert [i.url for i in service.search(limit=10, tag="fx", now=now)] == [older.url, newer.url]


def test_renew_of_a_live_listing_by_another_wallet_is_refused_and_changes_nothing(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different (or unattributable) payer cannot renew -- and thereby claim -- a live listing."""
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    service = ListingService(store)
    now = datetime.now(tz=UTC)
    original = _listed(store, "https://api.example.com/q")

    for payer in ("AGENT2", ""):
        with pytest.raises(DirectoryError, match="Only the wallet that listed") as excinfo:
            service.renew(
                normalized_url="https://api.example.com/q",
                payer=payer,
                settlement_tx_id="TX-HIJACK",
                now=now,
            )
        assert excinfo.value.http_status == 403
    assert store.get(original.url_hash) == original


def test_renew_of_an_expired_or_unowned_listing_by_another_wallet_is_refused_with_409(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After expiry (or with no payer) a different wallet may NOT renew: 409 renew_requires_relist, storage untouched (price/description/badge stay the previous owner's).

    Regression: renew used to let any wallet renew -- and thereby claim -- an
    expired listing, keeping the previous payer's price, description, schema
    and tags under the new wallet's name.
    """
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    service = ListingService(store)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    original = service.create(
        normalized_url="https://api.example.com/q",
        price="$0.01",
        description="lapsed",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        now=base,
    )
    badged = replace(original, verified_wallet="AGENT1", verified_at_epoch=7)
    store.upsert(badged)

    later = base + timedelta(days=45)
    with pytest.raises(DirectoryError, match="POST /api/v1/x402/list") as excinfo:
        service.renew(
            normalized_url="https://api.example.com/q",
            payer="AGENT2",
            settlement_tx_id="TX2",
            now=later,
        )
    assert excinfo.value.code == "renew_requires_relist"
    assert excinfo.value.http_status == 409
    assert store.get(original.url_hash) == badged

    unowned = replace(badged, payer="", verified_wallet="", verified_at_epoch=0)
    store.upsert(unowned)
    with pytest.raises(DirectoryError) as excinfo:
        service.renew(
            normalized_url="https://api.example.com/q",
            payer="AGENT3",
            settlement_tx_id="TX3",
            now=later,
        )
    assert excinfo.value.code == "renew_requires_relist"
    assert store.get(original.url_hash) == unowned

    # The owner itself can still boost after expiry: fresh boost window from
    # now, term_end untouched (still the original, already-lapsed value --
    # boosting an expired listing does not resurrect it), badge kept.
    store.upsert(badged)
    renewed = service.renew(
        normalized_url="https://api.example.com/q",
        payer="AGENT1",
        settlement_tx_id="TX4",
        now=later,
    )
    assert renewed.boosted_until_epoch == int(
        (later + timedelta(days=settings.x402_listing_boost_days)).timestamp()
    )
    assert renewed.term_end_epoch == original.term_end_epoch
    assert renewed.created_at_epoch == original.created_at_epoch
    assert renewed.is_verified
    assert renewed.settlement_tx_id == "TX4"


def test_renew_by_an_unattributable_payer_cannot_boost_an_unowned_listing(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty (unattributable) payer must not match an unowned listing's own empty payer -- two blanks are not proof of ownership (finding #11, 2026-09-06 adversarial review).

    Regression: `existing.payer != payer` was False whenever BOTH sides were
    "", so a settled payment that could not be attributed to any wallet was
    treated as if it belonged to the SAME owner as an already-unowned
    listing (an auto-discovered stub, or one whose payer was never set) and
    was allowed straight into the boost path. It must instead hit the same
    renew_requires_relist refusal any other non-owner gets, exactly like
    x402_board's BoardService.renew() (`not attributed or attributed !=
    placement.payer`) already enforces for the equivalent case.
    """
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    service = ListingService(store)
    base = datetime(2026, 9, 6, tzinfo=UTC)
    unowned = StoredListing(
        url_hash=url_hash("https://api.example.com/unowned"),
        url="https://api.example.com/unowned",
        price="",
        description="an auto-discovered stub",
        schema_json="",
        settlement_tx_id="",
        term_end_epoch=int((base + timedelta(days=30)).timestamp()),
        created_at_epoch=int(base.timestamp()),
        payer="",
    )
    store.upsert(unowned)

    with pytest.raises(DirectoryError) as excinfo:
        service.renew(
            normalized_url="https://api.example.com/unowned",
            payer="",
            settlement_tx_id="TX-UNATTRIBUTED",
            now=base,
        )
    assert excinfo.value.code == "renew_requires_relist"
    assert excinfo.value.http_status == 409
    # Payment settled but nothing about the listing changed.
    assert store.get(unowned.url_hash) == unowned


@pytest.mark.usefixtures("fake_redis")
def test_renew_route_serves_409_with_receipt_headers_for_an_expired_listing_of_another_wallet(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real (non-promo) renew's ownership refusal goes through run_with_refund (2026-09-02 retrofit), but DirectoryError is a PlatformError -- run_with_refund's own PlatformError exemption (found-in-audit fix, 2026-09-02) means this is STILL the direct 403/409 with payment kept, NEVER a refund, and mark_fulfilled is still never called.

    See run_with_refund's own docstring for why: this rejection is fully
    caller-controlled (pay to renew a url you know you don't own), so
    refunding it would make it a free, repeatable way to pump the circuit
    breaker -- exactly the bug this exemption exists to close.
    """
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    service = ListingService(store)
    original = _listed(store, "https://api.example.com/q", payer="AGENT-OTHER")
    store.upsert(replace(original, term_end_epoch=int(datetime.now(tz=UTC).timestamp()) - 1))
    monkeypatch.setattr(directory_routes, "listing_service", service)
    monkeypatch.setattr(
        directory_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        directory_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    refused = directory_routes.x402_renew(_renew_request("https://api.example.com/q"))

    assert refused.status_code == 409
    assert json.loads(refused.description)["error"]["code"] == "renew_requires_relist"
    assert refused.headers["PAYMENT-RESPONSE"] == "ok"
    assert fulfilled == []
    assert store.get(original.url_hash).payer == "AGENT-OTHER"


# --------------------------------------------------------------------------- #
# Verified badge on relist (097): carried for the same owner, blanked on change
# --------------------------------------------------------------------------- #
def test_same_owner_relist_carries_the_badge_into_every_projection(
    store: InMemoryListingStore,
) -> None:
    """A relist by the wallet that owns the badge keeps it on the canonical row AND the recency/tag/category projections.

    Regression: the projection rows were rewritten without the badge columns,
    so /search said unverified while /listings?url= said verified until the
    next probe sweep.
    """
    service = ListingService(store)
    listing = _listed(store, "https://api.example.com/q")
    store.upsert(replace(listing, verified_wallet="AGENT1", verified_at_epoch=42))

    relisted = service.create(
        normalized_url="https://api.example.com/q",
        price="$0.02",
        description="new text",
        assets=[],
        tags=["fx", "new"],
        schema_json="",
        settlement_tx_id="TX2",
        payer="AGENT1",
        category="finance",
    )
    assert relisted.is_verified
    assert relisted.verified_at_epoch == 42
    assert store.get(listing.url_hash).verified_wallet == "AGENT1"
    for item in (
        service.search(limit=10)[0],
        service.search(limit=10, tag="new")[0],
        service.search(limit=10, category="finance")[0],
    ):
        assert (item.verified_wallet, item.verified_at_epoch) == ("AGENT1", 42)


def test_relist_by_a_new_owner_of_an_expired_listing_stores_an_empty_badge(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the payer changes on relist, the stored verified_wallet is "" everywhere -- not the previous owner's wallet masked at read time."""
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    service = ListingService(store)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    original = service.create(
        normalized_url="https://api.example.com/q",
        price="$0.01",
        description="lapsed",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        now=base,
    )
    store.upsert(replace(original, verified_wallet="AGENT1", verified_at_epoch=7))

    relisted = service.create(
        normalized_url="https://api.example.com/q",
        price="$0.05",
        description="mine now",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX2",
        payer="AGENT2",
        now=base + timedelta(days=45),
    )
    assert relisted.payer == "AGENT2"
    assert relisted.verified_wallet == ""
    assert relisted.verified_at_epoch == 0
    stored = store.get(original.url_hash)
    assert (stored.verified_wallet, stored.verified_at_epoch) == ("", 0)
    assert store.list_by_tag("fx", limit=10)[0].verified_wallet == ""


def test_cassandra_store_writes_the_badge_columns_on_every_listing_insert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical INSERT (both variants) and both projection INSERTs name and bind verified_wallet/verified_at; an empty badge binds ("", None)."""
    from app.modules.x402_directory.stores import cassandra as cassandra_store

    now = datetime(2026, 8, 30, tzinfo=UTC)
    executed: list[tuple[str, tuple]] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> SimpleNamespace:
            executed.append((stmt, params))
            return SimpleNamespace(one=lambda: None, was_applied=True)

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(cassandra_store, "get_cassandra_session", lambda: _Session())
    cass = cassandra_store.CassandraListingStore()
    listing = StoredListing(
        url_hash="h",
        url="https://api.example.com/q",
        price="$0.01",
        description="",
        schema_json="",
        settlement_tx_id="TX",
        term_end_epoch=int(now.timestamp()),
        created_at_epoch=int(now.timestamp()),
        tags=["fx"],
        payer="AGENT1",
        verified_wallet="AGENT1",
        verified_at_epoch=int(now.timestamp()),
    )

    cass.upsert(listing)
    # x402_probe_queue's seed INSERT (migration 118) carries no badge columns
    # at all -- it is asserted on separately below, not swept into this list.
    inserts = [
        (s, p) for s, p in executed if s.startswith("INSERT") and "x402_probe_queue" not in s
    ]
    assert len(inserts) == 4  # canonical, recency, tag fx, category:other
    for stmt, params in inserts:
        assert "verified_wallet, verified_at" in stmt
        assert stmt.count("?") == len(params)
        # Badge params sit before the trailing reimburses/contact/source/
        # discovered_from/boosted_until sextuple (104, 113's auto-discovery
        # columns, then 114's boost column).
        assert params[-7:-5] == ("AGENT1", now)
        assert params[-5:-3] == (False, "")
        assert params[-3:-1] == ("paid", "")
        assert params[-1] is None  # not boosted (boosted_until_epoch == 0)
    queue_seed = next(p for s, p in executed if "x402_probe_queue" in s)
    assert queue_seed[1] == PROBE_QUEUE_NEVER_PROBED

    executed.clear()
    assert cass.insert_if_absent(replace(listing, verified_wallet="", verified_at_epoch=0)) is True
    inserts = [
        (s, p) for s, p in executed if s.startswith("INSERT") and "x402_probe_queue" not in s
    ]
    assert "IF NOT EXISTS" in inserts[0][0]
    for stmt, params in inserts:
        assert "verified_wallet, verified_at" in stmt
        assert params[-7:-5] == ("", None)
        assert params[-5:-3] == (False, "")
        assert params[-3:-1] == ("paid", "")
        assert params[-1] is None


@pytest.mark.usefixtures("fake_redis")
def test_renew_route_stores_marks_fulfilled_and_serves_receipt_headers_on_refusal(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settled boost is stored then marked fulfilled; a refused one goes through run_with_refund too, but DirectoryError's PlatformError exemption (2026-09-02) means it is still the direct 403 with payment kept, never a refund, and is NOT marked fulfilled."""
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    monkeypatch.setattr(settings, "x402_listing_boost_days", 7)
    service = ListingService(store)
    original = _listed(store, "https://api.example.com/q", payer="P" * 58)
    monkeypatch.setattr(directory_routes, "listing_service", service)
    monkeypatch.setattr(
        directory_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        directory_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    response = directory_routes.x402_renew(_renew_request("https://api.example.com/q"))

    assert response.status_code == 200
    assert response.headers["PAYMENT-RESPONSE"] == "ok"
    body = json.loads(response.description)
    assert body["listing"]["settlement_tx_id"] == "TX123"
    # term_end is unaffected by a boost -- the route does not pass `now`
    # through to renew(), so this only checks equality, not arithmetic.
    assert body["listing"]["term_end_epoch"] == original.term_end_epoch
    assert body["listing"]["created_at_epoch"] == original.created_at_epoch
    now_epoch = int(datetime.now(tz=UTC).timestamp())
    assert abs(body["listing"]["boosted_until_epoch"] - (now_epoch + 7 * 86400)) < 5
    assert body["boost_days"] == 7
    assert fulfilled == [("TX123", "x402-directory-boost")]

    # Now the listing belongs to someone else: the same settled payer is refused.
    store.upsert(replace(store.get(original.url_hash), payer="AGENT-OTHER"))
    refused = directory_routes.x402_renew(_renew_request("https://api.example.com/q"))
    assert refused.status_code == 403
    assert json.loads(refused.description)["error"]["code"] == "listing_owned_by_another_payer"
    assert refused.headers["PAYMENT-RESPONSE"] == "ok"
    assert fulfilled == [("TX123", "x402-directory-boost")]
    assert store.get(original.url_hash).payer == "AGENT-OTHER"


# --------------------------------------------------------------------------- #
# Detail view (GET /x402/listings?url=)
# --------------------------------------------------------------------------- #
def _detail(url: str, ip: str = "") -> Response | dict:
    return directory_routes.x402_listing_detail(
        _request(
            method="GET",
            path="/api/v1/x402/listings",
            query={"url": url},
            headers={"X-Real-IP": ip} if ip else None,
        )
    )


@pytest.mark.usefixtures("fake_redis")
def test_listing_detail_serves_the_search_shape_plus_the_probe(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The detail view is the search item for that url plus its newest probe (null before the first probe)."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    listing = _listed(store, "https://api.example.com/q")

    before = _detail("HTTPS://API.example.com/q#frag")
    assert before["probe"] is None
    search_item = directory_routes.x402_search(_request(method="GET", path="/api/v1/x402/search"))[
        "items"
    ][0]
    assert before["listing"] == search_item
    assert before["listing"]["category"] == "other"

    store.record_probe(_probe(listing.url_hash, latency_ms=55))
    after = _detail("https://api.example.com/q")
    assert after["probe"]["latency_ms"] == 55
    assert after["probe"]["served_valid_402"] is True


@pytest.mark.usefixtures("fake_redis")
def test_listing_detail_is_404_for_unlisted_and_expired_urls(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unlisted -> 404, expired -> 404 (search has stopped serving it), bad url -> 400, missing url -> 400."""
    monkeypatch.setattr(settings, "x402_listing_term_days", 30)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    ListingService(store).create(
        normalized_url="https://old.example.com/q",
        price="$0.01",
        description="lapsed",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX",
        payer="AGENT1",
        now=datetime.now(tz=UTC) - timedelta(days=31),
    )

    assert _detail("https://nobody.example.com/q").status_code == 404
    assert _detail("https://old.example.com/q").status_code == 404
    assert _detail("ftp://old.example.com/q").status_code == 400
    assert _detail("").status_code == 400


@pytest.mark.usefixtures("fake_redis")
def test_listing_detail_is_rate_limited_per_ip(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The detail route shares the search route's per-IP hourly budget."""
    monkeypatch.setattr(settings, "x402_search_rate_limit_per_hour", 1)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    _listed(store, "https://api.example.com/q")

    assert "listing" in _detail("https://api.example.com/q", ip="203.0.113.7")
    assert _detail("https://api.example.com/q", ip="203.0.113.7").status_code == 429
    assert "listing" in _detail("https://api.example.com/q", ip="203.0.113.9")


# --------------------------------------------------------------------------- #
# Self-declared reimburses/contact flags (104)
# --------------------------------------------------------------------------- #
def test_a_listing_with_both_flags_set_stores_and_serves_them(
    store: InMemoryListingStore,
) -> None:
    """Reimburses and contact round-trip through create() exactly as given."""
    listing = ListingService(store).create(
        normalized_url="https://api.example.com/q",
        price="$0.01",
        description="mine",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        reimburses=True,
        contact="support@example.com",
    )

    assert listing.reimburses is True
    assert listing.contact == "support@example.com"
    assert store.get(listing.url_hash).reimburses is True
    assert store.get(listing.url_hash).contact == "support@example.com"


def test_a_listing_with_neither_flag_behaves_exactly_as_before(
    store: InMemoryListingStore,
) -> None:
    """Omitting both flags is unchanged behavior: unset, never a fabricated claim."""
    listing = ListingService(store).create(
        normalized_url="https://api.example.com/q",
        price="$0.01",
        description="mine",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )

    assert listing.reimburses is False
    assert listing.contact == ""


@pytest.mark.parametrize("raw", ["x" * 257, "x" * 500])
def test_an_overlong_contact_is_rejected_at_decode(raw: str) -> None:
    """Contact is length-bounded like every other free-text field here -- rejected before the gate, never charged for.

    Same convention as test_an_overlong_asset_or_tag_item_is_rejected_at_decode:
    decoding is the pre-gate step, so an oversized contact never reaches
    x402_list's normalize_url/create() at all.
    """
    body = json.dumps(
        {"url": "https://api.example.com/v1/quote", "price": "$0.01", "contact": raw}
    ).encode()

    with pytest.raises(serialization.DecodeError):
        serialization.decode(body, X402ListingRequest)

    # ... and at the exact bound it decodes fine.
    ok = json.dumps(
        {"url": "https://api.example.com/v1/quote", "price": "$0.01", "contact": "x" * 256}
    ).encode()
    assert serialization.decode(ok, X402ListingRequest).contact == "x" * 256


@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_an_overlong_contact_never_reaches_the_payment_gate_via_the_route(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end: the route's own decode step rejects an oversized contact as a 400, gate never runs, nothing stored."""
    gate_calls: list[str] = []

    def _record_gate(*_args: object, **_kwargs: object) -> Never:
        gate_calls.append("gate")
        raise AssertionError("the payment gate must not run for an oversized contact")

    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(directory_routes, "require_paid_request", _record_gate)

    response = directory_routes.x402_list(
        _request(
            body=json.dumps(
                {
                    "url": "https://api.example.com/v1/quote",
                    "price": "$0.01",
                    "contact": "x" * 257,
                }
            ).encode()
        )
    )

    assert response.status_code == 400
    assert "invalid_request" in response.description
    assert gate_calls == []
    assert store.list_recent(limit=50) == []


def test_renew_does_not_change_reimburses_or_contact(
    store: InMemoryListingStore,
) -> None:
    """A boost buys search priority only -- reimburses/contact follow every other descriptive field (price, description, tags, category): unchanged by renew(), only settable via list/relist."""
    service = ListingService(store)
    base = datetime(2026, 8, 1, tzinfo=UTC)
    service.create(
        normalized_url="https://api.example.com/q",
        price="$0.01",
        description="mine",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        reimburses=True,
        contact="support@example.com",
        now=base,
    )

    renewed = service.renew(
        normalized_url="https://api.example.com/q",
        payer="AGENT1",
        settlement_tx_id="TX2",
        now=base + timedelta(days=10),
    )

    assert renewed.reimburses is True
    assert renewed.contact == "support@example.com"


@pytest.mark.usefixtures("fake_redis")
def test_search_and_listing_detail_both_serve_the_new_flags(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both free read paths (search, listing detail) carry reimburses/contact -- an agent can see them without paying."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    ListingService(store).create(
        normalized_url="https://api.example.com/q",
        price="$0.01",
        description="mine",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        reimburses=True,
        contact="support@example.com",
    )

    search_item = directory_routes.x402_search(_request(method="GET", path="/api/v1/x402/search"))[
        "items"
    ][0]
    assert search_item["reimburses"] is True
    assert search_item["contact"] == "support@example.com"

    detail = _detail("https://api.example.com/q")
    assert detail["listing"]["reimburses"] is True
    assert detail["listing"]["contact"] == "support@example.com"


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
    from app.modules.x402_directory.stores import cassandra as cassandra_store

    naive = datetime(2026, 9, 4, 13, 18, 17)  # noqa: DTZ001 -- naive on purpose, see docstring
    assert naive.tzinfo is None
    assert cassandra_store._epoch(naive) == 1788527897  # the correct UTC epoch
    assert cassandra_store._epoch(None) == 0


# --------------------------------------------------------------------------- #
# Promo-bypass identity spoofing (found 2026-09-04, same pattern as the
# x402_social reversal in commit f8d84a6): x402_list and x402_renew both feed
# `payer` in as the wallet that OWNS the listing (create()'s first-claim-wins
# ownership check, renew()'s `existing.payer != payer` check) -- not merely
# payment attribution. modules/x402/promo.py's own docstring says a promo
# redemption's wallet is checked for SYNTACTIC validity only ("a successful
# redemption is not proof the caller controls that wallet"), so a promo
# bypass would have let anyone claim/grief a url, or free-renew (or probe the
# ownership check of) a listing they do not own, via `?promo_wallet=`. Closed
# by never reading promo params on these two routes at all. These tests lock
# in the opposite invariant: promo params in the query string are ignored,
# not honored.
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis")
@pytest.mark.parametrize("route_name", ["x402_list", "x402_renew"])
def test_list_and_renew_never_forward_promo_params(
    store: InMemoryListingStore,
    monkeypatch: pytest.MonkeyPatch,
    route_name: str,
) -> None:
    """Neither paid write route may pass ?promo=/?promo_wallet= into require_paid_request, even when present in the query string."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    routes_by_name = {
        "x402_list": (
            "/api/v1/x402/list",
            {"body": b'{"url":"https://a.example/x","price":"$0.01"}'},
        ),
        "x402_renew": (
            "/api/v1/x402/list/renew",
            {"body": b'{"url":"https://api.example.com/q"}'},
        ),
    }
    path, extra_kwargs = routes_by_name[route_name]
    if route_name == "x402_renew":
        _listed(store, "https://api.example.com/q")

    captured: dict = {}

    def _spy_require_paid_request(*_args: object, **kwargs: object) -> object:
        captured.update(kwargs)
        return _settled_result()

    monkeypatch.setattr(directory_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(directory_routes, "mark_fulfilled", lambda *_a, **_kw: None)

    route = getattr(directory_routes, route_name)
    route(
        _request(
            query={"promo": "LAUNCH1000-TEST", "promo_wallet": "P" * 58},
            path=path,
            **extra_kwargs,
        )
    )

    assert "promo_code" not in captured
    assert "promo_wallet" not in captured


# --------------------------------------------------------------------------- #
# Auto-discovered ("unclaimed") listing import (2026-09-06)
# --------------------------------------------------------------------------- #
_DISCOVERED_URL = "https://onestepchess.xyz/api/v1/moves"


def _facilitator_resource(
    *,
    url: str = _DISCOVERED_URL,
    method: str = "POST",
    description: str = "Submit one legal move.",
    amount: str = "10000",
    asset: str = "31566704",
    decimals: int | None = 6,
    name: str | None = "USD Coin",
) -> dict:
    """One item shaped like a real GoPlausible `discovery/resources` entry."""
    extra: dict = {}
    if decimals is not None:
        extra["decimals"] = decimals
    if name is not None:
        extra["name"] = name
    return {
        "resourceUrl": url,
        "method": method,
        "description": description,
        "merchantId": "SOME_MERCHANT_ID",
        "accepts": [{"scheme": "exact", "amount": amount, "asset": asset, "extra": extra}],
    }


def test_import_creates_an_auto_discovered_listing_never_a_paid_one(
    store: InMemoryListingStore,
) -> None:
    """A brand-new url from the feed becomes source=auto_discovered, unowned, with no settlement -- never mistaken for a paid listing."""
    result = import_discovered_resources([_facilitator_resource()], store=store)

    assert (result.scanned, result.created, result.refreshed) == (1, 1, 0)
    listing = store.get(url_hash(normalize_url(_DISCOVERED_URL)))
    assert listing is not None
    assert listing.source == SOURCE_AUTO_DISCOVERED
    assert listing.is_auto_discovered is True
    assert listing.discovered_from == DISCOVERY_SOURCE_LABEL
    assert listing.payer == ""
    assert listing.settlement_tx_id == ""
    assert listing.description == "[POST] Submit one legal move."
    assert listing.price == "~0.01 USD Coin"


def test_import_never_overwrites_a_real_paid_listing(store: InMemoryListingStore) -> None:
    """A url already listed by a real payer is skipped outright, whatever the facilitator now reports about it -- an already-public catalog entry is never grounds to overwrite something someone paid us to list."""
    service = ListingService(store)
    service.create(
        normalized_url=_DISCOVERED_URL,
        price="$5.00",
        description="the real, paid listing",
        assets=[],
        tags=["chess"],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )

    result = import_discovered_resources([_facilitator_resource()], store=store)

    assert (result.created, result.refreshed, result.skipped_existing_paid) == (0, 0, 1)
    listing = store.get(url_hash(_DISCOVERED_URL))
    assert listing is not None
    assert listing.source == SOURCE_PAID
    assert listing.payer == "AGENT1"
    assert listing.description == "the real, paid listing"


def test_import_refreshes_an_existing_auto_discovered_listing_in_place(
    store: InMemoryListingStore,
) -> None:
    """A second import of the same url refreshes price/description/term_end but preserves created_at -- a re-import must not jump the stub to the front of the free feed."""
    now = datetime(2026, 9, 1, tzinfo=UTC)
    first = import_discovered_resources(
        [_facilitator_resource(description="old copy")], store=store, now=now
    )
    assert (first.created, first.refreshed) == (1, 0)
    original = store.get(url_hash(_DISCOVERED_URL))
    assert original is not None

    later = now + timedelta(days=5)
    second = import_discovered_resources(
        [_facilitator_resource(description="updated copy")], store=store, now=later
    )

    assert (second.created, second.refreshed) == (0, 1)
    refreshed = store.get(url_hash(_DISCOVERED_URL))
    assert refreshed is not None
    assert refreshed.description == "[POST] updated copy"
    assert refreshed.created_at_epoch == original.created_at_epoch
    assert refreshed.term_end_epoch > original.term_end_epoch


def test_import_skips_a_resource_with_an_unusable_url(store: InMemoryListingStore) -> None:
    """One malformed url in a feed of many must not abort the whole import."""
    result = import_discovered_resources(
        [{"resourceUrl": "not-a-url", "description": "bad"}, _facilitator_resource()],
        store=store,
    )

    assert result.scanned == 2
    assert result.skipped_invalid == 1
    assert result.created == 1


def test_import_falls_back_to_a_raw_price_string_without_decimals(
    store: InMemoryListingStore,
) -> None:
    """A resource whose `extra` carries no decimals still gets an informational (non-payment) price string, never an empty one."""
    result = import_discovered_resources(
        [_facilitator_resource(decimals=None, name=None, amount="500", asset="12345")],
        store=store,
    )

    assert result.created == 1
    listing = store.get(url_hash(_DISCOVERED_URL))
    assert listing is not None
    assert listing.price == "500 (asset 12345)"


def test_a_real_payer_can_claim_a_previously_auto_discovered_listing(
    store: InMemoryListingStore,
) -> None:
    """Relisting a url that was only an auto-discovered stub works through the ORDINARY create() ownership rule (an empty-payer row reads as unowned) and yields a real paid listing that leaves the free feed."""
    import_discovered_resources([_facilitator_resource()], store=store)
    assert store.get(url_hash(_DISCOVERED_URL)).source == SOURCE_AUTO_DISCOVERED  # type: ignore[union-attr]

    claimed = ListingService(store).create(
        normalized_url=_DISCOVERED_URL,
        price="$0.01",
        description="I actually run this endpoint",
        assets=[],
        tags=["chess"],
        schema_json="",
        settlement_tx_id="REAL_TX",
        payer="REAL_AGENT",
    )

    assert claimed.source == SOURCE_PAID
    assert claimed.payer == "REAL_AGENT"
    stored = store.get(url_hash(_DISCOVERED_URL))
    assert stored is not None
    assert stored.source == SOURCE_PAID
    assert stored.payer == "REAL_AGENT"
    assert store.list_auto_discovered(limit=50) == []


@pytest.mark.usefixtures("fake_redis")
def test_search_serves_paid_listings_before_auto_discovered_ones(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An auto-discovered stub never outranks, or is interleaved by recency with, a real paid listing -- it only fills room a paid listing left empty."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    now = datetime.now(tz=UTC)
    # The auto-discovered import happens FIRST but is timestamped OLDER --
    # if the two feeds were merged by recency alone the paid listing (newer)
    # would still sort first; make the regression harder to miss by also
    # giving the import an OLDER created_at than the paid listing, so a
    # naive "just merge and sort by created_at" implementation would still
    # rank paid first here by luck. The real guarantee is structural (see
    # ListingService.search()'s docstring), not just this specific ordering.
    import_discovered_resources(
        [_facilitator_resource(url="https://old-but-free.example.com/x")],
        store=store,
        now=now - timedelta(days=1),
    )
    ListingService(store).create(
        normalized_url="https://real-paid.example.com/x",
        price="$0.01",
        description="paid, listed after the import",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
        now=now,
    )

    result = directory_routes.x402_search(_request(method="GET", path="/api/v1/x402/search"))

    sources = [item["source"] for item in result["items"]]
    assert sources[0] == "paid"
    assert "auto_discovered" in sources
    assert result["items"][0]["url"] == "https://real-paid.example.com/x"


@pytest.mark.usefixtures("fake_redis")
def test_search_fills_only_remaining_room_with_auto_discovered_listings(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When paid listings alone already fill the limit, no auto-discovered stub is returned at all."""
    monkeypatch.setattr(settings, "x402_search_max_results", 1)
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    import_discovered_resources([_facilitator_resource()], store=store)
    ListingService(store).create(
        normalized_url="https://real-paid.example.com/x",
        price="$0.01",
        description="the only paid listing",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )

    result = directory_routes.x402_search(_request(method="GET", path="/api/v1/x402/search"))

    assert [item["source"] for item in result["items"]] == ["paid"]


@pytest.mark.usefixtures("fake_redis")
def test_tag_and_category_search_never_return_an_auto_discovered_listing(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An auto-discovered stub has no tags/category of its own and must never surface in a filtered search."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    import_discovered_resources([_facilitator_resource()], store=store)
    ListingService(store).create(
        normalized_url="https://real-paid.example.com/x",
        price="$0.01",
        description="paid, tagged",
        assets=[],
        tags=["fx"],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )

    tag_result = directory_routes.x402_search(
        _request(method="GET", path="/api/v1/x402/search", query={"tag": "fx"})
    )
    category_result = directory_routes.x402_search(
        _request(method="GET", path="/api/v1/x402/search", query={"category": "other"})
    )

    assert [item["url"] for item in tag_result["items"]] == ["https://real-paid.example.com/x"]
    assert all(item["source"] == "paid" for item in category_result["items"])


@pytest.mark.usefixtures("fake_redis")
def test_search_response_always_carries_source_and_discovered_from(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every search result names its source on the wire -- a real listing "paid"/"", never omitted or shape-identical to an auto-discovered one."""
    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    ListingService(store).create(
        normalized_url="https://real-paid.example.com/x",
        price="$0.01",
        description="paid",
        assets=[],
        tags=[],
        schema_json="",
        settlement_tx_id="TX1",
        payer="AGENT1",
    )
    import_discovered_resources([_facilitator_resource()], store=store)

    result = directory_routes.x402_search(_request(method="GET", path="/api/v1/x402/search"))

    by_url = {item["url"]: item for item in result["items"]}
    assert by_url["https://real-paid.example.com/x"]["source"] == "paid"
    assert by_url["https://real-paid.example.com/x"]["discovered_from"] == ""
    assert by_url[_DISCOVERED_URL]["source"] == "auto_discovered"
    assert by_url[_DISCOVERED_URL]["discovered_from"] == DISCOVERY_SOURCE_LABEL


def test_fetch_facilitator_resources_paginates_until_the_reported_total(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetch keeps requesting pages (network/offset) until pagination.total is reached, and reports it."""
    from app.modules.x402_directory.services import discovery_import as discovery_import_module

    monkeypatch.setattr(discovery_import_module, "_PAGE_SIZE", 2)
    seen_offsets: list[int] = []
    seen_networks: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params["offset"])
        seen_offsets.append(offset)
        seen_networks.append(request.url.params["network"])
        remaining = max(0, 5 - offset)
        page = [
            _facilitator_resource(url=f"https://api{offset + i}.example.com/x")
            for i in range(min(2, remaining))
        ]
        return httpx.Response(
            200,
            json={"items": page, "pagination": {"limit": 2, "offset": offset, "total": 5}},
        )

    result = fetch_facilitator_resources(transport=httpx.MockTransport(_handler))

    assert seen_offsets == [0, 2, 4]
    assert all(net == settings.x402_network for net in seen_networks)
    assert result.error == ""
    assert result.total_reported == 5
    assert len(result.resources) == 5


def test_fetch_facilitator_resources_degrades_on_http_error() -> None:
    """A facilitator-side failure never raises into the admin route -- it returns whatever was collected plus a non-empty error."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    result = fetch_facilitator_resources(transport=httpx.MockTransport(_handler))

    assert result.resources == []
    assert result.error != ""


def test_fetch_facilitator_resources_handles_a_missing_items_array() -> None:
    """A response missing the expected `items` array is a clean, reported failure, not a crash or a silent empty success."""

    def _handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"pagination": {"total": 0}})

    result = fetch_facilitator_resources(transport=httpx.MockTransport(_handler))

    assert result.resources == []
    assert result.error != ""


def test_admin_import_discovered_requires_admin_wallet(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No authorized admin session -> refused before any fetch is even attempted."""
    from app.core.http_errors import json_error_response

    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(
        directory_routes,
        "require_admin_wallet",
        lambda _request: json_error_response(403, "forbidden", "not an admin wallet"),
    )

    def _must_not_fetch() -> None:
        raise AssertionError("must not fetch the facilitator without an authorized admin")

    monkeypatch.setattr(directory_routes, "fetch_facilitator_resources", _must_not_fetch)

    response = directory_routes.x402_admin_import_discovered_listings(
        _request(method="POST", path="/api/v1/admin/x402/directory/import-discovered")
    )

    assert response.status_code == 403


def test_admin_import_discovered_writes_listings_and_reports_counts(
    store: InMemoryListingStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An authorized admin trigger fetches, imports, and reports both fetch and import outcomes -- never a bare 200 with no numbers."""
    from app.modules.x402_directory.services.discovery_import import FacilitatorFetchResult

    monkeypatch.setattr(directory_routes, "listing_service", ListingService(store))
    monkeypatch.setattr(directory_routes, "require_admin_wallet", lambda _request: None)
    fake_fetch = FacilitatorFetchResult(
        resources=[_facilitator_resource()], total_reported=1, error=""
    )
    monkeypatch.setattr(directory_routes, "fetch_facilitator_resources", lambda: fake_fetch)

    response = directory_routes.x402_admin_import_discovered_listings(
        _request(method="POST", path="/api/v1/admin/x402/directory/import-discovered")
    )

    assert response["fetch"]["total_reported_by_facilitator"] == 1
    assert response["fetch"]["error"] == ""
    assert response["import"]["created"] == 1
    listing = store.get(url_hash(_DISCOVERED_URL))
    assert listing is not None
    assert listing.source == SOURCE_AUTO_DISCOVERED


def _fake_cassandra_listing_row(item: StoredListing) -> SimpleNamespace:
    """A SimpleNamespace shaped like a driver row for `item`, covering every column _row_to_listing reads."""
    return SimpleNamespace(
        url_hash=item.url_hash,
        url=item.url,
        price=item.price,
        assets=list(item.assets),
        description=item.description,
        schema_json=item.schema_json,
        tags=list(item.tags),
        term_end=datetime.fromtimestamp(item.term_end_epoch, tz=UTC),
        settlement_tx_id=item.settlement_tx_id,
        created_at=datetime.fromtimestamp(item.created_at_epoch, tz=UTC),
        payer=item.payer,
        verified_wallet=item.verified_wallet,
        verified_at=(
            datetime.fromtimestamp(item.verified_at_epoch, tz=UTC)
            if item.verified_at_epoch
            else None
        ),
        category=item.category,
        reimburses=item.reimburses,
        contact=item.contact,
        source=item.source,
        discovered_from=item.discovered_from,
    )


def test_cassandra_store_writes_an_auto_discovered_listing_only_to_its_own_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An auto-discovered upsert writes the canonical row and the dedicated auto-discovered feed only -- never x402_listings_by_recency/by_tag, which stay paid-only."""
    from app.modules.x402_directory.stores import cassandra as cassandra_store

    now = datetime(2026, 9, 6, tzinfo=UTC)
    listing = StoredListing(
        url_hash="h",
        url=_DISCOVERED_URL,
        price="~0.01 USD Coin",
        description="[POST] Submit a move",
        schema_json="",
        settlement_tx_id="",
        term_end_epoch=int((now + timedelta(days=30)).timestamp()),
        created_at_epoch=int(now.timestamp()),
        payer="",
        source=SOURCE_AUTO_DISCOVERED,
        discovered_from=DISCOVERY_SOURCE_LABEL,
    )
    executed: list[tuple[str, tuple]] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> SimpleNamespace:
            executed.append((stmt, params))
            if stmt.startswith("SELECT"):
                return SimpleNamespace(one=lambda: None)
            return SimpleNamespace(one=lambda: None, was_applied=True)

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(cassandra_store, "get_cassandra_session", lambda: _Session())
    cass = cassandra_store.CassandraListingStore()

    cass.upsert(listing)

    inserts = [(s, p) for s, p in executed if s.startswith("INSERT")]
    assert len(inserts) == 2  # canonical x402_listings + the auto-discovered feed
    assert any("x402_listings_auto_discovered_by_recency" in s for s, _ in inserts)
    assert not any("x402_listings_by_recency" in s for s, _ in inserts)
    assert not any("x402_listings_by_tag" in s for s, _ in inserts)


def test_cassandra_store_claim_deletes_the_stub_from_the_auto_discovered_feed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relisting a previously auto-discovered url as a real payer deletes the stub's row from the auto-discovered feed and writes fresh rows into the paid projections instead."""
    from app.modules.x402_directory.stores import cassandra as cassandra_store

    stub_created = datetime(2026, 9, 1, tzinfo=UTC)
    stub = StoredListing(
        url_hash="h",
        url=_DISCOVERED_URL,
        price="",
        description="",
        schema_json="",
        settlement_tx_id="",
        term_end_epoch=int((stub_created + timedelta(days=30)).timestamp()),
        created_at_epoch=int(stub_created.timestamp()),
        payer="",
        source=SOURCE_AUTO_DISCOVERED,
        discovered_from=DISCOVERY_SOURCE_LABEL,
    )
    executed: list[tuple[str, tuple]] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> SimpleNamespace:
            executed.append((stmt, params))
            if stmt.startswith("SELECT"):
                return SimpleNamespace(one=lambda: _fake_cassandra_listing_row(stub))
            return SimpleNamespace(one=lambda: None, was_applied=True)

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(cassandra_store, "get_cassandra_session", lambda: _Session())
    cass = cassandra_store.CassandraListingStore()

    claimed = replace(
        stub,
        price="$0.01",
        description="the real thing",
        payer="REAL_AGENT",
        settlement_tx_id="TX1",
        created_at_epoch=int((stub_created + timedelta(days=5)).timestamp()),
        source=SOURCE_PAID,
        discovered_from="",
    )

    cass.upsert(claimed)

    deletes = [(s, p) for s, p in executed if s.startswith("DELETE")]
    assert any("x402_listings_auto_discovered_by_recency" in s for s, _ in deletes)
    inserts = [(s, p) for s, p in executed if s.startswith("INSERT")]
    assert any("x402_listings_by_recency" in s and "auto_discovered" not in s for s, _ in inserts)
    assert not any("x402_listings_auto_discovered_by_recency" in s for s, _ in inserts)


def test_cassandra_store_delete_uses_the_auto_discovered_feed_for_a_stub(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Admin-deleting an auto-discovered listing removes it from ITS feed, never issuing a (harmless but wrong) delete against the paid recency feed."""
    from app.modules.x402_directory.stores import cassandra as cassandra_store

    now = datetime(2026, 9, 6, tzinfo=UTC)
    stub = StoredListing(
        url_hash="h",
        url=_DISCOVERED_URL,
        price="",
        description="",
        schema_json="",
        settlement_tx_id="",
        term_end_epoch=int((now + timedelta(days=30)).timestamp()),
        created_at_epoch=int(now.timestamp()),
        payer="",
        source=SOURCE_AUTO_DISCOVERED,
        discovered_from=DISCOVERY_SOURCE_LABEL,
    )
    executed: list[tuple[str, tuple]] = []

    class _Session:
        def execute(self, stmt: str, params: tuple) -> SimpleNamespace:
            executed.append((stmt, params))
            if stmt.startswith("SELECT"):
                return SimpleNamespace(one=lambda: _fake_cassandra_listing_row(stub))
            return SimpleNamespace(one=lambda: None, was_applied=True)

    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(cassandra_store, "get_cassandra_session", lambda: _Session())
    cass = cassandra_store.CassandraListingStore()

    assert cass.delete("h") is True

    deletes = [(s, p) for s, p in executed if s.startswith("DELETE")]
    assert any("x402_listings_auto_discovered_by_recency" in s for s, _ in deletes)
    assert not any(
        "x402_listings_by_recency" in s and "auto_discovered" not in s for s, _ in deletes
    )
    assert not any("x402_listings_by_tag" in s for s, _ in deletes)
