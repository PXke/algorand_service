"""challenge_if_unpaid: an unpaid request sees the 402 offer before any input is parsed.

Found live 2026-09-05: every body-taking paid route (and every paid GET with
a required query param) answered a header-less request with a 400 from input
validation and never emitted its offer -- so a standard x402 client's opening
request, or an external prober's bare request, never learned the price.

Three layers, all offline (stub facilitator, no Redis, no Cassandra):

1. The primitive itself: no header -> the real 402 offer; a payment header,
   a preview or a promo request -> None, without touching the resource
   server at all.
2. Every reordered route, with a sentinel challenge: the handler must return
   the sentinel for an EMPTY request (no body, no query string), which
   proves the challenge runs before whatever parse/validation used to answer
   400 first.
3. One route end to end against the offline facilitator: an empty body with
   no header yields the real offer at the route's configured price.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, Never

import pytest

pytest.importorskip("x402")

from x402.http.constants import PAYMENT_SIGNATURE_HEADER
from x402.http.utils import decode_payment_required_header
from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2
from x402.schemas.payments import PaymentRequirements
from x402.schemas.responses import SupportedKind, SupportedResponse
from x402.schemas.v1 import PaymentRequirementsV1
from x402.server import x402ResourceServerSync

from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.kya.api import routes as kyc_routes
from app.modules.x402 import circuit_breaker, paid_request
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard
from app.modules.x402_board.api import routes as board_routes
from app.modules.x402_directory.api import routes as directory_routes
from app.modules.x402_features.api import routes as features_routes
from app.modules.x402_grading.api import routes as grading_routes
from app.modules.x402_news.api import routes as news_routes
from app.modules.x402_scan.api import routes as scan_routes
from app.modules.x402_social.api import routes as social_routes
from app.modules.x402_storage.api import routes as storage_routes
from app.modules.x402_uptime.api import routes as uptime_routes

_PAY_TO = "A" * 58

# What a reordered route must hand back untouched when the challenge fires:
# a distinct object, so identity (not just status) proves the route returned
# the challenge itself rather than some other 402 of its own.
_SENTINEL = Response(status_code=402, headers={"X-Sentinel": "challenge"}, description="{}")


class _StubFacilitator:
    """Canned /supported; verify()/settle() must never run for a header-less request."""

    def get_supported(self) -> SupportedResponse:
        return SupportedResponse(
            kinds=[SupportedKind(x402_version=2, scheme="exact", network=ALGORAND_TESTNET_CAIP2)]
        )

    def verify(
        self, _payload: dict, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("verify() must not be called for a header-less request")

    def settle(
        self, _payload: dict, _requirements: PaymentRequirements | PaymentRequirementsV1
    ) -> Never:
        raise AssertionError("settle() must not be called for a header-less request")


def _stub_resource_server() -> x402ResourceServerSync:
    server = x402ResourceServerSync(_StubFacilitator())
    x402_client.register_tagged_exact_avm_scheme(server, ALGORAND_TESTNET_CAIP2)
    server.initialize()
    return server


def _request(
    method: str = "POST",
    *,
    path: str = "/api/v1/x402/list",
    query: dict[str, Any] | None = None,
    path_params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
    body: bytes = b"",
) -> Request:
    return Request(
        method=method,
        headers=headers or {},
        query_params=QueryParams(query or {}),
        path_params=path_params or {},
        body=body,
        url=SimpleNamespace(scheme="https", host="localhost", path=path),
    )


@pytest.fixture
def testnet_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the gate at TestNet and the offline stub facilitator."""
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", _PAY_TO)
    monkeypatch.setattr(x402_guard, "get_resource_server", _stub_resource_server)


