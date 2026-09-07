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
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Never

import pytest

pytest.importorskip("x402")

from x402.mechanisms.avm.constants import (
    ALGORAND_MAINNET_CAIP2,
    ALGORAND_TESTNET_CAIP2,
    USDC_MAINNET_ASA_ID,
    USDC_TESTNET_ASA_ID,
)
from x402.schemas.payments import PaymentRequirements
from x402.schemas.responses import SupportedKind, SupportedResponse
from x402.schemas.v1 import PaymentRequirementsV1
from x402.server import x402ResourceServerSync

import app.modules.x402_grading as grading_package
from app.core import cassandra as cassandra_core
from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.x402 import circuit_breaker
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard
from app.modules.x402 import paid_request as paid_request_module
from app.modules.x402 import replay as replay_module
from app.modules.x402.assets import EURQ
from app.modules.x402.refund import RefundResult
from app.modules.x402.settlement import (
    InMemorySettlementStore,
    SettlementRecord,
    set_settlement_store,
)
from app.modules.x402_grading.api import routes as grading_routes
from app.modules.x402_grading.models.domain import GradedEndpoint, GradingError, StoredGrade
from app.modules.x402_grading.services import credibility as credibility_module
from app.modules.x402_grading.services.credibility import (
    CassandraSpendLookup,
    InMemorySpendLookup,
    SpendLookup,
)
from app.modules.x402_grading.services.grading_service import GradingService
from app.modules.x402_grading.services.url_key import url_hash
from app.modules.x402_grading.stores import cassandra as grading_cassandra_store
from app.modules.x402_grading.stores.memory import InMemoryGradeStore

_PAY_TO = "A" * 58
_PAYER = "P" * 58
_OTHER_PAYER = "Q" * 58
_THIRD_PAYER = "R" * 58

# An endpoint nobody has listed anywhere. That is the point: grading takes any
# http(s) URL, so every test grades something the directory has never heard of.
_URL = "https://api.unlisted-example.com/v1/quote"
_MAINNET = "algorand:wGHE2Pwdvd7S12BL5FaOP20EGYesN73k/QCSD3JhBfE="

# TestNet USDC's ASA id, as a string the way the settlement ledger's text
# column carries it. This is what _settled_result and _spent already paid
# with implicitly; naming it lets the new cross-asset tests below contrast it
# against EURQ's mainnet-only ASA id explicitly.
_USDC_TESTNET_ASSET_ID = str(USDC_TESTNET_ASA_ID)
_USDC_MAINNET_ASSET_ID = str(USDC_MAINNET_ASA_ID)
_EURQ_MAINNET_ASSET_ID = str(EURQ.asa_ids[ALGORAND_MAINNET_CAIP2])


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _FakeRedis:
    """Enough of the Redis API for the replay claim, the rate-limit counter, and the refund circuit breaker."""

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
@pytest.fixture(autouse=True)
def _already_paid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the pre-parse 402 for header-less requests: every route test here models a request that already carries a payment (the gate is stubbed, or run against the offline facilitator), so the unpaid challenge is out of scope. Its ordering has its own tests in tests/test_x402_unpaid_challenge.py."""
    monkeypatch.setattr(grading_routes, "challenge_if_unpaid", lambda *_a, **_kw: None)


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
    asset_id: str = _USDC_TESTNET_ASSET_ID,
) -> None:
    """Record that `payer` settled a payment with this marketplace for some product.

    Defaults to TestNet USDC, the asset every pre-existing test in this file
    implicitly paid in; `asset_id` is overridable so the cross-asset
    credibility tests can record a settlement in EURQ instead.
    """
    ledger.record_settlement(
        SettlementRecord(
            tx_id=f"PAID_{payer[:4]}_{amount_atomic}",
            asset_id=asset_id,
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
    usage_verified: bool = True,
    now: datetime | None = None,
) -> StoredGrade:
    """Submit one grade through the service, resolving the URL the way a route does.

    usage_verified defaults to True: every OTHER test in this file that goes
    through this helper predates the mandatory-usage-proof requirement and is
    testing something else entirely (credibility weighting, the leaderboard,
    the free index, ...) -- defaulting True keeps them all exercising exactly
    what they always exercised.
    """
    normalized, hashed = service.resolve_url(url)
    return service.submit(
        url=normalized,
        url_hash_value=hashed,
        grader=grader,
        score=score,
        comment=comment,
        settlement_tx_id=settlement_tx_id,
        usage_verified=usage_verified,
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
    """Swap every Redis seam for one in-process fake shared by replay, rate limiting and the refund circuit breaker."""
    client = _FakeRedis()
    monkeypatch.setattr(replay_module, "get_redis", lambda **_kw: client)
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda **_kw: client)
    # circuit_breaker.is_tripped reads app.core.redis_client's get_redis,
    # imported into circuit_breaker's own namespace -- patch it there, same
    # seam test_x402_preview.py already patches for ping's retrofit.
    monkeypatch.setattr(circuit_breaker, "get_redis", lambda **_kw: client)
    return client


@pytest.fixture
def weights(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the weighting knobs so the arithmetic in these tests is explicit."""
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_grading_base_weight_atomic", 10_000)
    monkeypatch.setattr(settings, "x402_grading_max_weight_atomic", 1_000_000)


_TX_ID = "A" * 52


def _grade_body(**overrides: object) -> bytes:
    body: dict[str, object] = {"url": _URL, "score": 4, "comment": "solid", "tx_id": _TX_ID}
    body.update(overrides)
    return json.dumps(body).encode()


