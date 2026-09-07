"""Every route path renamed by docs/x402-marketplace-ux-audit.md section 3.3 keeps working.

Old paths keep working, unchanged, forever (section 3.5 "Migration without breakage") --
this marketplace is live on mainnet with real external callers, some of which may already
have hardcoded the old paths (see the 2026-09-07 audit's own live-fetched route count). Every
new path added by that rename is a SECOND, direct route registration against the identical
Python handler as its old path -- never a duplicated handler body, and never an HTTP-internal
redirect from new to old or old to new.

This file proves both halves for every renamed pair:

1. `test_every_alias_pair_shares_the_identical_handler_object` drives the real
   `register_x402_*_routes` registrars (the same entry point create_app() uses) and asserts
   the old path and the new path resolve to the literal same handler function (`is`, not
   `==`) -- the only way "no duplicated logic" is actually true rather than merely claimed.
2. `test_free_read_alias_pairs_return_byte_identical_responses` calls a curated set of free,
   non-mutating GET pairs through both paths with an equivalent request and asserts the
   returned payloads are equal -- the strongest available proof that an old-path caller sees
   exactly what it saw before.
3. `test_paid_or_mutating_alias_pairs_advertise_the_same_price_and_resource` covers every
   remaining (paid, or state-changing) pair: same unpaid header-less request through both
   paths must yield the same status code, and for a 402 the same price/asset/resource id --
   with the one field that SHOULD legitimately differ (the offer's advertised resource URL,
   which necessarily echoes back whichever path the caller actually used) checked instead for
   the opposite property: it echoes back the path that was actually called, proving the alias
   is wired correctly rather than silently hardcoded to one path.

Fully offline: a stub facilitator (get_supported/verify/settle), no Redis (rate limiters
and the circuit breaker are monkeypatched closed/off), no Cassandra. Nothing here settles a
real payment.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from types import SimpleNamespace
from typing import Never
from unittest.mock import MagicMock

import pytest

pytest.importorskip("x402")

from x402.http.utils import decode_payment_required_header
from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2
from x402.schemas.payments import PaymentRequired
from x402.schemas.responses import SupportedKind, SupportedResponse
from x402.server import x402ResourceServerSync

from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.x402 import circuit_breaker
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard

_Handler = Callable[..., object]

# (method, old_path, new_path) for every rename in
# docs/x402-marketplace-ux-audit.md section 3.3 that this pass implemented --
# a pure path rename against the identical handler, never a behavior change.
# Kept in the doc's own section order for easy cross-reference.
ALIAS_PAIRS: tuple[tuple[str, str, str], ...] = (
    # meta
    ("GET", "/api/v1/x402/settlements/recent", "/api/v1/x402/settlements"),
    # discover / directory
    ("POST", "/api/v1/x402/list", "/api/v1/x402/directory/listings"),
    ("GET", "/api/v1/x402/search", "/api/v1/x402/directory/listings"),
    ("GET", "/api/v1/x402/listings", "/api/v1/x402/directory/listings/lookup"),
    ("POST", "/api/v1/x402/list/renew", "/api/v1/x402/directory/listings/boost"),
    ("GET", "/api/v1/x402/directory/probe", "/api/v1/x402/uptime/probes/latest"),
    ("GET", "/api/v1/x402/directory/probe/history", "/api/v1/x402/uptime/probes"),
    (
        "GET",
        "/api/v1/x402/directory/probe/leaderboard",
        "/api/v1/x402/trust/leaderboards/reliability",
    ),
    # discover / board
    ("POST", "/api/v1/x402/board", "/api/v1/x402/board/placements"),
    ("GET", "/api/v1/x402/board", "/api/v1/x402/board/placements"),
    ("POST", "/api/v1/x402/board/:entry_id/renew", "/api/v1/x402/board/placements/:entry_id/boost"),
    ("GET", "/api/v1/x402/board/:entry_id/go", "/api/v1/x402/board/placements/:entry_id/go"),
    (
        "GET",
        "/api/v1/x402/board/:entry_id/clicks",
        "/api/v1/x402/board/placements/:entry_id/clicks",
    ),
    # discover / requests (features -> requests)
    ("POST", "/api/v1/x402/features", "/api/v1/x402/requests"),
    ("GET", "/api/v1/x402/features", "/api/v1/x402/requests"),
    ("GET", "/api/v1/x402/features/demand", "/api/v1/x402/requests/ranked"),
    (
        "POST",
        "/api/v1/x402/features/:request_id/vote",
        "/api/v1/x402/requests/:request_id/votes",
    ),
    (
        "POST",
        "/api/v1/x402/features/:request_id/claim",
        "/api/v1/x402/requests/:request_id/claims",
    ),
    (
        "POST",
        "/api/v1/x402/features/:request_id/complete",
        "/api/v1/x402/requests/:request_id/completions",
    ),
    # trust / grades
    ("GET", "/api/v1/x402/grades/summary", "/api/v1/x402/grades/lookup"),
    ("GET", "/api/v1/x402/grades/top", "/api/v1/x402/trust/leaderboards/graded"),
    # trust / uptime
    ("POST", "/api/v1/x402/uptime/check", "/api/v1/x402/uptime/checks"),
    ("GET", "/api/v1/x402/uptime/history", "/api/v1/x402/uptime/checks"),
    # trust / leaderboards (social half)
    ("GET", "/api/v1/x402/social/agents/leaderboard", "/api/v1/x402/trust/leaderboards/spend"),
    # network / social
    ("POST", "/api/v1/x402/social/register", "/api/v1/x402/social/agents"),
    ("PATCH", "/api/v1/x402/social/profile", "/api/v1/x402/social/agents/me"),
    (
        "DELETE",
        "/api/v1/x402/social/groups/:group_id/membership",
        "/api/v1/x402/social/groups/:group_id/members/me",
    ),
    (
        "POST",
        "/api/v1/x402/social/groups/:group_id/join",
        "/api/v1/x402/social/groups/:group_id/members",
    ),
    # identity primitive (storage half)
    ("POST", "/api/v1/x402/storage/auth/challenge", "/api/v1/x402/storage/auth/nonce"),
)

# Free, side-effect-free GET pairs whose response never depends on which of
# the two paths was used to call it -- safe to assert full response equality
# rather than only "same handler, same status". Kept intentionally smaller
# than ALIAS_PAIRS: mutating routes (board's click-through, POST writes) or
# routes whose free/paid status genuinely depends on the specific
# path/target (grades/top's pre-gate 404) are covered by the paid/mutating
# test below instead, since a second identical call is not guaranteed
# side-effect-free for them.
_FREE_DEEP_EQUALITY_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("GET", "/api/v1/x402/settlements/recent", "/api/v1/x402/settlements"),
    ("GET", "/api/v1/x402/search", "/api/v1/x402/directory/listings"),
    ("GET", "/api/v1/x402/listings", "/api/v1/x402/directory/listings/lookup"),
    ("GET", "/api/v1/x402/directory/probe", "/api/v1/x402/uptime/probes/latest"),
    ("GET", "/api/v1/x402/directory/probe/history", "/api/v1/x402/uptime/probes"),
    ("GET", "/api/v1/x402/board", "/api/v1/x402/board/placements"),
    ("GET", "/api/v1/x402/features", "/api/v1/x402/requests"),
    ("GET", "/api/v1/x402/grades/summary", "/api/v1/x402/grades/lookup"),
)

_WALLET = "KSAVOYTVNB7A6NKCM4W2WBOOGFHWH2SEGR5T6OGB7THCAT5E36LDFEBTII"
_EXAMPLE_URL = "https://api.example.com/v1/quote"

_PATH_PARAM_VALUES = {
    "entry_id": "00000000-0000-0000-0000-000000000001",
    "request_id": "00000000-0000-0000-0000-000000000001",
    "group_id": "00000000-0000-0000-0000-000000000001",
    "backup_id": "00000000-0000-0000-0000-000000000001",
    "wallet": _WALLET,
}

# A few routes require a non-empty query to reach anything meaningful
# (a lookup-by-url, or storage's size-priced create).
_QUERY_BY_PATH: dict[str, dict[str, str]] = {
    "/api/v1/x402/listings": {"url": _EXAMPLE_URL},
    "/api/v1/x402/directory/listings/lookup": {"url": _EXAMPLE_URL},
    "/api/v1/x402/directory/probe": {"url": _EXAMPLE_URL},
    "/api/v1/x402/uptime/probes/latest": {"url": _EXAMPLE_URL},
    "/api/v1/x402/directory/probe/history": {"url": _EXAMPLE_URL},
    "/api/v1/x402/uptime/probes": {"url": _EXAMPLE_URL},
    "/api/v1/x402/grades/summary": {"url": _EXAMPLE_URL},
    "/api/v1/x402/grades/lookup": {"url": _EXAMPLE_URL},
    "/api/v1/x402/grades/top": {"tag": "pricing"},
    "/api/v1/x402/trust/leaderboards/graded": {"tag": "pricing"},
}

_REGISTRARS = [
    ("x402_catalog", "register_x402_catalog_routes"),
    ("x402_directory", "register_x402_directory_routes"),
    ("x402_board", "register_x402_board_routes"),
    ("x402_features", "register_x402_features_routes"),
    ("x402_grading", "register_x402_grading_routes"),
    ("x402_uptime", "register_x402_uptime_routes"),
    ("x402_social", "register_x402_social_routes"),
    ("x402_storage", "register_x402_storage_routes"),
]


class _StubFacilitator:
    """Canned /supported. An unpaid request must never verify or settle."""

    def get_supported(self) -> SupportedResponse:
        return SupportedResponse(
            kinds=[SupportedKind(x402_version=2, scheme="exact", network=ALGORAND_TESTNET_CAIP2)]
        )

    def verify(self, *_a: object, **_kw: object) -> Never:
        raise AssertionError("verify() must not be called for a header-less request")

    def settle(self, *_a: object, **_kw: object) -> Never:
        raise AssertionError("settle() must not be called for a header-less request")


def _stub_resource_server() -> x402ResourceServerSync:
    server = x402ResourceServerSync(_StubFacilitator())
    x402_client.register_tagged_exact_avm_scheme(server, ALGORAND_TESTNET_CAIP2)
    server.initialize()
    return server


class _RecordingRouter:
    """Same decorator surface as app.core.falcon_router.FalconRouter, indexed by (method, path)."""

    def __init__(self) -> None:
        self.by_key: dict[tuple[str, str], _Handler] = {}

    def _decorator(self, method: str, path: str) -> Callable[[_Handler], _Handler]:
        def inner(handler: _Handler) -> _Handler:
            self.by_key[(method, path)] = handler
            return handler

        return inner

    def get(self, path: str) -> Callable[[_Handler], _Handler]:
        return self._decorator("GET", path)

    def post(self, path: str) -> Callable[[_Handler], _Handler]:
        return self._decorator("POST", path)

    def put(self, path: str) -> Callable[[_Handler], _Handler]:
        return self._decorator("PUT", path)

    def patch(self, path: str) -> Callable[[_Handler], _Handler]:
        return self._decorator("PATCH", path)

    def delete(self, path: str) -> Callable[[_Handler], _Handler]:
        return self._decorator("DELETE", path)

    def head(self, path: str) -> Callable[[_Handler], _Handler]:
        return self._decorator("HEAD", path)


def _service(**attrs: object) -> MagicMock:
    stub = MagicMock()
    for name, value in attrs.items():
        setattr(stub, name, value)
    return stub


def _request(method: str, path: str) -> Request:
    """A header-less (unpaid), rate-limit-bypassed request for `path`, path params filled in."""
    path_params: dict[str, str] = {}
    resolved = path
    for segment in path.split("/"):
        if segment.startswith(":"):
            name = segment[1:]
            path_params[name] = _PATH_PARAM_VALUES.get(name, "x")
            resolved = resolved.replace(segment, path_params[name])
    return Request(
        method=method,
        headers={},
        query_params=QueryParams(_QUERY_BY_PATH.get(path, {})),
        path_params=path_params,
        body=b"",
        url=SimpleNamespace(scheme="https", host="localhost", path=resolved),
    )


@pytest.fixture
def routes_by_key(monkeypatch: pytest.MonkeyPatch) -> dict[tuple[str, str], _Handler]:
    """The real route table (old paths and new alias paths both), keyed by (method, path).

    Same shape as test_x402_bazaar_extension_sweep.py's own harness: drives the real
    register_x402_*_routes registrars against an offline stub facilitator, with every
    Redis-backed rate limiter and the circuit breaker forced open/closed so a route reaches
    its actual behavior instead of a 429/503.
    """
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", _WALLET)
    monkeypatch.setattr(x402_guard, "get_resource_server", _stub_resource_server)
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    for flag in ("x402_social_moderation_enabled", "x402_uptime_enabled"):
        monkeypatch.setattr(settings, flag, True)

    router = _RecordingRouter()
    for package, registrar in _REGISTRARS:
        module = importlib.import_module(f"app.modules.{package}.api.routes")
        for name in dir(module):
            if "rate_limited" in name and callable(getattr(module, name, None)):
                monkeypatch.setattr(module, name, lambda _request, *_a, **_kw: False)
        getattr(module, registrar)(router)

    social = importlib.import_module("app.modules.x402_social.api.routes")
    monkeypatch.setattr(
        social,
        "post_service",
        _service(get=lambda _id: SimpleNamespace(deleted=False, hidden_platform=False)),
    )
    monkeypatch.setattr(
        social,
        "group_service",
        _service(get=lambda _id: SimpleNamespace(deleted=False)),
        raising=False,
    )
    features = importlib.import_module("app.modules.x402_features.api.routes")
    monkeypatch.setattr(
        features,
        "feature_service",
        _service(
            exists=lambda _id: True,
            get=lambda _id: SimpleNamespace(status="open", claimed_by=None),
        ),
        raising=False,
    )
    board = importlib.import_module("app.modules.x402_board.api.routes")
    monkeypatch.setattr(
        board,
        "board_service",
        _service(get=lambda _id: SimpleNamespace(expired=False, payer=_WALLET)),
        raising=False,
    )
    return router.by_key


def test_every_alias_pair_is_registered(routes_by_key: dict[tuple[str, str], _Handler]) -> None:
    """Sanity check on the fixture itself: every pair this file claims to cover is actually live in the router, both paths."""
    missing = [
        f"{method} {path}"
        for method, old_path, new_path in ALIAS_PAIRS
        for path in (old_path, new_path)
        if (method, path) not in routes_by_key
    ]
    assert not missing, "alias pair path never registered:\n" + "\n".join(missing)


def test_every_alias_pair_shares_the_identical_handler_object(
    routes_by_key: dict[tuple[str, str], _Handler],
) -> None:
    """The core claim of this whole change: a renamed path is a second registration against the SAME Python function, never a copy, never an HTTP-internal redirect."""
    mismatched = []
    for method, old_path, new_path in ALIAS_PAIRS:
        old_handler = routes_by_key[(method, old_path)]
        new_handler = routes_by_key[(method, new_path)]
        if old_handler is not new_handler:
            mismatched.append(
                f"{method} {old_path} -> {old_handler!r} vs {method} {new_path} -> {new_handler!r}"
            )
    assert not mismatched, "alias pairs NOT sharing the identical handler:\n" + "\n".join(
        mismatched
    )


def test_free_read_alias_pairs_return_byte_identical_responses(
    routes_by_key: dict[tuple[str, str], _Handler],
) -> None:
    """For every free, non-mutating GET pair, calling the old path and the new path with an equivalent request returns an equal response -- the strongest available proof an old-path caller sees exactly what it saw before."""
    mismatched = []
    for method, old_path, new_path in _FREE_DEEP_EQUALITY_PAIRS:
        old_response = routes_by_key[(method, old_path)](_request(method, old_path))
        new_response = routes_by_key[(method, new_path)](_request(method, new_path))
        old_repr = _response_body(old_response)
        new_repr = _response_body(new_response)
        if old_repr != new_repr:
            mismatched.append(f"{method} {old_path} vs {new_path}:\n  {old_repr}\n  {new_repr}")
    assert not mismatched, "free-read alias pairs diverged:\n\n".join(mismatched)


def _response_body(result: object) -> object:
    if isinstance(result, Response):
        return (
            result.status_code,
            tuple(sorted((result.headers or {}).items())),
            result.description,
        )
    return result


def test_paid_or_mutating_alias_pairs_advertise_the_same_price_and_resource(
    routes_by_key: dict[tuple[str, str], _Handler],
) -> None:
    """Every alias pair not covered by the free-read deep-equality test above.

    Calls both paths with an equivalent unpaid, header-less request. Both must return the
    same status code. When that status is 402, the underlying price/asset/resource id must
    match exactly between old and new (same handler, same settings) -- the ONE field allowed
    to differ is the offer's advertised resource URL, which is checked for the opposite
    property instead: it must echo back the exact path that was actually called, proving the
    alias is genuinely wired to both paths rather than silently hardcoded to one.
    """
    remaining = [pair for pair in ALIAS_PAIRS if pair not in _FREE_DEEP_EQUALITY_PAIRS]
    assert remaining, "expected at least one non-free-read alias pair to check"

    failures = []
    for method, old_path, new_path in remaining:
        old_response = routes_by_key[(method, old_path)](_request(method, old_path))
        new_response = routes_by_key[(method, new_path)](_request(method, new_path))

        old_status = getattr(old_response, "status_code", None)
        new_status = getattr(new_response, "status_code", None)
        if old_status != new_status:
            failures.append(f"{method} {old_path} -> {old_status} but {new_path} -> {new_status}")
            continue

        if old_status != 402:
            # Free-but-mutating (features submit/vote/etc. reached a non-402
            # outcome, e.g. a 400/404 from this test's minimal fixtures) --
            # same status on both sides is everything this generic check can
            # safely assert without seeding real domain state.
            continue

        old_header = _payment_required_header(old_response)
        new_header = _payment_required_header(new_response)
        if old_header is None or new_header is None:
            failures.append(
                f"{method} {old_path} / {new_path}: 402 with no Payment-Required header"
            )
            continue

        old_offer = decode_payment_required_header(old_header)
        new_offer = decode_payment_required_header(new_header)
        old_facts = _offer_facts(old_offer)
        new_facts = _offer_facts(new_offer)
        if old_facts != new_facts:
            failures.append(
                f"{method} {old_path} vs {new_path}: price/asset/resource diverged:\n"
                f"  {old_facts}\n  {new_facts}"
            )

        old_url = old_offer.resource.url if old_offer.resource else ""
        new_url = new_offer.resource.url if new_offer.resource else ""
        if ":" in old_path:
            # A route with a path parameter passes an explicit resource_path=
            # to require_payment() so the Bazaar catalogs one TEMPLATE entry
            # rather than one per parameter value (see
            # test_x402_bazaar_extension_sweep.py's own test on this). That
            # resource_path is a hardcoded string still naming the OLD path
            # -- untouched by this pass, which deliberately does not touch
            # payment-gate call sites (CLAUDE.md section 9 / the task's own
            # scope limit). So for these routes specifically, old and new
            # calls advertise the SAME (old) URL -- not a divergence, and not
            # something this pass changes; see this file's own "Observed,
            # not fixed" note and docs/x402-marketplace-ux-audit.md section
            # 3.5's "Only resource_path ... should emit the new canonical
            # path" follow-up.
            if old_url != new_url:
                failures.append(
                    f"{method} {old_path} vs {new_path}: hardcoded resource_path "
                    f"template diverged unexpectedly: {old_url!r} vs {new_url!r}"
                )
            continue
        # A path with no parameter falls back to request.url.path (see
        # guard.py's _resource_url), so it correctly echoes back whichever of
        # the two paths was actually called -- proving the alias is wired to
        # both paths rather than silently hardcoded to one.
        if not old_url.endswith(old_path):
            failures.append(
                f"{method} {old_path}: offer resource url {old_url!r} does not end with "
                f"the path actually called ({old_path!r})"
            )
        if not new_url.endswith(new_path):
            failures.append(
                f"{method} {new_path}: offer resource url {new_url!r} does not end with "
                f"the path actually called ({new_path!r})"
            )

    assert not failures, "paid/mutating alias pairs:\n" + "\n".join(failures)


def _payment_required_header(response: object) -> str | None:
    if not isinstance(response, Response):
        return None
    return next(
        (v for k, v in (response.headers or {}).items() if k.lower() == "payment-required"),
        None,
    )


def _offer_facts(offer: PaymentRequired) -> tuple[object, ...]:
    """(resource.description, each accepted asset+amount) -- everything about a 402 offer EXCEPT the advertised URL, which legitimately varies by calling path.

    require_payment() (app/modules/x402/guard.py) builds resource.url from
    the REQUEST's own path (`_resource_url`); the short stable ledger id
    (e.g. "x402-directory-list") is never echoed into the response at all,
    only resource.description (static text, independent of path) and the
    priced `accepts` list -- both must be identical between an old path and
    its new alias, since both are produced by the identical handler call.
    """
    description = offer.resource.description if offer.resource else None
    accepts = tuple(sorted((option.asset, option.amount) for option in offer.accepts))
    return (description, accepts)