@pytest.fixture(autouse=True)
def _breaker_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every reordered route checks its circuit breaker before the challenge; keep it closed without Redis."""
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)


# --------------------------------------------------------------------------- #
# 1. The primitive
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings")
def test_a_header_less_request_gets_the_real_offer_without_any_input() -> None:
    """No payment header, no body, no query string: the 402 offer comes back with the price, the challenge tag and the advertised route template."""
    challenge = paid_request.challenge_if_unpaid(
        _request(),
        price="$0.10",
        resource="x402-test",
        description="A test offer.",
        resource_path="/api/v1/x402/test/{id}",
    )

    assert challenge is not None
    assert challenge.status_code == 402
    offer = decode_payment_required_header(challenge.headers["PAYMENT-REQUIRED"])
    assert offer.accepts[0].amount == "100000"
    assert offer.accepts[0].extra["tag"] == x402_client.CHALLENGE_TAG
    assert offer.resource.url.endswith("/api/v1/x402/test/{id}")
    assert "A test offer." in (offer.resource.description or "")


def _must_not_build_a_resource_server() -> Never:
    raise AssertionError("challenge_if_unpaid must not touch the resource server here")


@pytest.mark.parametrize(
    "request_kwargs",
    [
        pytest.param({"headers": {PAYMENT_SIGNATURE_HEADER: "a-payment"}}, id="payment-header"),
        pytest.param({"headers": {"payment-signature": "a-payment"}}, id="lowercase-header"),
        pytest.param({"query": {"preview": "true"}}, id="preview"),
        pytest.param({"query": {"promo": "CODE", "promo_wallet": _PAY_TO}}, id="promo"),
    ],
)
def test_a_paid_preview_or_promo_request_is_not_challenged(
    monkeypatch: pytest.MonkeyPatch, request_kwargs: dict[str, Any]
) -> None:
    """A request that carries a payment, or asks for a preview or a promo redemption, gets None -- and the resource server is never even built, so this costs nothing."""
    monkeypatch.setattr(x402_guard, "get_resource_server", _must_not_build_a_resource_server)

    assert (
        paid_request.challenge_if_unpaid(
            _request(**request_kwargs), price="$0.10", resource="x402-test"
        )
        is None
    )


# --------------------------------------------------------------------------- #
# 2. Every reordered route, with a sentinel challenge
# --------------------------------------------------------------------------- #
def _post_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        social_routes,
        "post_service",
        SimpleNamespace(get=lambda _id: SimpleNamespace(deleted=False, hidden_platform=False)),
    )


def _case_is_open(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        social_routes,
        "moderation_service",
        SimpleNamespace(get_case=lambda _id: SimpleNamespace(state=social_routes.CASE_STATE_OPEN)),
    )


def _no_rate_limit(module: object, name: str) -> Callable[[pytest.MonkeyPatch], None]:
    def _apply(monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(module, name, lambda _request: False)

    return _apply


def _nothing(_monkeypatch: pytest.MonkeyPatch) -> None:
    return None


def _storage_backup_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Storage renew's price depends on a real backup lookup, so the challenge only fires once one resolves: stub `backup_service.get` to find one, not at its remaining-term cap."""
    monkeypatch.setattr(
        storage_routes,
        "backup_service",
        SimpleNamespace(
            get=lambda _wallet, _backup_id: SimpleNamespace(status="active", size_bytes=1024)
        ),
    )
    monkeypatch.setattr(storage_routes, "at_remaining_cap", lambda _backup: False)