def _mock_verified_proof(
    monkeypatch: pytest.MonkeyPatch, *, sender: str = _PAYER, verified: bool = True
) -> None:
    """Bypass the real usage-proof network/indexer work with a canned outcome.

    Every existing submit test predates the mandatory-usage-proof
    requirement and is not itself testing that mechanism (see
    test_x402_grading_usage_proof.py for that) -- this keeps them exercising
    what they always exercised without hitting the process-wide no-network
    guard (CLAUDE.md section 6).
    """
    monkeypatch.setattr(
        grading_routes,
        "_resolve_usage_proof",
        lambda *_a, **_kw: grading_routes._UsageProofOutcome(
            error=None, verified=verified, sender=sender
        ),
    )


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
    _mock_verified_proof(monkeypatch)

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
    _mock_verified_proof(monkeypatch)

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
    """A wallet with no history whatsoever, grading a URL nobody has listed, is stored on the strength of the payment plus a verified usage proof — there is no ELIGIBILITY check left to fail (only the mandatory proof)."""
    assert ledger.settlements == []  # nobody has ever paid us for anything
    service = _service(store)
    monkeypatch.setattr(grading_routes, "grading_service", service)
    monkeypatch.setattr(
        grading_routes, "require_paid_request", lambda *_a, **_kw: _settled_result()
    )
    _mock_verified_proof(monkeypatch)

    response = grading_routes.x402_grade_submit(
        _request(body=_grade_body(url="https://totally.unknown.example/api", score=5))
    )

    assert response.status_code == 200
    payload = json.loads(response.description)
    assert payload["url"] == "https://totally.unknown.example/api"
    assert payload["grade"]["grader"] == _PAYER
    assert payload["grade"]["score"] == 5
    assert payload["grade"]["usage_verified"] is True
    assert payload["settlement_tx_id"] == "TX123"
    stored = store.get(url_hash("https://totally.unknown.example/api"), _PAYER)
    assert stored is not None
    assert stored.score == 5
    assert stored.usage_verified is True


def test_the_grading_module_does_not_import_the_directory_outside_its_routes() -> None:
    """The decoupling is structural, not just behavioural: no file in this module may import x402_directory -- except api/routes.py.

    Parsed rather than grepped, so that prose EXPLAINING why the dependency was
    removed does not read as the dependency itself -- several docstrings here
    name x402_directory precisely to say this module does not use it. Only
    x402_grading's own files are checked: modules/x402 (the shared payment gate
    and ledger) is a deliberate and documented read-only dependency.

    The single allowed exception is api/routes.py, and only there: the paid
    tag leaderboard (GET /grades/top?tag=) was explicitly specified to take
    its candidate URLs from the directory's PUBLIC ListingService.search
    rather than from cross-module CQL. routes.py binds that one call into
    GradingService as a plain callable (TagCandidateLookup), so the service,
    stores and models remain directory-free and this test keeps guarding
    them; a second importing file, or an import of anything but the public
    service, is still a failure here.
    """
    module_root = Path(grading_package.__file__).parent
    allowed = {"api/routes.py"}
    offenders: list[str] = []
    for path in sorted(module_root.rglob("*.py")):
        if path.relative_to(module_root).as_posix() in allowed:
            continue
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

    # And the one allowed file may import only the directory's public service.
    routes_tree = ast.parse((module_root / "api" / "routes.py").read_text(encoding="utf-8"))
    directory_imports = [
        node.module
        for node in ast.walk(routes_tree)
        if isinstance(node, ast.ImportFrom) and "x402_directory" in (node.module or "")
    ]
    assert directory_imports == ["app.modules.x402_directory.services.listing_service"]


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
    _mock_verified_proof(monkeypatch)

    response = grading_routes.x402_grade_submit(_request(body=_grade_body()))

    assert response.status_code == 400
    assert "no payer address" in response.description
    assert response.headers["PAYMENT-RESPONSE"] == "ok"
    assert store.list_graded_endpoints(limit=10) == []


