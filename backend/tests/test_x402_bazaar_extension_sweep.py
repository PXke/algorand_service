"""Every paid route's 402 must carry a Bazaar extension the facilitator accepts.

A route whose declared discovery extension fails the facilitator's own
`validate_discovery_extension` (after the resource server injects the HTTP
method at request time) still settles payments fine -- it is just silently
never catalogued in the Bazaar. tests/test_x402_discovery.py pins that rule
on the primitive; this sweep drives the real `register_x402_*_routes`
registrars, so a newly added paid route is covered the day it is registered,
with no list to keep in sync.

For every route that answers an unpaid request with a 402 it asserts:

1. the declared `bazaar` extension survives the facilitator's own
   `parse_discovery_extension` + `validate_discovery_extension`, run after
   injecting the route's HTTP method exactly the way
   `BazaarResourceServerExtension.enrich_declaration` does at request time;
2. a route with a path parameter advertises the TEMPLATE
   (`.../{backup_id}/renew`), not one concrete value -- the facilitator
   catalogs one entry per distinct advertised URL;
3. every payment option carries the challenge tag.

Fully offline: a stub facilitator, no Redis, no Cassandra, no network.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from types import SimpleNamespace
from typing import Never

import pytest

pytest.importorskip("x402")

from x402.extensions.bazaar import validate_discovery_extension
from x402.extensions.bazaar.types import parse_discovery_extension
from x402.http.utils import decode_payment_required_header
from x402.mechanisms.avm.constants import ALGORAND_TESTNET_CAIP2
from x402.schemas.payments import PaymentRequired, PaymentRequirements
from x402.schemas.responses import SupportedKind, SupportedResponse
from x402.schemas.v1 import PaymentRequirementsV1
from x402.server import x402ResourceServerSync

from app.core.config import settings
from app.core.http import QueryParams, Request, Response
from app.modules.x402 import circuit_breaker
from app.modules.x402 import client as x402_client
from app.modules.x402 import guard as x402_guard

# A route handler and the decorator the registrars apply to one. Handlers
# return a Response or a plain dict the framework serializes, so the return
# type is deliberately open.
_Handler = Callable[..., object]
_Decorator = Callable[[_Handler], _Handler]
_Route = tuple[str, str, _Handler]

# A real, checksum-valid Algorand address: several routes validate the shape of
# a wallet path parameter before they ever reach the payment gate.
_WALLET = "KSAVOYTVNB7A6NKCM4W2WBOOGFHWH2SEGR5T6OGB7THCAT5E36LDFEBTII"

# Placeholder values for path parameters, so a route that looks its subject up
# before charging gets past that check and reaches its offer.
_PATH_PARAM_VALUES = {
    "backup_id": "00000000-0000-0000-0000-000000000001",
    "article_id": "00000000-0000-0000-0000-000000000001",
    "wallet": _WALLET,
}

# The storage create route prices its offer from this query parameter, so it
# needs one to have a price to advertise at all.
_QUERY_BY_PATH = {"/api/v1/x402/storage/backups": {"declared_size_bytes": "1024"}}

# The registrars to drive. Each is the module's real entry point, so this sweep
# sees exactly the routes the app registers.
_REGISTRARS = [
    ("x402_catalog", "register_x402_catalog_routes"),
    ("x402_news", "register_x402_news_routes"),
    ("x402_scan", "register_x402_scan_routes"),
    ("x402_storage", "register_x402_storage_routes"),
]

# Guards the sweep against silently degrading to "nothing was checked" -- if a
# registrar stops registering, or every route starts erroring before its offer,
# the count drops and this test fails instead of passing vacuously. Raise it
# when paid routes are added; never lower it to make a red test green. Set to
# the paid routes that reach their offer from a bare request today: news
# search, scan url, storage create (storage renew/add-version need a real
# backup lookup first).
_MINIMUM_PAID_ROUTES_COVERED = 3


class _StubFacilitator:
    """Canned /supported. An unpaid request must never verify or settle."""

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


class _RecordingRouter:
    """Same decorator surface as app.core.falcon_router.FalconRouter."""

    def __init__(self) -> None:
        self.routes: list[_Route] = []

    def _decorator(self, method: str, path: str) -> _Decorator:
        def inner(handler: _Handler) -> _Handler:
            self.routes.append((method, path, handler))
            return handler

        return inner

    def get(self, path: str) -> _Decorator:
        return self._decorator("GET", path)

    def post(self, path: str) -> _Decorator:
        return self._decorator("POST", path)

    def put(self, path: str) -> _Decorator:
        return self._decorator("PUT", path)

    def patch(self, path: str) -> _Decorator:
        return self._decorator("PATCH", path)

    def delete(self, path: str) -> _Decorator:
        return self._decorator("DELETE", path)

    def head(self, path: str) -> _Decorator:
        return self._decorator("HEAD", path)


def _request(method: str, path: str) -> Request:
    path_params: dict[str, str] = {}
    for segment in path.split("/"):
        if segment.startswith(":"):
            name = segment[1:]
            path_params[name] = _PATH_PARAM_VALUES.get(name, "x")
            path = path.replace(segment, path_params[name])
    return Request(
        method=method,
        headers={},
        query_params=QueryParams(_QUERY_BY_PATH.get(path, {})),
        path_params=path_params,
        body=b"",
        url=SimpleNamespace(scheme="https", host="localhost", path=path),
    )


@pytest.fixture
def registered_routes(monkeypatch: pytest.MonkeyPatch) -> list[_Route]:
    """The real route table, with the gate pointed at the offline stub facilitator.

    Route families that are gated off by default are switched on so their paid
    routes are swept too -- they are registered in prod.
    """
    import importlib

    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", _WALLET)
    monkeypatch.setattr(x402_guard, "get_resource_server", _stub_resource_server)
    # Every paid route checks its breaker before its offer; keep it closed
    # without Redis.
    monkeypatch.setattr(circuit_breaker, "is_tripped", lambda _resource: False)
    monkeypatch.setattr(settings, "x402_scan_enabled", True)

    router = _RecordingRouter()
    for package, registrar in _REGISTRARS:
        module = importlib.import_module(f"app.modules.{package}.api.routes")
        # Rate limits are Redis-backed; never let one answer instead of the offer.
        for name in dir(module):
            if "rate_limited" in name and callable(getattr(module, name, None)):
                monkeypatch.setattr(module, name, lambda _request, *_a, **_kw: False)
        getattr(module, registrar)(router)

    return router.routes


def _offers(routes: list[_Route]) -> Iterator[tuple[str, str, PaymentRequired]]:
    """(method, path, decoded 402 offer) for every route that challenges an unpaid request."""
    for method, path, handler in routes:
        if "/admin/" in path or "/internal/" in path:
            continue
        try:
            response = handler(_request(method, path))
        except Exception:
            # A free route hitting a faked store is not this test's subject.
            continue
        if not isinstance(response, Response) or response.status_code != 402:
            continue
        header = next(
            (v for k, v in (response.headers or {}).items() if k.lower() == "payment-required"),
            None,
        )
        if header is None:
            continue
        yield method, path, decode_payment_required_header(header)


def test_every_paid_route_declares_an_extension_the_facilitator_accepts(
    registered_routes: list[_Route],
) -> None:
    """The exact check the facilitator runs before cataloging: parse the declared extension, inject the route's method, validate. A route that fails this settles fine and is never catalogued."""
    covered = 0
    rejected: list[str] = []

    for method, path, offer in _offers(registered_routes):
        covered += 1
        extension = (offer.extensions or {}).get("bazaar")
        if extension is None:
            rejected.append(f"{method} {path}: no bazaar extension declared")
            continue

        # What BazaarResourceServerExtension.enrich_declaration does at request
        # time: inject the real HTTP method and make the schema require it.
        enriched = json.loads(json.dumps(extension))
        enriched["info"]["input"]["method"] = method
        required = list(enriched["schema"]["properties"]["input"].get("required", []))
        if "method" not in required:
            required.append("method")
        enriched["schema"]["properties"]["input"]["required"] = required

        try:
            result = validate_discovery_extension(parse_discovery_extension(enriched))
        except Exception as exc:
            # A declaration the facilitator cannot even parse is a failing
            # route, not a broken test.
            rejected.append(f"{method} {path}: not parseable -- {exc}")
            continue
        if not result.valid:
            rejected.append(f"{method} {path}: {result.errors}")

    assert not rejected, "routes the Bazaar would silently refuse to catalog:\n" + "\n".join(
        rejected
    )
    assert covered >= _MINIMUM_PAID_ROUTES_COVERED, (
        f"only {covered} paid routes reached their offer "
        f"(expected at least {_MINIMUM_PAID_ROUTES_COVERED}) -- the sweep has gone blind"
    )