_ROUTES: list[tuple[str, object, str, dict[str, Any], Callable[[pytest.MonkeyPatch], None]]] = [
    ("directory list", directory_routes, "x402_list", {}, _nothing),
    (
        "directory renew",
        directory_routes,
        "x402_renew",
        {"path": "/api/v1/x402/list/renew"},
        _nothing,
    ),
    (
        "directory probe leaderboard",
        directory_routes,
        "x402_probe_leaderboard",
        {"method": "GET", "path": "/api/v1/x402/directory/probe/leaderboard"},
        _nothing,
    ),
    ("board place", board_routes, "x402_board_place", {"path": "/api/v1/x402/board"}, _nothing),
    (
        "features demand",
        features_routes,
        "x402_features_demand",
        {"method": "GET", "path": "/api/v1/x402/features/demand"},
        _nothing,
    ),
    (
        "grade submit",
        grading_routes,
        "x402_grade_submit",
        {"path": "/api/v1/x402/grades"},
        _nothing,
    ),
    (
        "grade score",
        grading_routes,
        "x402_grade_score",
        {"method": "GET", "path": "/api/v1/x402/grades/score"},
        _nothing,
    ),
    (
        "grade top",
        grading_routes,
        "x402_grade_top",
        {"method": "GET", "path": "/api/v1/x402/grades/top"},
        _no_rate_limit(grading_routes, "grading_top_rate_limited"),
    ),
    (
        "news search",
        news_routes,
        "x402_news_search",
        {"method": "GET", "path": "/api/v1/x402/news/search"},
        _nothing,
    ),
    (
        "scan url",
        scan_routes,
        "x402_scan_url",
        {"path": "/api/v1/x402/scan/url"},
        _no_rate_limit(scan_routes, "scan_rate_limited"),
    ),
    (
        "uptime check",
        uptime_routes,
        "x402_uptime_check",
        {"path": "/api/v1/x402/uptime/check"},
        _no_rate_limit(uptime_routes, "ip_rate_limited"),
    ),
    (
        "social register",
        social_routes,
        "x402_social_register",
        {"path": "/api/v1/x402/social/register"},
        _nothing,
    ),
    (
        "social agent search",
        social_routes,
        "x402_social_agent_search",
        {"method": "GET", "path": "/api/v1/x402/social/agents/search"},
        _nothing,
    ),
    (
        "social follow",
        social_routes,
        "x402_social_follow",
        # No path_params at all -- the worst case: a bare probe that has not
        # substituted a real Algorand address for `:wallet` yet.
        {"path": "/api/v1/x402/social/agents/x/follow"},
        _nothing,
    ),
    (
        "social post",
        social_routes,
        "x402_social_post_create",
        {"path": "/api/v1/x402/social/posts"},
        _nothing,
    ),
    (
        "social comment",
        social_routes,
        "x402_social_comment_create",
        {"path": "/api/v1/x402/social/posts/p1/comments", "path_params": {"post_id": "p1"}},
        _post_exists,
    ),
    (
        "social react",
        social_routes,
        "x402_social_react",
        {"path": "/api/v1/x402/social/posts/p1/react", "path_params": {"post_id": "p1"}},
        _post_exists,
    ),
    (
        "social group create",
        social_routes,
        "x402_social_group_create",
        {"path": "/api/v1/x402/social/groups"},
        _nothing,
    ),
    (
        "social report",
        social_routes,
        "x402_social_report_create",
        {"path": "/api/v1/x402/social/reports"},
        _nothing,
    ),
    (
        "social case vote",
        social_routes,
        "x402_social_case_vote",
        {"path": "/api/v1/x402/social/cases/c1/vote", "path_params": {"case_id": "c1"}},
        _case_is_open,
    ),
    (
        "storage create",
        storage_routes,
        "x402_storage_create_backup",
        # The price is computed from declared_size_bytes, so that one query
        # param is the only input read before the challenge.
        {"path": "/api/v1/x402/storage/backups", "query": {"declared_size_bytes": "10"}},
        _nothing,
    ),
    (
        "storage renew",
        storage_routes,
        "x402_storage_renew_backup",
        # wallet moved to a query param 2026-09-06 (was body-only, so a
        # header-less request always 400'd before it could ever see this
        # challenge) -- the free lookup it enables must resolve to an
        # existing, not-at-cap backup before the price-bearing challenge can
        # fire at all, hence the setup below.
        {
            "path": "/api/v1/x402/storage/backups/b1/renew",
            "path_params": {"backup_id": "b1"},
            "query": {"wallet": "A" * 58},
        },
        _storage_backup_exists,
    ),
    (
        "kya verify",
        kyc_routes,
        "kyc_verify",
        {"method": "GET", "path": "/api/v1/kyc/verify"},
        _nothing,
    ),
]


