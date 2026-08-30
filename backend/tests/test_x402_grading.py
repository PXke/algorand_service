"""x402 endpoint-grading tests: paid grading of any URL, credibility weighting, free index.

Fully offline. The facilitator is a stub that never touches the network (same
shape as test_x402_board.py's), Redis is a fake at the get_redis seam, the
grade store is the in-memory backend, and the settlement ledger the credibility
weight reads is modules/x402's own in-memory ledger. Nothing here settles a
real payment or reaches TestNet, and no test constructs a directory listing --
this module no longer knows what one is.

Replay protection and the settlement ledger's WRITE path are shared
infrastructure (modules/x402/) already covered by test_x402_directory.py --
they are not re-tested here. What IS grading-specific and tested here: that a
flat payment is the only gate, the one-grade-per-(grader, url) overwrite rule,
the credibility-weighted aggregate and its fallbacks, the bounds on the ledger
read that produces the weights, and the free index's bounds.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never

import pytest

pytest.importorskip("x402")

from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2
from x402.schemas.payments import PaymentRequirements
from x402.schemas.responses import SupportedKind, SupportedResponse
from x402.schemas.v1 import PaymentRequirementsV1
from x402.server import x402ResourceServerSync

import app.modules.x402_grading as grading_package
from app.core import cassandra as cassandra_core
from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import replay as replay_module
from app.modules.x402.settlement import (
    InMemorySettlementStore,
    SettlementRecord,
    set_settlement_store,
)
from app.modules.x402_grading.api import routes as grading_routes
from app.modules.x402_grading.models.domain import GradedEndpoint, GradingError, StoredGrade
from app.modules.x402_grading.services.credibility import (
    CassandraSpendLookup,
    InMemorySpendLookup,
    SpendLookup,
)
from app.modules.x402_grading.services.grading_service import GradingService
from app.modules.x402_grading.services.url_key import url_hash
from app.modules.x402_grading.stores.memory import InMemoryGradeStore

_PAY_TO = "A" * 58
_PAYER = "P" * 58
_OTHER_PAYER = "Q" * 58
_THIRD_PAYER = "R" * 58

# An endpoint nobody has listed anywhere. That is the point: grading takes any
# http(s) URL, so every test grades something the directory has never heard of.
_URL = "https://api.unlisted-example.com/v1/quote"
_MAINNET = "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73k/QCSD3JhBfE="


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


class _UnreadableLedgerLookup:
    """A spend lookup whose backing ledger cannot be read."""

    def spend_by_payer(self, payers: object) -> dict[str, int] | None:
        _ = payers
        return None


class _FakeCassandraSession:
    """Records every (statement, params) pair and replays canned ledger rows."""

    def __init__(self, rows: list[SimpleNamespace] | None = None) -> None:
        self.rows = rows or []
        self.calls: list[tuple[object, tuple]] = []

    def execute(self, statement: object, params: tuple) -> list[SimpleNamespace]:
        self.calls.append((statement, params))
        return self.rows


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
    path: str = "/api/v1/x402/grades",
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
        amount_atomic="20000",
        payment_txid=txid,
        asset_id="10458941",
        network=ALGORAND_TESTNET_CAIP2,
    )


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #
@pytest.fixture
def store() -> InMemoryGradeStore:
    """A fresh in-memory grade store per test."""
    return InMemoryGradeStore()


@pytest.fixture
def ledger() -> Iterator[InMemorySettlementStore]:
    """The shared in-memory settlement ledger, installed process-wide and torn down.

    Installed at the shared seam rather than handed to the lookup directly:
    InMemorySpendLookup deliberately reads modules/x402's own store so dev and
    tests see exactly the payments require_paid_request recorded, and a test
    that bypassed that seam would not exercise the real wiring.
    """
    settlement_store = InMemorySettlementStore()
    set_settlement_store(settlement_store)
    yield settlement_store
    set_settlement_store(None)


def _spent(
    ledger: InMemorySettlementStore,
    *,
    payer: str,
    amount_atomic: str,
    resource: str = "x402-directory-list",
    network: str = ALGORAND_TESTNET_CAIP2,
) -> None:
    """Record that `payer` settled a payment with this marketplace for some product."""
    ledger.record_settlement(
        SettlementRecord(
            tx_id=f"PAID_{payer[:4]}_{amount_atomic}",
            asset_id="10458941",
            amount_atomic=amount_atomic,
            payer=payer,
            resource=resource,
            network=network,
            settled_at_epoch=int(datetime.now(tz=UTC).timestamp()),
        )
    )


def _service(
    store: InMemoryGradeStore,
    lookup: SpendLookup | None = None,
) -> GradingService:
    return GradingService(store, lookup=lookup or InMemorySpendLookup())


def _grade(
    service: GradingService,
    *,
    grader: str,
    score: int,
    url: str = _URL,
    comment: str = "",
    settlement_tx_id: str = "TX",
    now: datetime | None = None,
) -> StoredGrade:
    """Submit one grade through the service, resolving the URL the way a route does."""
    normalized, hashed = service.resolve_url(url)
    return service.submit(
        url=normalized,
        url_hash_value=hashed,
        grader=grader,
        score=score,
        comment=comment,
        settlement_tx_id=settlement_tx_id,
        now=now,
    )


def _endpoint(service: GradingService, url: str = _URL) -> GradedEndpoint:
    """The index entry a paid score lookup aggregates over."""
    _, hashed = service.resolve_url(url)
    entry = service.graded_endpoint(hashed)
    assert entry is not None
    return entry


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


@pytest.fixture
def weights(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the weighting knobs so the arithmetic in these tests is explicit."""
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_grading_base_weight_atomic", 10_000)
    monkeypatch.setattr(settings, "x402_grading_max_weight_atomic", 1_000_000)