@pytest.mark.usefixtures("ledger", "fake_redis")
def test_grade_submit_never_forwards_promo_params(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test (2026-09-07 security review, finding 10 -- same pattern already found and fixed for x402_directory/x402_board): `grader` is a public identity statement stored with the grade, not merely payment attribution, and a promo redemption's wallet is only checked for syntactic validity, never proof of control (modules/x402/promo.py's own docstring). Worse than a plain spoof here: the tx_id sender-mismatch check only compares the claimed payer against the usage proof's public on-chain sender, so a promo caller could read that address straight off the chain and pass it as `?promo_wallet=`, sailing through the mismatch check while never controlling that wallet. Closed by never reading promo params on this route at all -- this test locks in that invariant: promo params in the query string are ignored, not honored."""
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))

    captured: dict = {}

    def _spy_require_paid_request(*_args: object, **kwargs: object) -> x402_guard.PaymentResult:
        captured.update(kwargs)
        return _settled_result()

    monkeypatch.setattr(grading_routes, "require_paid_request", _spy_require_paid_request)
    monkeypatch.setattr(grading_routes, "mark_fulfilled", lambda *_a, **_kw: None)
    _mock_verified_proof(monkeypatch)

    grading_routes.x402_grade_submit(
        _request(query={"promo": "LAUNCH50", "promo_wallet": _PAYER}, body=_grade_body())
    )

    assert "promo_code" not in captured
    assert "promo_wallet" not in captured


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
        [
            SimpleNamespace(
                payer=_PAYER,
                amount_atomic="1000",
                network=ALGORAND_TESTNET_CAIP2,
                asset_id=_USDC_TESTNET_ASSET_ID,
            )
        ]
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
            SimpleNamespace(
                payer=_PAYER,
                amount_atomic="1000",
                network=ALGORAND_TESTNET_CAIP2,
                asset_id=_USDC_TESTNET_ASSET_ID,
            ),
            SimpleNamespace(
                payer=_OTHER_PAYER,
                amount_atomic="7",
                network=ALGORAND_TESTNET_CAIP2,
                asset_id=_USDC_TESTNET_ASSET_ID,
            ),
            SimpleNamespace(
                payer="somebody-else",
                amount_atomic="99",
                network=ALGORAND_TESTNET_CAIP2,
                asset_id=_USDC_TESTNET_ASSET_ID,
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


@pytest.mark.usefixtures("ledger")
def test_spend_across_different_assets_is_normalized_before_summing_not_treated_as_equal_units(
    ledger: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this guards: atomic units of different assets are not commensurate.

    A wallet that pays 0.50 USDC and 0.50 EURQ has not spent "1.00" with us --
    EURQ is worth more than USDC per unit. Naively summing raw atomic amounts
    (the pre-fix behaviour) would give 500_000 + 500_000 = 1_000_000. The
    correct, price-normalized total prices the EURQ leg at its USD rate before
    adding it: 500_000 (USDC) + 540_000 (0.50 EURQ @ $1.08) = 1_040_000.
    """
    monkeypatch.setattr(settings, "x402_network", ALGORAND_MAINNET_CAIP2)
    monkeypatch.setattr(
        credibility_module,
        "get_usd_rate",
        lambda coingecko_id: Decimal("1.08") if coingecko_id == EURQ.coingecko_id else None,
    )
    _spent(
        ledger,
        payer=_PAYER,
        amount_atomic="500000",
        network=ALGORAND_MAINNET_CAIP2,
        asset_id=_USDC_MAINNET_ASSET_ID,
    )
    _spent(
        ledger,
        payer=_PAYER,
        amount_atomic="500000",
        network=ALGORAND_MAINNET_CAIP2,
        asset_id=_EURQ_MAINNET_ASSET_ID,
    )

    totals = InMemorySpendLookup().spend_by_payer([_PAYER])

    assert totals == {_PAYER: 1_040_000}


@pytest.mark.usefixtures("ledger")
def test_a_settlement_in_an_asset_with_no_available_price_right_now_is_excluded_and_logged(
    ledger: InMemorySettlementStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """price_oracle is fail-closed: a missing rate excludes that settlement.

    It is excluded from the credibility sum (never priced as zero-value,
    never crashes the read) and the exclusion is logged.
    """
    monkeypatch.setattr(settings, "x402_network", ALGORAND_MAINNET_CAIP2)
    monkeypatch.setattr(credibility_module, "get_usd_rate", lambda _coingecko_id: None)
    _spent(
        ledger,
        payer=_PAYER,
        amount_atomic="500000",
        network=ALGORAND_MAINNET_CAIP2,
        asset_id=_USDC_MAINNET_ASSET_ID,
    )
    _spent(
        ledger,
        payer=_PAYER,
        amount_atomic="999999",
        network=ALGORAND_MAINNET_CAIP2,
        asset_id=_EURQ_MAINNET_ASSET_ID,
    )

    with caplog.at_level(logging.WARNING):
        totals = InMemorySpendLookup().spend_by_payer([_PAYER])

    # Only the USDC leg counts; the unpriceable EURQ leg is excluded, not
    # summed in as zero-value spend and not summed in raw.
    assert totals == {_PAYER: 500_000}
    assert any("no USD rate available" in record.message for record in caplog.records)


@pytest.mark.usefixtures("ledger")
def test_a_settlement_in_an_unrecognized_asset_is_excluded_and_logged(
    ledger: InMemorySettlementStore,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An asset_id naming no currently accepted asset is excluded, not misread as USDC.

    Covers a stale/retired asset or a data error on this network -- it is
    excluded from the sum rather than silently priced as USDC.
    """
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    _spent(ledger, payer=_PAYER, amount_atomic="500000")
    _spent(ledger, payer=_PAYER, amount_atomic="123456", asset_id="999999999")

    with caplog.at_level(logging.WARNING):
        totals = InMemorySpendLookup().spend_by_payer([_PAYER])

    assert totals == {_PAYER: 500_000}
    assert any("not a currently accepted asset" in record.message for record in caplog.records)


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


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "weights", "ledger")
def test_grade_score_preview_serves_a_redacted_response_with_no_facilitator_call(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """?preview=true on a graded url gets the redacted shape, never the real aggregate."""
    service = _service(store)
    _grade(service, grader=_PAYER, score=5)
    _grade(service, grader=_OTHER_PAYER, score=1)
    monkeypatch.setattr(grading_routes, "grading_service", service)

    response = grading_routes.x402_grade_score(
        _request(
            method="GET",
            query={"url": _URL, "preview": "true"},
            path="/api/v1/x402/grades/score",
        )
    )

    assert response.status_code == 200
    payload = json.loads(response.description)
    assert payload["url"] == _URL
    assert payload["url_hash"] == url_hash(_URL)
    assert payload["count"] == -1
    assert payload["weighted_mean"] == 0.0
    assert payload["grades"] == []
    assert payload["settlement_tx_id"] == "<preview>"


@pytest.mark.usefixtures("ledger", "fake_redis")
def test_a_promo_redemption_reads_the_score_with_no_settlement(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pure-read paid route with no payer downstream: promo still returns the real aggregate, is never marked fulfilled, and carries via="promo"."""
    service = _service(store)
    _grade(service, grader=_PAYER, score=5)
    monkeypatch.setattr(grading_routes, "grading_service", service)
    monkeypatch.setattr(
        grading_routes,
        "require_paid_request",
        lambda *_a, **_kw: x402_guard.PaymentResult(error=None, is_promo=True),
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        grading_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)),
    )

    response = grading_routes.x402_grade_score(
        _request(
            method="GET",
            query={"url": _URL, "promo": "LAUNCH50", "promo_wallet": _PAYER},
            path="/api/v1/x402/grades/score",
        )
    )

    assert response.status_code == 200
    body = json.loads(response.description)
    assert body["count"] == 1
    assert body["settlement_tx_id"] == ""
    assert body["via"] == "promo"
    assert fulfilled == []


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


# --------------------------------------------------------------------------- #
# GET /grades/summary — free per-URL existence-tier summary
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("fake_redis", "ledger")
def test_the_free_summary_serves_count_and_recency_but_never_a_score(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Free is existence: how many graded and when, with no mean, distribution or opinion anywhere in the payload."""
    service = _service(store)
    _grade(service, grader=_PAYER, score=5, comment="excellent")
    _grade(service, grader=_OTHER_PAYER, score=1)
    monkeypatch.setattr(grading_routes, "grading_service", service)

    result = grading_routes.x402_grade_summary(
        _request(method="GET", query={"url": _URL}, path="/api/v1/x402/grades/summary")
    )

    assert set(result) == {"url", "url_hash", "count", "last_graded_at_epoch", "truncated"}
    assert result["count"] == 2
    assert result["last_graded_at_epoch"] > 0
    assert "mean" not in json.dumps(result)
    assert "excellent" not in json.dumps(result)


@pytest.mark.usefixtures("fake_redis", "ledger")
@pytest.mark.parametrize(
    ("query", "status"),
    [({}, 400), ({"url": "ftp://nope.example/x"}, 400), ({"url": _URL}, 404)],
)
def test_the_free_summary_rejects_bad_urls_and_404s_ungraded_ones(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch, query: dict[str, Any], status: int
) -> None:
    """A missing or unusable URL is a 400, an ungraded one a 404."""
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))

    result = grading_routes.x402_grade_summary(
        _request(method="GET", query=query, path="/api/v1/x402/grades/summary")
    )

    assert result.status_code == status


@pytest.mark.usefixtures("fake_redis", "ledger")
def test_the_free_summary_is_rate_limited_per_ip_on_its_own_counter(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An IP over the hourly summary budget gets a 429; the index budget is separate."""
    monkeypatch.setattr(settings, "x402_grading_rate_limit_per_hour", 1)
    service = _service(store)
    _grade(service, grader=_PAYER, score=5)
    monkeypatch.setattr(grading_routes, "grading_service", service)
    headers = {"X-Real-IP": "203.0.113.7"}

    def _summary() -> Response | dict:
        return grading_routes.x402_grade_summary(
            _request(method="GET", headers=headers, query={"url": _URL})
        )

    assert "count" in _summary()
    assert _summary().status_code == 429
    assert "items" in grading_routes.x402_grade_index(_request(method="GET", headers=headers))


# --------------------------------------------------------------------------- #
# GET /grades/top?tag= — paid tag leaderboard over directory listings
# --------------------------------------------------------------------------- #
_TAGGED = [
    "https://api.alpha.example/v1/quote",
    "https://api.beta.example/v1/quote",
    "https://api.gamma.example/v1/quote",
]


_LISTER = "L" * 58
_THIRD_PAYER = "R" * 58


def _directory_with(urls: list[str], *, tag: str = "pricing", payer: str = _LISTER) -> object:
    """A directory holding live listings of `urls` under `tag`, owned by `payer`, via the directory's public service."""
    from app.modules.x402_directory.models.domain import StoredListing
    from app.modules.x402_directory.services.listing_service import url_hash as dir_hash
    from app.modules.x402_directory.stores.memory import InMemoryListingStore

    listing_store = InMemoryListingStore()
    now = int(datetime.now(tz=UTC).timestamp())
    for index, url in enumerate(urls):
        listing_store.upsert(
            StoredListing(
                url_hash=dir_hash(url),
                url=url,
                price="$0.01",
                description="",
                schema_json="",
                settlement_tx_id=f"TXL{index}",
                term_end_epoch=now + 86400,
                created_at_epoch=now - index,
                tags=[tag],
                payer=payer,
            )
        )
    return listing_store


def _leaderboard_service(store: InMemoryGradeStore, listing_store: object) -> GradingService:
    from app.modules.x402_directory.services.listing_service import ListingService

    directory = ListingService(listing_store)  # type: ignore[arg-type]
    return GradingService(
        store,
        lookup=InMemorySpendLookup(),
        tag_lookup=lambda tag, limit: [
            (i.url, i.payer) for i in directory.search(limit=limit, tag=tag)
        ],
    )


def _must_not_charge(*_args: object, **_kwargs: object) -> Never:
    raise AssertionError("the payment gate must not run for a doomed leaderboard request")


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
@pytest.mark.parametrize("query", [{}, {"tag": ""}, {"tag": "   "}, {"tag": "x" * 65}])
def test_a_missing_or_unusable_tag_is_a_400_before_the_gate(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch, query: dict[str, Any]
) -> None:
    """The directory's tag validation runs pre-gate and its error maps to a free 400."""
    monkeypatch.setattr(
        grading_routes, "grading_service", _leaderboard_service(store, _directory_with([]))
    )
    monkeypatch.setattr(grading_routes, "require_paid_request", _must_not_charge)

    response = grading_routes.x402_grade_top(
        _request(method="GET", query=query, path="/api/v1/x402/grades/top")
    )

    assert response.status_code == 400
    assert "invalid_request" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_a_tag_with_no_graded_listing_is_a_404_before_the_gate(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Listed but ungraded, or an unknown tag: nobody pays for an empty leaderboard."""
    monkeypatch.setattr(
        grading_routes, "grading_service", _leaderboard_service(store, _directory_with(_TAGGED))
    )
    monkeypatch.setattr(grading_routes, "require_paid_request", _must_not_charge)

    for tag in ("pricing", "unknown-tag"):
        response = grading_routes.x402_grade_top(
            _request(method="GET", query={"tag": tag}, path="/api/v1/x402/grades/top")
        )
        assert response.status_code == 404
        assert "not_found" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_a_graded_tag_costs_the_score_price_and_declares_discovery(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once a tag has a graded listing, the leaderboard is a 402 at the score price with a query-params Bazaar extension."""
    from x402.http.utils import decode_payment_required_header

    monkeypatch.setattr(settings, "x402_grading_score_price", "$0.03")
    service = _leaderboard_service(store, _directory_with(_TAGGED))
    _grade(service, grader=_PAYER, score=4, url=_TAGGED[0])
    _grade(service, grader=_OTHER_PAYER, score=4, url=_TAGGED[0])
    monkeypatch.setattr(grading_routes, "grading_service", service)

    response = grading_routes.x402_grade_top(
        _request(method="GET", query={"tag": "pricing"}, path="/api/v1/x402/grades/top")
    )

    assert response.status_code == 402
    payment_required = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    assert payment_required.accepts[0].amount == "30000"
    bazaar = (payment_required.extensions or {}).get("bazaar")
    assert bazaar is not None
    assert "body" not in json.dumps(bazaar)


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_grade_top_preview_serves_a_redacted_response_never_ranking_the_real_candidates(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """?preview=true on a rankable tag gets a redacted exemplar row, never the real leaderboard order."""
    service = _leaderboard_service(store, _directory_with(_TAGGED))
    _grade(service, grader=_PAYER, score=4, url=_TAGGED[0])
    _grade(service, grader=_OTHER_PAYER, score=4, url=_TAGGED[0])
    monkeypatch.setattr(grading_routes, "grading_service", service)

    response = grading_routes.x402_grade_top(
        _request(
            method="GET",
            query={"tag": "pricing", "preview": "true"},
            path="/api/v1/x402/grades/top",
        )
    )

    assert response.status_code == 200
    payload = json.loads(response.description)
    assert payload["tag"] == "pricing"
    assert payload["candidates_considered"] == -1
    assert payload["items"] == [
        {
            "rank": 1,
            "url_hash": "<preview>",
            "url": "<preview>",
            "count": -1,
            "weighted_mean": 0.0,
            "mean": 0.0,
            "total_weight": 0,
            "truncated": False,
        }
    ]
    assert payload["settlement_tx_id"] == "<preview>"


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "weights")
def test_the_paid_leaderboard_ranks_tagged_listings_by_weighted_mean_and_marks_fulfilled(
    store: InMemoryGradeStore, ledger: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only tag-listed, graded endpoints appear, ordered by weighted mean, without per-grader rows; the settlement is marked fulfilled after the read."""
    _spent(ledger, payer=_PAYER, amount_atomic="500000")
    service = _leaderboard_service(store, _directory_with(_TAGGED))
    # alpha: spender says 5, a fresh wallet says 5 -> weighted 5.0 (two
    # independent grades, the leaderboard's rankability floor)
    _grade(service, grader=_PAYER, score=5, url=_TAGGED[0])
    _grade(service, grader=_THIRD_PAYER, score=5, url=_TAGGED[0])
    # beta: spender 2, fresh wallet 5 -> weighted well below 5, plain mean 3.5
    _grade(service, grader=_PAYER, score=2, url=_TAGGED[1])
    _grade(service, grader=_OTHER_PAYER, score=5, url=_TAGGED[1], comment="secret opinion")
    # gamma is listed but ungraded; _URL is graded but not listed under the tag.
    _grade(service, grader=_PAYER, score=5, url=_URL)
    monkeypatch.setattr(grading_routes, "grading_service", service)
    monkeypatch.setattr(
        grading_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXT")
    )
    fulfilled: list[tuple[str, str]] = []
    monkeypatch.setattr(
        grading_routes,
        "mark_fulfilled",
        lambda txid, *, resource: fulfilled.append((txid, resource)) or True,
    )

    response = grading_routes.x402_grade_top(
        _request(method="GET", query={"tag": "pricing"}, path="/api/v1/x402/grades/top")
    )

    assert response.status_code == 200
    payload = json.loads(response.description)
    assert payload["tag"] == "pricing"
    assert [item["url"] for item in payload["items"]] == [_TAGGED[0], _TAGGED[1]]
    assert [item["rank"] for item in payload["items"]] == [1, 2]
    assert payload["items"][0]["weighted_mean"] == 5.0
    assert payload["items"][1]["mean"] == 3.5
    assert payload["items"][1]["weighted_mean"] < 3.5
    assert payload["items"][1]["count"] == 2
    assert payload["weights_resolved"] is True
    assert payload["candidates_considered"] == 2
    assert "grades" not in payload["items"][0]
    assert "secret opinion" not in response.description
    assert payload["settlement_tx_id"] == "TXT"
    assert fulfilled == [("TXT", "x402-grading-top")]


@pytest.mark.usefixtures("weights", "ledger")
def test_the_leaderboard_candidate_set_is_bounded(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """At most TOP_CANDIDATE_LIMIT listings are considered, however many carry the tag."""
    from app.modules.x402_grading.services import grading_service as service_module

    monkeypatch.setattr(service_module, "TOP_CANDIDATE_LIMIT", 2)
    urls = [f"https://api{i}.example/v1/q" for i in range(5)]
    service = _leaderboard_service(store, _directory_with(urls))
    for url in urls:
        _grade(service, grader=_PAYER, score=4, url=url)

    assert len(service.graded_candidates_for_tag("pricing")) == 2


@pytest.mark.usefixtures("weights", "ledger")
def test_aggregate_many_resolves_every_graders_weight_in_one_ledger_lookup(
    store: InMemoryGradeStore,
) -> None:
    """The leaderboard's ledger cost does not scale with the number of endpoints."""
    calls: list[list[str]] = []

    class _CountingLookup:
        def spend_by_payer(self, payers: list[str]) -> dict[str, int]:
            calls.append(list(payers))
            return dict.fromkeys(payers, 0)

    service = GradingService(store, lookup=_CountingLookup())
    for url in _TAGGED:
        _grade(service, grader=_PAYER, score=4, url=url)
        _grade(service, grader=_OTHER_PAYER, score=2, url=url)

    aggregates = service.aggregate_many([_endpoint(service, url) for url in _TAGGED])

    assert len(aggregates) == 3
    assert len(calls) == 1
    assert sorted(calls[0]) == sorted([_PAYER, _OTHER_PAYER])
    assert all(item.mean == 3.0 for item in aggregates)


def test_a_service_without_a_tag_lookup_cannot_serve_leaderboards(
    store: InMemoryGradeStore,
) -> None:
    """No directory bound means no leaderboard, said explicitly rather than an empty board."""
    with pytest.raises(GradingError, match="not available"):
        _service(store).graded_candidates_for_tag("pricing")


# --------------------------------------------------------------------------- #
# Leaderboard anti-gaming: self-grades and the rankability floor
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("weights", "ledger")
def test_a_listers_own_grade_of_their_own_listing_does_not_count_on_the_leaderboard(
    store: InMemoryGradeStore,
) -> None:
    """The listing owner's grade still shows in the per-URL score, but the ranking ignores it."""
    service = _leaderboard_service(store, _directory_with([_TAGGED[0]], payer=_PAYER))
    _grade(service, grader=_PAYER, score=5, url=_TAGGED[0])
    _grade(service, grader=_OTHER_PAYER, score=2, url=_TAGGED[0])
    _grade(service, grader=_THIRD_PAYER, score=2, url=_TAGGED[0])

    ranked = service.rank_leaderboard(service.leaderboard_candidates("pricing"))

    assert len(ranked) == 1
    assert ranked[0].count == 2
    assert ranked[0].mean == 2.0
    # The paid per-URL lookup is not a ranking and keeps the owner's opinion.
    assert service.aggregate(_endpoint(service, _TAGGED[0])).count == 3


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "weights", "ledger")
def test_an_endpoint_with_fewer_than_two_independent_grades_is_unranked_and_a_free_404(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One grade (or the lister's plus one) cannot buy the top slot: unranked, and the gate never runs."""
    from app.modules.x402_grading.services.grading_service import MIN_LEADERBOARD_GRADES

    assert MIN_LEADERBOARD_GRADES == 2
    service = _leaderboard_service(store, _directory_with(_TAGGED, payer=_PAYER))
    # alpha: only the lister -> 0 eligible. beta: lister + one -> 1 eligible.
    _grade(service, grader=_PAYER, score=5, url=_TAGGED[0])
    _grade(service, grader=_PAYER, score=5, url=_TAGGED[1])
    _grade(service, grader=_OTHER_PAYER, score=5, url=_TAGGED[1])
    assert service.leaderboard_candidates("pricing") == []
    monkeypatch.setattr(grading_routes, "grading_service", service)
    monkeypatch.setattr(grading_routes, "require_paid_request", _must_not_charge)

    response = grading_routes.x402_grade_top(
        _request(method="GET", query={"tag": "pricing"}, path="/api/v1/x402/grades/top")
    )

    assert response.status_code == 404
    # A second independent grade on beta makes it rankable.
    _grade(service, grader=_THIRD_PAYER, score=3, url=_TAGGED[1])
    candidates = service.leaderboard_candidates("pricing")
    assert [c.endpoint.url for c in candidates] == [_TAGGED[1]]
    assert len(candidates[0].rows) == 2


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_the_leaderboards_pre_gate_scan_is_rate_limited_per_ip_before_any_lookup(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Over budget is a 429 before the directory or any grade partition is read, gate untouched."""
    monkeypatch.setattr(settings, "x402_grading_rate_limit_per_hour", 1)
    lookups: list[str] = []

    def _lookup(tag: str, _limit: int) -> list[tuple[str, str]]:
        lookups.append(tag)
        return []

    monkeypatch.setattr(
        grading_routes, "grading_service", GradingService(store, tag_lookup=_lookup)
    )
    monkeypatch.setattr(grading_routes, "require_paid_request", _must_not_charge)
    headers = {"X-Real-IP": "203.0.113.9"}

    def _top() -> Response:
        return grading_routes.x402_grade_top(
            _request(
                method="GET",
                headers=headers,
                query={"tag": "pricing"},
                path="/api/v1/x402/grades/top",
            )
        )

    assert _top().status_code == 404
    assert lookups == ["pricing"]
    response = _top()
    assert response.status_code == 429
    assert "rate_limited" in response.description
    assert lookups == ["pricing"]
    # Its own counter: the free index is still within budget for this IP.
    assert "items" in grading_routes.x402_grade_index(_request(method="GET", headers=headers))


# --------------------------------------------------------------------------- #
# Probe / self wallets are excluded from every number, in code
# --------------------------------------------------------------------------- #
_PROBE = "B" * 58


@pytest.mark.usefixtures("weights", "ledger")
def test_a_probe_payers_grade_is_stored_but_counted_nowhere(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe's grade lands as a row; aggregate, summary and leaderboard all ignore it."""
    monkeypatch.setattr(settings, "x402_probe_payers", f" {_PROBE.lower()} , ")
    service = _leaderboard_service(store, _directory_with([_TAGGED[0]]))
    _grade(service, grader=_PROBE, score=5, url=_TAGGED[0])
    _grade(service, grader=_PAYER, score=1, url=_TAGGED[0])
    _grade(service, grader=_OTHER_PAYER, score=1, url=_TAGGED[0])

    _, hashed = service.resolve_url(_TAGGED[0])
    assert store.get(hashed, _PROBE) is not None
    endpoint = _endpoint(service, _TAGGED[0])
    assert service.summary(endpoint).count == 2
    aggregate = service.aggregate(endpoint)
    assert aggregate.count == 2
    assert aggregate.mean == 1.0
    assert all(g.grade.grader != _PROBE for g in aggregate.grades)
    ranked = service.rank_leaderboard(service.leaderboard_candidates("pricing"))
    assert ranked[0].count == 2
    assert ranked[0].mean == 1.0


@pytest.mark.usefixtures("weights")
def test_a_probe_payers_spend_earns_no_credibility_weight(
    ledger: InMemorySettlementStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ledger spend by our own wallet reads as 0 from both lookups, not as influence."""
    monkeypatch.setattr(settings, "x402_probe_payers", _PROBE)
    _spent(ledger, payer=_PROBE, amount_atomic="900000")
    _spent(ledger, payer=_PAYER, amount_atomic="900000")

    assert InMemorySpendLookup().spend_by_payer([_PROBE, _PAYER]) == {_PROBE: 0, _PAYER: 900000}

    day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    session = _FakeCassandraSession(
        rows=[
            SimpleNamespace(
                payer=_PROBE,
                amount_atomic="900000",
                network=ALGORAND_TESTNET_CAIP2,
                asset_id=_USDC_TESTNET_ASSET_ID,
                day=day,
            )
        ]
    )
    cassandra_lookup = CassandraSpendLookup(session_provider=lambda: session)
    assert cassandra_lookup.spend_by_payer([_PROBE]) == {_PROBE: 0}
    # Nothing to sum for -> no ledger reads at all.
    assert session.calls == []


# --------------------------------------------------------------------------- #
# Admin delete
# --------------------------------------------------------------------------- #
def _delete_request(url: str, grader: str, headers: dict[str, str] | None = None) -> Request:
    return _request(
        method="DELETE",
        headers=headers,
        query={"url": url, "grader": grader},
        path="/api/v1/admin/x402/grades",
    )


@pytest.mark.usefixtures("ledger")
def test_admin_grade_delete_without_admin_session_is_rejected(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real require_admin_wallet runs first: unconfigured allowlist -> refused, nothing deleted."""
    service = _service(store)
    _grade(service, grader=_PAYER, score=4)
    monkeypatch.setattr(grading_routes, "grading_service", service)

    response = grading_routes.x402_admin_delete_grade(
        _delete_request(_URL, _PAYER, headers={"X-Admin-Wallet": _PAYER})
    )

    assert getattr(response, "status_code", 200) != 200
    assert store.get(url_hash(service.resolve_url(_URL)[0]), _PAYER) is not None


@pytest.mark.usefixtures("ledger")
def test_admin_grade_delete_removes_the_grade_and_the_index_entry_with_the_last_one(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An authorized delete removes one grader's row; the URL leaves the free index only when empty."""
    service = _service(store)
    _grade(service, grader=_PAYER, score=4)
    _grade(service, grader=_OTHER_PAYER, score=2)
    monkeypatch.setattr(grading_routes, "grading_service", service)
    monkeypatch.setattr(grading_routes, "require_admin_wallet", lambda _request: None)
    hashed = service.resolve_url(_URL)[1]

    # Host case differs from the stored URL: the admin route normalizes the same way the write did.
    response = grading_routes.x402_admin_delete_grade(
        _delete_request(_URL.replace("https://api.", "HTTPS://API."), _PAYER)
    )
    assert response == {"deleted": True, "url_hash": hashed, "grader": _PAYER}
    assert store.get(hashed, _PAYER) is None
    assert service.graded_endpoint(hashed) is not None

    assert grading_routes.x402_admin_delete_grade(_delete_request(_URL, _OTHER_PAYER))["deleted"]
    assert service.graded_endpoint(hashed) is None
    assert service.list_graded(limit=10) == []


@pytest.mark.usefixtures("ledger")
def test_admin_grade_delete_of_a_missing_grade_or_bad_input_is_a_4xx(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing to delete is a 404; a missing grader or an ungradeable url is a 400."""
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))
    monkeypatch.setattr(grading_routes, "require_admin_wallet", lambda _request: None)

    assert grading_routes.x402_admin_delete_grade(_delete_request(_URL, _PAYER)).status_code == 404
    assert grading_routes.x402_admin_delete_grade(_delete_request(_URL, "")).status_code == 400
    assert (
        grading_routes.x402_admin_delete_grade(_delete_request("ftp://x", _PAYER)).status_code
        == 400
    )


# --------------------------------------------------------------------------- #
# Auto-refund + circuit breaker retrofit (grade submit, score, top)
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_a_tripped_breaker_blocks_every_paid_grading_route_before_the_gate(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Once tripped, submit/score/top all refuse with a 503 before require_paid_request ever runs."""
    service = _leaderboard_service(store, _directory_with(_TAGGED))
    _grade(service, grader=_PAYER, score=4, url=_TAGGED[0])
    _grade(service, grader=_OTHER_PAYER, score=4, url=_TAGGED[0])
    monkeypatch.setattr(grading_routes, "grading_service", service)
    monkeypatch.setattr(grading_routes, "require_paid_request", _must_not_charge)
    for resource in ("x402-grading-submit", "x402-grading-score", "x402-grading-top"):
        for _ in range(settings.x402_refund_breaker_max_failures):
            circuit_breaker.record_refund_failure(resource)

    submit_response = grading_routes.x402_grade_submit(
        _request(method="POST", body=_grade_body(url=_TAGGED[0]), path="/api/v1/x402/grades")
    )
    score_response = grading_routes.x402_grade_score(
        _request(method="GET", query={"url": _TAGGED[0]}, path="/api/v1/x402/grades/score")
    )
    top_response = grading_routes.x402_grade_top(
        _request(method="GET", query={"tag": "pricing"}, path="/api/v1/x402/grades/top")
    )

    for response in (submit_response, score_response, top_response):
        assert response.status_code == 503
        assert "temporarily_disabled" in response.description


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "ledger")
def test_grade_submit_write_failure_after_payment_triggers_a_refund(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A storage failure AFTER the gate settles refunds the payer instead of a bare 500 -- the sender-mismatch/unattributable-payer rejections above the write are unaffected (checked pre-write, not wrapped)."""
    monkeypatch.setattr(grading_routes, "grading_service", _service(store))
    monkeypatch.setattr(
        grading_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXF1")
    )
    _mock_verified_proof(monkeypatch)
    monkeypatch.setattr(
        grading_routes.grading_service,
        "submit",
        lambda **_kw: (_ for _ in ()).throw(RuntimeError("cassandra write blew up")),
    )
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: RefundResult(status="sent", txid="REFUND1"),
    )

    response = grading_routes.x402_grade_submit(
        _request(method="POST", body=_grade_body(), path="/api/v1/x402/grades")
    )

    assert response.status_code == 503
    assert "refunded" in response.description.lower()
    assert circuit_breaker.is_tripped("x402-grading-submit") is False  # one failure, below default


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "weights", "ledger")
def test_grade_score_compute_failure_after_payment_triggers_a_refund(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An aggregation failure AFTER the gate settles refunds the payer instead of a bare 500."""
    service = _service(store)
    _grade(service, grader=_PAYER, score=5)
    monkeypatch.setattr(grading_routes, "grading_service", service)
    monkeypatch.setattr(
        grading_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXF2")
    )
    monkeypatch.setattr(
        grading_routes.grading_service,
        "aggregate",
        lambda _endpoint: (_ for _ in ()).throw(RuntimeError("aggregation blew up")),
    )
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: RefundResult(status="sent", txid="REFUND2"),
    )

    response = grading_routes.x402_grade_score(
        _request(method="GET", query={"url": _URL}, path="/api/v1/x402/grades/score")
    )

    assert response.status_code == 503
    assert "refunded" in response.description.lower()


@pytest.mark.usefixtures("testnet_settings", "fake_redis", "weights", "ledger")
def test_grade_top_rank_failure_after_payment_triggers_a_refund(
    store: InMemoryGradeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A leaderboard-ranking failure AFTER the gate settles refunds the payer instead of a bare 500."""
    service = _leaderboard_service(store, _directory_with(_TAGGED))
    _grade(service, grader=_PAYER, score=4, url=_TAGGED[0])
    _grade(service, grader=_OTHER_PAYER, score=4, url=_TAGGED[0])
    monkeypatch.setattr(grading_routes, "grading_service", service)
    monkeypatch.setattr(
        grading_routes, "require_paid_request", lambda *_a, **_kw: _settled_result(txid="TXF3")
    )
    monkeypatch.setattr(
        grading_routes.grading_service,
        "rank_leaderboard",
        lambda _candidates: (_ for _ in ()).throw(RuntimeError("ranking blew up")),
    )
    monkeypatch.setattr(
        paid_request_module,
        "send_refund",
        lambda **_kw: RefundResult(status="sent", txid="REFUND3"),
    )

    response = grading_routes.x402_grade_top(
        _request(method="GET", query={"tag": "pricing"}, path="/api/v1/x402/grades/top")
    )

    assert response.status_code == 503
    assert "refunded" in response.description.lower()


def test_cassandra_epoch_treats_a_naive_driver_datetime_as_utc() -> None:
    """_epoch must treat a timezone-naive datetime as UTC, not the interpreter's local zone.

    That's what the real Cassandra driver actually returns for a `timestamp` column
    (x402_grades.created_at). Same bug class root-caused 2026-09-03 in
    x402_social/stores/cassandra.py: `value.timestamp()` on a naive datetime assumes the
    *local* system zone -- on a UTC+2 host, "13:18:17 wall-clock, no tzinfo" is silently
    read as 11:18:17 UTC, 2 hours off from the real UTC value that was actually stored.

    Constructs the naive datetime explicitly rather than relying on this test's own
    execution environment happening to run in a non-UTC zone (which would make the bug
    invisible in CI).
    """
    naive = datetime(2026, 9, 4, 13, 18, 17)  # noqa: DTZ001 -- naive on purpose, see docstring
    assert naive.tzinfo is None
    assert grading_cassandra_store._epoch(naive) == 1788527897  # the correct UTC epoch
    assert grading_cassandra_store._epoch(None) == 0