def test_a_route_with_a_path_parameter_advertises_its_template(
    registered_routes: list[_Route],
) -> None:
    """The facilitator catalogs one Bazaar entry per distinct advertised URL, so a templated route must advertise `{param}`, never one concrete value -- otherwise every renew creates its own junk entry."""
    concrete: list[str] = []

    for method, path, offer in _offers(registered_routes):
        if ":" not in path:
            continue
        advertised = offer.resource.url if offer.resource else ""
        concrete.extend(
            f"{method} {path}: advertised {advertised!r}"
            for segment in path.split("/")
            if segment.startswith(":") and "{" + segment[1:] + "}" not in advertised
        )

    assert not concrete, (
        "templated routes advertising a concrete path parameter "
        "(pass resource_path= to the payment gate):\n" + "\n".join(concrete)
    )


def test_every_paid_route_carries_the_challenge_tag(
    registered_routes: list[_Route],
) -> None:
    """The tag rides on each AssetAmount.extra via the money parser; a module that builds its own scheme would ship untagged and drop off the competition leaderboard."""
    untagged: list[str] = []

    for method, path, offer in _offers(registered_routes):
        untagged.extend(
            f"{method} {path}: asset {option.asset} extra={option.extra}"
            for option in offer.accepts
            if (option.extra or {}).get("tag") != x402_client.CHALLENGE_TAG
        )

    assert not untagged, "payment options missing the challenge tag:\n" + "\n".join(untagged)