def _grade_body(**overrides: object) -> bytes:
    body: dict[str, object] = {"url": _URL, "score": 4, "comment": "solid"}
    body.update(overrides)
    return json.dumps(body).encode()


# --------------------------------------------------------------------------- #
# The 402 offer, and what is checked before it
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_grading_any_url_without_payment_returns_402_with_the_grading_price_and_tag(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No payment header yields a 402 carrying the configured payTo, TestNet CAIP-2 id, USDC TestNet asset id, grading's own price and the challenge tag."""
    from x402.http.utils import decode_payment_required_header
    from x402.mechanisms.avm.constants import USDC_TESTNET_ASA_ID

    monkeypatch.setattr(settings, "x402_grading_grade_price", "$0.02")
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))

    response = grading_routes.x402_grade_submit(_request(body=_grade_body()))

    assert response.status_code == 402
    offer = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"]).accepts[0]
    assert offer.pay_to == _PAY_TO
    assert offer.network == ALGORAND_TESTNET_CAIP2
    assert offer.asset == str(USDC_TESTNET_ASA_ID)
    # 0.02 USDC in atomic units at 6 decimals — grading's price, not the
    # directory's $0.10 or the board's $0.05.
    assert offer.amount == "20000"
    assert offer.extra["tag"] == x402_client.CHALLENGE_TAG


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_the_402_declares_body_discovery_and_the_overwrite_and_weighting_rules(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 402 declares a JSON-body Bazaar extension and tells the payer, before they commit, that re-grading replaces and that their grade is weighted by what they have spent."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(grading_routes, "grading_service", _service(store))

    response = grading_routes.x402_grade_submit(_request(body=_grade_body()))

    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    bazaar = (payment_required.extensions or {}).get("bazaar")
    assert bazaar is not None
    # POST takes its input as a body, not query params — a query-shaped
    # declaration would describe this route's input incorrectly to the Bazaar.
    assert "body" in json.dumps(bazaar)
    description = payment_required.resource.description or ""
    assert "replaces your previous grade" in description
    assert "weighted" in description
    # The gate the previous build had is gone, and the 402 must not still
    # advertise it: nothing is refused after settlement any more.
    assert "refused after settlement" not in description


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
@pytest.mark.parametrize(
    "body",
    [
        b"{not json",
        b'{"url":"https://api.unlisted-example.com/v1/quote"}',  # no score
        b'{"url":"https://api.unlisted-example.com/v1/quote","score":0}',
        b'{"url":"https://api.unlisted-example.com/v1/quote","score":6}',
        b'{"url":"https://api.unlisted-example.com/v1/quote","score":"four"}',
        b'{"url":"ftp://api.example.com/v1/quote","score":4}',
        b'{"url":"https://","score":4}',
    ],
)
def test_malformed_bodies_are_rejected_before_the_payment_gate(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    """Bad JSON, out-of-range scores and unusable URLs are 400s, not 402s — nobody is charged to submit an invalid grade."""
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))

    response = grading_routes.x402_grade_submit(_request(body=body))

    assert response.status_code == 400
    assert "invalid_request" in response.description
    assert store.list_graded_endpoints(limit=10) == []


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_an_over_long_comment_is_rejected_before_the_payment_gate(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An opinion beyond the 280-character cap is a 400, not a charged request."""
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))

    response = grading_routes.x402_grade_submit(_request(body=_grade_body(comment="z" * 281)))

    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# Payment is the only gate
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis")
def test_grading_an_arbitrary_unlisted_url_succeeds_on_payment_alone(
    store: InMemoryGradeStore, ledger: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wallet with no history whatsoever, grading a URL nobody has listed, is stored on the strength of the payment alone — there is no eligibility check left to fail."""
    assert ledger.settlements == []  # nobody has ever paid us for anything
    service = _service(store)
    monkeypatch.setattr(grading_routes, "grading_service", service)
    monkeypatch.setattr(
        grading_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    response = grading_routes.x402_grade_submit(
        _request(body=_grade_body(url="https://totally.unknown.example/api", score=5))
    )

    assert response.status_code == 200
    payload = json.loads(response.description)
    assert payload["url"] == "https://totally.unknown.example/api"
    assert payload["grade"]["grader"] == _PAYER
    assert payload["grade"]["score"] == 5
    assert payload["settlement_tx_id"] == "TX123"
    stored = store.get(url_hash("https://totally.unknown.example/api"), _PAYER)
    assert stored is not None
    assert stored.score == 5


def test_the_grading_module_does_not_import_the_directory_at_all() -> None:
    """The decoupling is structural, not just behavioural: no file in this module may import x402_directory.

    Parsed rather than grepped, so that prose EXPLAINING why the dependency was
    removed does not read as the dependency itself -- several docstrings here
    name x402_directory precisely to say this module does not use it. Only
    x402_grading's own files are checked: modules/x402 (the shared payment gate
    and ledger) is a deliberate and documented read-only dependency.
    """
    module_root = Path(grading_package.__file__).parent
    offenders: list[str] = []
    for path in sorted(module_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported = [node.module or ""]
            if any("x402_directory" in name for name in imported):
                offenders.append(f"{path.relative_to(module_root).as_posix()}:{node.lineno}")
    assert offenders == []


@pytest.mark.usefixtures("ledger")
def test_regrading_replaces_your_own_grade_rather_than_stacking_a_second(
    store: InMemoryGradeStore,
) -> None:
    """One grade per (wallet, url): a second paid grade from the same wallet moves the first rather than adding to it."""
    service = _service(store)
    _grade(service, grader=_PAYER, score=5, comment="great")
    _grade(service, grader=_PAYER, score=2, comment="changed my mind")

    rows = store.list_for_url(url_hash(_URL), limit=10)
    assert len(rows) == 1
    assert rows[0].score == 2
    assert rows[0].comment == "changed my mind"
    assert service.aggregate(_endpoint(service)).count == 1


@pytest.mark.usefixtures("ledger")
def test_two_graders_of_the_same_url_each_keep_their_own_grade(
    store: InMemoryGradeStore,
) -> None:
    """The overwrite rule is per wallet — one wallet's re-grade never touches another's."""
    service = _service(store)
    _grade(service, grader=_PAYER, score=5)
    _grade(service, grader=_OTHER_PAYER, score=1)

    assert len(store.list_for_url(url_hash(_URL), limit=10)) == 2


@pytest.mark.usefixtures("ledger")
def test_url_normalization_folds_case_and_fragment_onto_one_graded_endpoint(
    store: InMemoryGradeStore,
) -> None:
    """Scheme/host case and a fragment cannot split one endpoint's grades across two scores, and the path stays untouched."""
    service = _service(store)
    _grade(service, grader=_PAYER, score=5, url="https://API.Unlisted-Example.com/v1/quote#docs")
    _grade(service, grader=_PAYER, score=3, url=_URL)

    assert len(store.list_graded_endpoints(limit=10)) == 1
    # The path is case-significant and must NOT be folded.
    _grade(service, grader=_PAYER, score=4, url="https://api.unlisted-example.com/v1/QUOTE")
    assert len(store.list_graded_endpoints(limit=10)) == 2


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_an_unattributable_payment_cannot_be_stored_as_a_grade(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settled payment with no payer address has no wallet to key the overwrite rule on, so it is refused with the accurate reason and the settlement headers still served."""
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))
    monkeypatch.setattr(
        grading_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(payer="")
    )

    response = grading_routes.x402_grade_submit(_request(body=_grade_body()))

    assert response.status_code == 400
    assert "no payer address" in response.description
    assert response.headers["PAYMENT-RESPONSE"] == "ok"
    assert store.list_graded_endpoints(limit=10) == []


@pytest.mark.usefixtures("ledger")
def test_the_service_refuses_an_unattributable_grade_even_if_a_route_forgets(
    store: InMemoryGradeStore,
) -> None:
    """The rule lives in the service too, so a future caller cannot store an unkeyable grade."""
    service = _service(store)
    with pytest.raises(GradingError, match="payer address"):
        _grade(service, grader="   ", score=4)


# --------------------------------------------------------------------------- #
# Credibility weighting
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("weights", "ledger")
def test_a_high_spend_wallet_moves_the_average_more_than_a_zero_history_one(
    store: InMemoryGradeStore, ledger: InMemorySettlementStore
) -> None:
    """The whole product: two opposite grades, and the wallet that has actually paid this marketplace pulls the published average its way."""
    _spent(ledger, payer=_PAYER, amount_atomic="500000")  # $0.50 spent with us
    service = _service(store)
    _grade(service, grader=_PAYER, score=5)  # the spender
    _grade(service, grader=_OTHER_PAYER, score=1)  # never paid us before

    aggregate = service.aggregate(_endpoint(service))

    # Unweighted, these two cancel out exactly.
    assert aggregate.mean == 3.0
    # Weighted: 5 x 510_000 + 1 x 10_000, over 520_000.
    assert aggregate.weighted_mean == 4.923
    assert aggregate.weighted_mean > aggregate.mean
    assert aggregate.total_weight == 520_000
    assert aggregate.weights_resolved is True
    by_grader = {item.grade.grader: item.weight for item in aggregate.grades}
    assert by_grader == {_PAYER: 510_000, _OTHER_PAYER: 10_000}


@pytest.mark.usefixtures("weights", "ledger")
def test_a_wallet_with_no_spending_history_still_carries_the_base_weight(
    store: InMemoryGradeStore,
) -> None:
    """A grade paid for by a wallet with no other history counts for something: a zero weight would silently delete paid work from the average."""
    service = _service(store)
    _grade(service, grader=_OTHER_PAYER, score=4)

    aggregate = service.aggregate(_endpoint(service))

    assert aggregate.grades[0].weight == 10_000
    assert aggregate.total_weight == 10_000
    # With one grader, weighting cannot move anything — but it must not erase
    # the grade either.
    assert aggregate.weighted_mean == 4.0
    assert aggregate.count == 1


@pytest.mark.usefixtures("weights", "ledger")
def test_one_wallet_cannot_buy_unlimited_influence(
    store: InMemoryGradeStore, ledger: InMemorySettlementStore
) -> None:
    """Weight is clamped, so credibility cannot simply be purchased outright by out-spending every honest grader."""
    _spent(ledger, payer=_PAYER, amount_atomic="50000000")  # $50 spent with us
    service = _service(store)
    _grade(service, grader=_PAYER, score=5)

    aggregate = service.aggregate(_endpoint(service))

    assert aggregate.grades[0].weight == 1_000_000


@pytest.mark.usefixtures("weights", "ledger")
def test_the_aggregate_serves_the_raw_signal_alongside_the_weighted_one(
    store: InMemoryGradeStore, ledger: InMemorySettlementStore
) -> None:
    """The plain mean, the count and the full distribution are served too — the weighting is an addition to the raw signal, never a replacement that hides it."""
    _spent(ledger, payer=_PAYER, amount_atomic="500000")
    service = _service(store)
    _grade(service, grader=_PAYER, score=5)
    _grade(service, grader=_OTHER_PAYER, score=1)
    _grade(service, grader=_THIRD_PAYER, score=3)

    aggregate = service.aggregate(_endpoint(service))

    assert aggregate.count == 3
    assert aggregate.mean == 3.0
    assert aggregate.distribution == {1: 1, 2: 0, 3: 1, 4: 0, 5: 1}
    assert aggregate.weighted_mean != aggregate.mean


@pytest.mark.usefixtures("weights", "ledger")
def test_testnet_and_mainnet_spend_are_never_summed_together(
    store: InMemoryGradeStore, ledger: InMemorySettlementStore
) -> None:
    """TestNet USDC is free from a dispenser, so only settlements on the configured network may count towards credibility."""
    _spent(ledger, payer=_PAYER, amount_atomic="70000")
    _spent(ledger, payer=_PAYER, amount_atomic="900000", network=_MAINNET)
    service = _service(store)
    _grade(service, grader=_PAYER, score=5)

    aggregate = service.aggregate(_endpoint(service))

    # Base + the TestNet row only; the MainNet row is not ours to count here.
    assert aggregate.grades[0].weight == 80_000


@pytest.mark.usefixtures("weights", "ledger")
def test_an_unreadable_ledger_falls_back_to_base_weights_and_says_so(
    store: InMemoryGradeStore,
) -> None:
    """A ledger outage must not produce a zero-weight wipeout or a NaN: every grade falls back to the base weight, the weighted mean degrades to the plain mean, and the paid response admits the weighting did not run."""
    service = _service(store, lookup=_UnreadableLedgerLookup())
    _grade(service, grader=_PAYER, score=5)
    _grade(service, grader=_OTHER_PAYER, score=2)

    aggregate = service.aggregate(_endpoint(service))

    assert aggregate.weights_resolved is False
    assert aggregate.weighted_mean == aggregate.mean == 3.5
    assert aggregate.total_weight == 20_000


@pytest.mark.usefixtures("weights", "ledger")
def test_the_aggregate_scan_is_bounded_and_says_so(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader who paid for a number is told when it is over a partial sample rather than being handed a silently truncated one."""
    monkeypatch.setattr(settings, "x402_grading_scan_limit", 2)
    service = _service(store)
    for index in range(4):
        _grade(service, grader=f"W{index}" + "X" * 56, score=4)

    aggregate = service.aggregate(_endpoint(service))

    assert aggregate.truncated is True
    assert aggregate.count == 2


@pytest.mark.usefixtures("weights", "ledger")
def test_the_grades_served_with_an_aggregate_are_capped(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The individual grades in a response are bounded even when the aggregate is over more of them."""
    monkeypatch.setattr(settings, "x402_grading_max_results", 2)
    service = _service(store)
    for index in range(5):
        _grade(service, grader=f"W{index}" + "X" * 56, score=4)

    aggregate = service.aggregate(_endpoint(service))

    assert aggregate.count == 5
    assert len(aggregate.grades) == 2


# --------------------------------------------------------------------------- #
# The ledger read behind the weights
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("weights")
def test_the_spend_lookup_reads_a_bounded_number_of_bounded_day_partitions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No unbounded read: the credibility sum is exactly lookback_days partition-key queries, each with a bound LIMIT, and never a filtered scan."""
    monkeypatch.setattr(settings, "x402_grading_spend_lookback_days", 3)
    monkeypatch.setattr(settings, "x402_grading_spend_scan_limit", 250)
    monkeypatch.setattr(cassandra_core, "prepare_cached", lambda cql: cql)
    session = _FakeCassandraSession(
        [SimpleNamespace(payer=_PAYER, amount_atomic="1000", network=ALGORAND_TESTNET_CAIP2)]
    )

    totals = CassandraSpendLookup(session_provider=lambda: session).spend_by_payer([_PAYER])

    assert len(session.calls) == 3
    for statement, params in session.calls:
        assert "WHERE day = ?" in statement
        assert "LIMIT ?" in statement
        assert "ALLOW FILTERING" not in statement
        assert params[1] == 250
    assert totals == {_PAYER: 3000}


@pytest.mark.usefixtures("weights")
def test_the_spend_lookup_costs_the_same_number_of_queries_however_many_graders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every grader of one endpoint is answered in ONE pass, so a paid aggregate's ledger cost does not scale with how many wallets graded it."""
    monkeypatch.setattr(settings, "x402_grading_spend_lookback_days", 2)
    monkeypatch.setattr(cassandra_core, "prepare_cached", lambda cql: cql)
    session = _FakeCassandraSession(
        [
            SimpleNamespace(payer=_PAYER, amount_atomic="1000", network=ALGORAND_TESTNET_CAIP2),
            SimpleNamespace(payer=_OTHER_PAYER, amount_atomic="7", network=ALGORAND_TESTNET_CAIP2),
            SimpleNamespace(
                payer="somebody-else", amount_atomic="99", network=ALGORAND_TESTNET_CAIP2
            ),
        ]
    )

    totals = CassandraSpendLookup(session_provider=lambda: session).spend_by_payer(
        [_PAYER, _OTHER_PAYER, _THIRD_PAYER]
    )

    assert len(session.calls) == 2
    # A payer with no rows is a real zero; a payer we did not ask about is not
    # summed in at all.
    assert totals == {_PAYER: 2000, _OTHER_PAYER: 14, _THIRD_PAYER: 0}


@pytest.mark.usefixtures("weights")
def test_an_unreadable_ledger_is_undeterminable_not_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed ledger read returns None, never zeros: "could not determine" must stay distinguishable from "never spent"."""
    monkeypatch.setattr(cassandra_core, "prepare_cached", lambda cql: cql)

    def _boom() -> Never:
        raise ConnectionError("cassandra down")

    assert CassandraSpendLookup(session_provider=_boom).spend_by_payer([_PAYER]) is None


@pytest.mark.usefixtures("weights")
def test_an_unparseable_settlement_amount_does_not_break_a_paid_read(
    ledger: InMemorySettlementStore,
) -> None:
    """A junk amount_atomic in the ledger is skipped and logged, not raised on a request the caller paid for."""
    _spent(ledger, payer=_PAYER, amount_atomic="not-a-number")
    _spent(ledger, payer=_PAYER, amount_atomic="4000")

    assert InMemorySpendLookup().spend_by_payer([_PAYER]) == {_PAYER: 4000}


# --------------------------------------------------------------------------- #
# The paid score lookup
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis", "weights", "ledger")
def test_the_paid_score_lookup_serves_the_weighted_aggregate(
    store: InMemoryGradeStore, ledger: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A settled score lookup returns the weighted mean, the raw mean, the count, the distribution and each grade's weight."""
    _spent(ledger, payer=_PAYER, amount_atomic="500000")
    service = _service(store)
    _grade(service, grader=_PAYER, score=5)
    _grade(service, grader=_OTHER_PAYER, score=1)
    monkeypatch.setattr(grading_routes, "grading_service", service)
    monkeypatch.setattr(
        grading_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )

    response = grading_routes.x402_grade_score(
        _request(method="GET", query={"url": _URL}, path="/api/v1/x402/grades/score")
    )

    assert response.status_code == 200
    payload = json.loads(response.description)
    assert payload["url"] == _URL
    assert payload["url_hash"] == url_hash(_URL)
    assert payload["weighted_mean"] == 4.923
    assert payload["mean"] == 3.0
    assert payload["count"] == 2
    assert payload["weights_resolved"] is True
    assert payload["distribution"] == {"1": 1, "2": 0, "3": 0, "4": 0, "5": 1}
    assert {grade["grader"]: grade["weight"] for grade in payload["grades"]} == {
        _PAYER: 510_000,
        _OTHER_PAYER: 10_000,
    }


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_the_score_lookup_of_an_ungraded_url_is_a_free_404(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody is charged for an empty aggregate — the existence check runs before the gate and leaks only what the free index already gives away."""
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))

    response = grading_routes.x402_grade_score(
        _request(method="GET", query={"url": _URL}, path="/api/v1/x402/grades/score")
    )

    assert response.status_code == 404
    assert "not_found" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
@pytest.mark.parametrize("query", [{}, {"url": ""}, {"url": "ftp://nope.example/x"}, {"url": "  "}])
def test_a_missing_or_malformed_score_url_is_rejected_before_the_payment_gate(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch, query: dict[str, Any]
) -> None:
    """A score lookup with no usable URL is a free 400, never a 402."""
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))

    response = grading_routes.x402_grade_score(
        _request(method="GET", query=query, path="/api/v1/x402/grades/score")
    )

    assert response.status_code == 400
    assert "invalid_request" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_a_graded_url_still_costs_a_payment_to_score(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The free existence check is not a free aggregate: once a URL has grades, reading them is paid."""
    service = _service(store)
    _grade(service, grader=_PAYER, score=5)
    monkeypatch.setattr(grading_routes, "grading_service", service)

    response = grading_routes.x402_grade_score(
        _request(method="GET", query={"url": _URL}, path="/api/v1/x402/grades/score")
    )

    assert response.status_code == 402


# --------------------------------------------------------------------------- #
# The free index
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis", "ledger")
def test_the_free_index_lists_graded_urls_with_no_scores(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Free is existence, paid is signal: the index says an endpoint has grades without giving the score away."""
    service = _service(store)
    _grade(service, grader=_PAYER, score=5, comment="excellent")
    monkeypatch.setattr(grading_routes, "grading_service", service)

    result = grading_routes.x402_grade_index(_request(method="GET"))

    assert len(result["items"]) == 1
    entry = result["items"][0]
    assert entry["url"] == _URL
    assert entry["url_hash"] == url_hash(_URL)
    assert entry["last_graded_at_epoch"] > 0
    assert set(entry) == {"url", "url_hash", "last_graded_at_epoch"}
    assert "excellent" not in json.dumps(result)


@pytest.mark.usefixtures("fake_redis", "ledger")
def test_the_free_index_limit_is_clamped_to_the_configured_maximum(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A caller cannot ask for an unbounded listing — the limit is clamped."""
    monkeypatch.setattr(settings, "x402_grading_max_results", 1)
    service = _service(store)
    for index in range(4):
        _grade(service, grader=_PAYER, score=3, url=f"https://api{index}.example.com/v1/quote")
    monkeypatch.setattr(grading_routes, "grading_service", service)

    result = grading_routes.x402_grade_index(_request(method="GET", query={"limit": "9999"}))

    assert len(result["items"]) == 1


@pytest.mark.usefixtures("fake_redis", "ledger")
def test_a_non_integer_index_limit_is_a_400(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-integer limit is rejected rather than silently ignored."""
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))

    result = grading_routes.x402_grade_index(_request(method="GET", query={"limit": "lots"}))

    assert result.status_code == 400
    assert "invalid_request" in result.description


@pytest.mark.usefixtures("fake_redis", "ledger")
def test_the_free_index_is_rate_limited_per_ip(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An IP over the hourly budget gets a 429; a different IP is unaffected."""
    monkeypatch.setattr(settings, "x402_grading_rate_limit_per_hour", 2)
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))

    def _read(ip: str) -> Response | dict:
        return grading_routes.x402_grade_index(_request(method="GET", headers={"X-Real-IP": ip}))

    assert "items" in _read("203.0.113.7")
    assert "items" in _read("203.0.113.7")
    assert _read("203.0.113.7").status_code == 429
    assert "items" in _read("203.0.113.9")


@pytest.mark.usefixtures("ledger")
def test_the_free_index_fails_open_when_redis_is_down(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Redis outage must not take the free index offline."""
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: _BrokenRedis())
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))

    result = grading_routes.x402_grade_index(
        _request(method="GET", headers={"X-Real-IP": "203.0.113.7"})
    )

    assert "items" in result
