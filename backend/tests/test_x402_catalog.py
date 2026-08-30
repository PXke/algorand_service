"""x402 catalog tests: gating, prices, and the roster-vs-route-table cross-check.

Fully offline: Redis is a fake at the get_redis seam and nothing here settles
a payment. The route-table checks build the real Falcon app via create_app()
and look routes up in its compiled router, so the roster in
services/catalog.py is verified against what is ACTUALLY registered rather
than against a second hand-written list.
"""

from __future__ import annotations

import re
from typing import Any, Never

import falcon
import pytest

pytest.importorskip("x402")

from falcon import testing
from x402.mechanisms.avm.constants import ALGORAND_MAINNET_CAIP2, ALGORAND_TESTNET_CAIP2

from app.core import rate_limit as rate_limit_core
from app.core.config import settings
from app.core.http import QueryParams, Request
from app.falcon_main import create_app
from app.modules.x402_catalog.api import routes as catalog_routes
from app.modules.x402_catalog.services import catalog as catalog_service

_PAY_TO = "A" * 58
_PARAM_RE = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")

# Every store setting falcon_main.py gates a product on, plus the product key
# the catalog files that product under.
_STORE_GATES = {
    "directory": "x402_directory_store",
    "board": "x402_board_store",
    "features": "x402_features_store",
    "grading": "x402_grading_store",
    "news": "news_store",
    "kya": "kyc_store",
}


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def incr(self, key: str) -> int:
        value = int(self.store.get(key, "0")) + 1
        self.store[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        _ = key, seconds
        return True


class _BrokenRedis:
    def incr(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")

    def expire(self, *_args: object, **_kwargs: object) -> Never:
        raise ConnectionError("redis down")


def _request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        method="GET",
        headers=headers or {},
        query_params=QueryParams({}),
        path_params={},
        body=b"",
        url=None,
    )


def _configure(monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True, **stores: str) -> None:
    """x402 on, every product store "memory" unless overridden by `stores`."""
    monkeypatch.setattr(settings, "x402_enabled", enabled)
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", _PAY_TO)
    for setting in _STORE_GATES.values():
        monkeypatch.setattr(settings, setting, "memory")
    for setting, value in stores.items():
        monkeypatch.setattr(settings, setting, value)


def _concrete(path: str) -> str:
    """A catalog path with every :param filled, so the router can match it."""
    return _PARAM_RE.sub("x", path)


def _registered_methods(app: falcon.App, path: str) -> set[str]:
    """The methods create_app() registered for `path`, empty when it is not routed."""
    found = app._router.find(_concrete(path))
    if found is None:
        return set()
    resource = found[0]
    return set(getattr(resource, "_methods", {}).keys())


def _catalog_routes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    monkeypatch.setattr(rate_limit_core, "get_redis", _FakeRedis)
    return catalog_service.build_catalog()["routes"]


# --------------------------------------------------------------------------- #
# Gating
# --------------------------------------------------------------------------- #
def test_catalog_route_is_registered_only_when_x402_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The catalog route itself is gated on the shared x402_enabled switch, like every product."""
    monkeypatch.setattr(rate_limit_core, "get_redis", _FakeRedis)
    _configure(monkeypatch, enabled=False)
    assert testing.TestClient(create_app()).simulate_get("/api/v1/x402").status_code == 404

    _configure(monkeypatch, enabled=True)
    response = testing.TestClient(create_app()).simulate_get("/api/v1/x402")
    assert response.status_code == 200
    assert response.json["pay_to"] == _PAY_TO
    assert response.json["challenge_tag"] == "x402-global-challenge"


def test_catalog_lists_only_products_whose_store_is_durable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A product backed by "memory" is not registered by create_app(), so it must not be advertised."""
    _configure(monkeypatch, x402_board_store="cassandra", news_store="cassandra")
    products = {route["product"] for route in _catalog_routes(monkeypatch)}
    assert products == {"catalog", "board", "news"}


def test_catalog_is_empty_of_products_when_x402_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """With x402 off, nothing is registered, so nothing is listed -- a durable store alone does not enable a product."""
    _configure(monkeypatch, enabled=False, x402_board_store="cassandra")
    assert catalog_service.enabled_products() == []


@pytest.mark.parametrize(("product", "store_setting"), sorted(_STORE_GATES.items()))
def test_each_product_gate_matches_create_app(
    monkeypatch: pytest.MonkeyPatch, product: str, store_setting: str
) -> None:
    """Turning exactly one store durable registers exactly that product's routes, and the catalog says so."""
    _configure(monkeypatch, **{store_setting: "cassandra"})
    app = create_app()
    listed = [route for route in _catalog_routes(monkeypatch) if route["product"] == product]
    assert listed, f"{product} is enabled but the catalog lists no route for it"
    for route in listed:
        assert route["method"] in _registered_methods(app, route["path"]), route["path"]
    # Every OTHER product's routes are neither registered nor listed.
    for other in catalog_service.PRODUCTS:
        if other.key in {product, "catalog"}:
            continue
        for route in other.routes:
            assert route.method not in _registered_methods(app, route.path), route.path


# --------------------------------------------------------------------------- #
# Roster vs route table
# --------------------------------------------------------------------------- #
def test_every_listed_route_is_registered_when_everything_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every route the catalog lists resolves in create_app()'s router with that method."""
    _configure(monkeypatch, **dict.fromkeys(_STORE_GATES.values(), "cassandra"))
    app = create_app()
    routes = _catalog_routes(monkeypatch)
    assert {route["product"] for route in routes} == {"catalog", *_STORE_GATES}
    for route in routes:
        assert route["method"] in _registered_methods(app, route["path"]), route


def test_every_registered_x402_route_is_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reverse direction: a paid/free product route added to a module without a roster entry fails here.

    Walks the compiled router for every /api/v1/x402/* and /api/v1/kyc/*
    template create_app() registered and demands a catalog entry per
    (method, path). Admin routes are exempt by design.
    """
    _configure(monkeypatch, **dict.fromkeys(_STORE_GATES.values(), "cassandra"))
    app = create_app()
    listed = {(route["method"], route["path"]) for route in _catalog_routes(monkeypatch)}
    registered: set[tuple[str, str]] = set()
    for template, resource in _walk_router(app):
        if not (template.startswith("/api/v1/x402") or template.startswith("/api/v1/kyc")):
            continue
        if template.startswith("/api/v1/admin/"):
            continue
        for method in resource._methods:
            registered.add((method, re.sub(r"\{(\w+)\}", r":\1", template)))
    assert registered == listed


def _walk_router(app: falcon.App) -> list[tuple[str, Any]]:
    """(uri_template, resource) for every route in Falcon's compiled router."""
    out: list[tuple[str, Any]] = []

    def visit(nodes: list[Any]) -> None:
        for node in nodes:
            if node.resource is not None:
                out.append((node.uri_template, node.resource))
            visit(node.children)

    visit(app._router._roots)
    return out


# --------------------------------------------------------------------------- #
# Prices, assets, network
# --------------------------------------------------------------------------- #
def test_prices_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prices are read from settings at build time, never copied into the roster."""
    _configure(monkeypatch, **dict.fromkeys(_STORE_GATES.values(), "cassandra"))
    monkeypatch.setattr(settings, "x402_listing_price", "$1.23")
    monkeypatch.setattr(settings, "x402_news_article_price", "$0.07")
    by_key = {(r["method"], r["path"]): r for r in _catalog_routes(monkeypatch)}
    assert by_key[("POST", "/api/v1/x402/list")]["price_usd"] == "$1.23"
    assert by_key[("GET", "/api/v1/x402/news/articles/:article_id")]["price_usd"] == "$0.07"
    assert by_key[("GET", "/api/v1/x402/search")]["price_usd"] is None
    assert by_key[("GET", "/api/v1/x402/search")]["paid"] is False
    assert by_key[("POST", "/api/v1/x402/list")]["paid"] is True


def test_every_paid_route_has_a_real_price_setting_and_resource() -> None:
    """A paid roster entry must name a settings attribute that exists and a ledger resource id."""
    for product in catalog_service.PRODUCTS:
        for route in product.routes:
            if route.paid:
                assert hasattr(settings, route.price_setting), route.path
                assert route.resource, route.path


def test_owner_only_renew_routes_say_a_non_owner_payment_is_still_taken() -> None:
    """Every owner-gated renew entry tells the agent up front that a non-owner's payment settles and is refused -- the 402 says it, so the catalog must too."""
    renew_routes = [
        route
        for product in catalog_service.PRODUCTS
        for route in product.routes
        if route.path.endswith("/renew")
    ]
    assert len(renew_routes) >= 2
    for route in renew_routes:
        text = route.description.lower()
        assert "settles" in text, route.path
        assert "refused" in text, route.path


def test_assets_follow_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The advertised assets are the ones that exist on the configured network, USDC first."""
    _configure(monkeypatch)
    monkeypatch.setattr(rate_limit_core, "get_redis", _FakeRedis)
    testnet = catalog_service.build_catalog()
    assert testnet["network_name"] == "testnet"
    assert [a["symbol"] for a in testnet["assets"]] == ["USDC"]

    monkeypatch.setattr(settings, "x402_network", ALGORAND_MAINNET_CAIP2)
    mainnet = catalog_service.build_catalog()
    assert mainnet["network_name"] == "mainnet"
    assert [a["symbol"] for a in mainnet["assets"]] == ["USDC", "EURQ", "USDQ"]
    assert mainnet["assets"][0]["asa_id"] == 31566704


# --------------------------------------------------------------------------- #
# Rate limit
# --------------------------------------------------------------------------- #
def test_catalog_is_rate_limited_per_ip_and_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """Third call from one IP within the budget of 2 is a 429; another IP is unaffected; a Redis failure fails open."""
    _configure(monkeypatch)
    monkeypatch.setattr(settings, "x402_catalog_rate_limit_per_hour", 2)
    fake = _FakeRedis()
    monkeypatch.setattr(rate_limit_core, "get_redis", lambda: fake)
    headers = {"x-real-ip": "203.0.113.9"}
    assert isinstance(catalog_routes.x402_catalog(_request(headers)), dict)
    assert isinstance(catalog_routes.x402_catalog(_request(headers)), dict)
    limited = catalog_routes.x402_catalog(_request(headers))
    assert limited.status_code == 429
    # A different IP has its own budget.
    assert isinstance(catalog_routes.x402_catalog(_request({"x-real-ip": "203.0.113.10"})), dict)

    monkeypatch.setattr(rate_limit_core, "get_redis", _BrokenRedis)
    assert isinstance(catalog_routes.x402_catalog(_request(headers)), dict)