@pytest.mark.parametrize(
    ("module", "handler_name", "request_kwargs", "setup"),
    [pytest.param(m, h, kw, s, id=label) for label, m, h, kw, s in _ROUTES],
)
def test_every_reordered_route_challenges_before_reading_its_input(
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    handler_name: str,
    request_kwargs: dict[str, Any],
    setup: Callable[[pytest.MonkeyPatch], None],
) -> None:
    """An EMPTY request (no body, no query string) must come back as the challenge itself -- before the fix every one of these answered 400 from its own input validation first."""
    setup(monkeypatch)
    monkeypatch.setattr(module, "challenge_if_unpaid", lambda *_a, **_kw: _SENTINEL)

    def _must_not_reach_the_gate(*_a: object, **_kw: object) -> Never:
        raise AssertionError("require_paid_request must not run when the challenge fires")

    monkeypatch.setattr(module, "require_paid_request", _must_not_reach_the_gate)

    response = getattr(module, handler_name)(_request(**request_kwargs))

    assert response is _SENTINEL


# --------------------------------------------------------------------------- #
# 3. One route end to end against the offline facilitator
# --------------------------------------------------------------------------- #
@pytest.mark.usefixtures("testnet_settings")
def test_an_empty_body_with_no_payment_yields_the_listing_offer_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/v1/x402/list with an empty body and no payment header -- the exact request found live returning 400 -- now returns the real 402 at the listing price."""
    monkeypatch.setattr(settings, "x402_listing_price", "$0.10")

    response = directory_routes.x402_list(_request(body=b""))

    assert response.status_code == 402
    offer = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    assert offer.accepts[0].amount == "100000"
    assert offer.accepts[0].extra["tag"] == x402_client.CHALLENGE_TAG
    assert offer.resource.url.endswith("/api/v1/x402/list")


@pytest.mark.usefixtures("testnet_settings")
def test_an_unpaid_renew_for_a_real_backup_yields_the_offer_at_its_computed_price_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST .../storage/backups/{id}/renew?wallet=... with no body and no payment header -- the exact shape that used to 400 on the missing JSON body -- now returns the real 402, priced from the looked-up backup's own size_bytes, not a decode error."""
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    monkeypatch.setattr(settings, "x402_storage_price_per_kb_per_90d", "$0.000001953125")
    monkeypatch.setattr(settings, "x402_storage_price_floor", "$0.001")
    _storage_backup_exists(monkeypatch)  # a 1024-byte (1 KB-unit) backup, not at its cap

    response = storage_routes.x402_storage_renew_backup(
        _request(
            path="/api/v1/x402/storage/backups/b1/renew",
            path_params={"backup_id": "b1"},
            query={"wallet": "A" * 58},
            body=b"",
        )
    )

    assert response.status_code == 402
    offer = decode_payment_required_header(response.headers["PAYMENT-REQUIRED"])
    # ceil(1024/1KB)=1 unit * $0.000001953125, floored at $0.001 (the raw
    # linear amount for a single KB is far below the floor).
    assert offer.accepts[0].amount == "1000"
    assert offer.accepts[0].extra["tag"] == x402_client.CHALLENGE_TAG
    assert offer.resource.url.endswith("/api/v1/x402/storage/backups/{backup_id}/renew")


def test_an_unknown_backup_still_free_404s_before_any_challenge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renew for a wallet/backup_id pair with no matching row is still the free 404 this route has always had -- moving `wallet` to a query param does not turn an unknown id into a 402 or invent any new behavior for it."""
    monkeypatch.setattr(
        storage_routes, "backup_service", SimpleNamespace(get=lambda _wallet, _backup_id: None)
    )

    def _must_not_challenge(*_a: object, **_kw: object) -> Never:
        raise AssertionError("an unknown backup must never reach the payment gate")

    monkeypatch.setattr(storage_routes, "challenge_if_unpaid", _must_not_challenge)

    response = storage_routes.x402_storage_renew_backup(
        _request(
            path="/api/v1/x402/storage/backups/no-such-id/renew",
            path_params={"backup_id": "no-such-id"},
            query={"wallet": "A" * 58},
            body=b"",
        )
    )

    assert response.status_code == 404
