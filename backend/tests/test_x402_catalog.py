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
#
# "storage" is a SECOND special case (see the "storage" Product's own
# nonempty_string_setting="x402_storage_local_root" in services/catalog.py):
# its registration needs BOTH this store durable AND a connector root
# configured, so toggling x402_storage_meta_store alone (as
# test_each_product_gate_matches_create_app below does for every entry in
# this dict) is only correct here because _configure() below ALWAYS sets
# x402_storage_local_root to a valid placeholder in its baseline (the same
# "just make it valid, not a per-test toggle" treatment as
# x402_pay_to_address/x402_network) -- test_storage_requires_both_meta_store_
# and_local_root separately proves the second half of the AND actually
# matters.
_STORE_GATES = {
    "directory": "x402_directory_store",
    "board": "x402_board_store",
    "features": "x402_features_store",
    "grading": "x402_grading_store",
    "news": "news_store",
    "kya": "kyc_store",
    "social": "x402_social_store",
    "storage": "x402_storage_meta_store",
}

_STORAGE_LOCAL_ROOT_PLACEHOLDER = "/tmp/x402-storage-catalog-test-root"

# Same idea for a product gated on a plain boolean instead of a store setting
# (Product.bool_setting) -- added 2026-09-01 after x402_scan shipped with a
# falcon_main.py registration but no PRODUCTS entry, and this file's "enable
# everything" helpers didn't know a bool-gated product existed either, so the
# cross-check tests passed vacuously without ever exercising it.
_BOOL_GATES = {
    "scan": "x402_scan_enabled",
    "uptime": "x402_uptime_enabled",
}

# A THIRD gating shape, distinct from both dicts above: a bool setting that
# gates individual ROUTES within an already-gated product (CatalogRoute.
# extra_bool_setting), not a whole product (Product.bool_setting). Added
# 2026-09-03 for x402_social's Phase S2 (community moderation) routes, which
# sit inside the "social" product (x402_social_store, already in
# _STORE_GATES above) but additionally require x402_social_moderation_enabled.
# Not keyed by product -- flipping it doesn't add a new product key to the
# catalog's product list, only more routes under the existing "social" key --
# so it is reset/enabled by _configure/_all_gates_on like the dicts above but
# deliberately left out of the product-set assertions that iterate
# _STORE_GATES/_BOOL_GATES. Exact repeat of the same lesson: this file's
# gate-enabling helpers must know about every gate shape a product can use,
# or the cross-check tests pass vacuously without exercising the gated routes.
_EXTRA_ROUTE_BOOL_GATES = {"x402_social_moderation_enabled"}


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
    """x402 on, every product store "memory" and every bool gate off unless overridden by `stores`.

    A bool_setting override is passed the same way as a store override (e.g.
    `x402_scan_enabled="true"` or any truthy value coerces via bool()) -- one
    kwargs dict covers both gate shapes so callers don't need to know which
    kind of gate a given product uses.
    """
    monkeypatch.setattr(settings, "x402_enabled", enabled)
    monkeypatch.setattr(settings, "x402_network", ALGORAND_TESTNET_CAIP2)
    monkeypatch.setattr(settings, "x402_pay_to_address", _PAY_TO)
    # Baseline "just make it valid" infra constant, same treatment as
    # x402_pay_to_address/x402_network above -- see _STORE_GATES's own
    # comment on why "storage" needs this set for its single-axis store-gate
    # parametrization to mean what it means for every other product here.
    monkeypatch.setattr(settings, "x402_storage_local_root", _STORAGE_LOCAL_ROOT_PLACEHOLDER)
    for setting in _STORE_GATES.values():
        monkeypatch.setattr(settings, setting, "memory")
    for setting in _BOOL_GATES.values():
        monkeypatch.setattr(settings, setting, False)
    for setting in _EXTRA_ROUTE_BOOL_GATES:
        monkeypatch.setattr(settings, setting, False)
    for setting, value in stores.items():
        monkeypatch.setattr(settings, setting, value)


def _all_gates_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn on every store AND every bool gate -- the "everything is on" cross-check state."""
    _configure(
        monkeypatch,
        **dict.fromkeys(_STORE_GATES.values(), "cassandra"),
        **dict.fromkeys(_BOOL_GATES.values(), True),
        **dict.fromkeys(_EXTRA_ROUTE_BOOL_GATES, True),
    )


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


@pytest.mark.parametrize(("product", "bool_setting"), sorted(_BOOL_GATES.items()))
def test_each_bool_gated_product_matches_create_app(
    monkeypatch: pytest.MonkeyPatch, product: str, bool_setting: str
) -> None:
    """Same cross-check as test_each_product_gate_matches_create_app, for a bool_setting-gated product."""
    _configure(monkeypatch, **{bool_setting: True})
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


def test_storage_requires_both_meta_store_and_local_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """The "storage" product is the one _STORE_GATES entry with a SECOND gate (Product.nonempty_string_setting="x402_storage_local_root") -- neither its store nor its root alone is enough, unlike every other product in that dict.

    test_each_product_gate_matches_create_app above only exercises the store
    half (it relies on _configure()'s baseline always setting a valid
    local_root placeholder); this proves the AND actually holds both ways.
    """
    probe_path = "/api/v1/x402/storage/backups"

    _configure(monkeypatch, x402_storage_meta_store="cassandra", x402_storage_local_root="")
    app = create_app()
    assert not _registered_methods(app, probe_path)
    assert not any(route["product"] == "storage" for route in _catalog_routes(monkeypatch))

    _configure(
        monkeypatch,
        x402_storage_meta_store="memory",
        x402_storage_local_root=_STORAGE_LOCAL_ROOT_PLACEHOLDER,
    )
    app = create_app()
    assert not _registered_methods(app, probe_path)
    assert not any(route["product"] == "storage" for route in _catalog_routes(monkeypatch))

    _configure(
        monkeypatch,
        x402_storage_meta_store="cassandra",
        x402_storage_local_root=_STORAGE_LOCAL_ROOT_PLACEHOLDER,
    )
    app = create_app()
    assert _registered_methods(app, probe_path)
    assert any(route["product"] == "storage" for route in _catalog_routes(monkeypatch))


def test_social_moderation_routes_need_their_own_extra_gate_on_top_of_the_store_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S2 (extra_bool_setting=x402_social_moderation_enabled) is a second, route-level gate.

    Layered on top of "social"'s own x402_social_store gate -- neither gate alone is enough.
    """
    social_routes = next(p for p in catalog_service.PRODUCTS if p.key == "social").routes
    s2_paths = {r.path for r in social_routes if r.extra_bool_setting is not None}
    assert s2_paths, "expected at least one S2 route with extra_bool_setting set"
    s0_s1_paths = {r.path for r in social_routes if r.extra_bool_setting is None}
    assert s0_s1_paths, "expected at least one S0/S1 route with no extra gate"

    # Store gate on, moderation off: S0/S1 routes present, S2 routes absent from both
    # the catalog and the real router.
    _configure(monkeypatch, x402_social_store="cassandra")
    app = create_app()
    listed_paths = {route["path"] for route in _catalog_routes(monkeypatch)}
    assert s0_s1_paths <= listed_paths
    assert listed_paths.isdisjoint(s2_paths)
    for route in social_routes:
        registered = bool(_registered_methods(app, route.path))
        assert registered == (route.path not in s2_paths), route.path

    # Both gates on: S2 routes now present too.
    _configure(monkeypatch, x402_social_store="cassandra", x402_social_moderation_enabled=True)
    app = create_app()
    listed_paths = {route["path"] for route in _catalog_routes(monkeypatch)}
    assert s2_paths <= listed_paths
    for route in social_routes:
        assert _registered_methods(app, route.path), route.path


# --------------------------------------------------------------------------- #
# Roster vs route table
# --------------------------------------------------------------------------- #
def test_every_listed_route_is_registered_when_everything_is_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every route the catalog lists resolves in create_app()'s router with that method."""
    _all_gates_on(monkeypatch)
    app = create_app()
    routes = _catalog_routes(monkeypatch)
    assert {route["product"] for route in routes} == {"catalog", *_STORE_GATES, *_BOOL_GATES}
    for route in routes:
        assert route["method"] in _registered_methods(app, route["path"]), route


def test_every_registered_x402_route_is_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The reverse direction: a paid/free product route added to a module without a roster entry fails here.

    Walks the compiled router for every /api/v1/x402/* and /api/v1/kyc/*
    template create_app() registered and demands a catalog entry per
    (method, path). Admin routes are exempt by design.
    """
    _all_gates_on(monkeypatch)
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
    monkeypatch.setattr(settings, "x402_news_search_price", "$0.07")
    by_key = {(r["method"], r["path"]): r for r in _catalog_routes(monkeypatch)}
    assert by_key[("POST", "/api/v1/x402/list")]["price_usd"] == "$1.23"
    assert by_key[("GET", "/api/v1/x402/news/search")]["price_usd"] == "$0.07"
    assert by_key[("GET", "/api/v1/x402/news/articles/:article_id")]["price_usd"] is None
    assert by_key[("GET", "/api/v1/x402/news/articles/:article_id")]["paid"] is False
    assert by_key[("GET", "/api/v1/x402/search")]["price_usd"] is None
    assert by_key[("GET", "/api/v1/x402/search")]["paid"] is False
    assert by_key[("POST", "/api/v1/x402/list")]["paid"] is True


def test_storage_paid_routes_advertise_price_per_mb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Storage create/renew are per-MB: price_usd is the rate, price_unit is MB, unlike every flat-priced route."""
    _all_gates_on(monkeypatch)
    by_key = {(r["method"], r["path"]): r for r in _catalog_routes(monkeypatch)}
    create = by_key[("POST", "/api/v1/x402/storage/backups")]
    renew = by_key[("POST", "/api/v1/x402/storage/backups/:backup_id/renew")]
    assert create["price_usd"] == settings.x402_storage_price_per_mb
    assert create["price_unit"] == "MB"
    assert renew["price_unit"] == "MB"
    listing = by_key[("POST", "/api/v1/x402/list")]
    assert listing["price_unit"] is None


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


_PROMO_UNWIRED_RESOURCES = {
    # kyc-verify triggers a real payout to the looked-up wallet on a hit
    # (services/payout_service.py) -- a promo bypass there would need its
    # own review of what a $0-amount lookup does to that payout path, so it
    # is deliberately left unwired rather than assumed safe by copying the
    # same pattern as every other paid route.
    "kyc-verify",
    # Every x402_social paid write, REVERSED 2026-09-03: promo was wired,
    # then deliberately removed the same day when a security review found
    # PaymentResult.payer -- unproven under a promo bypass, per
    # modules/x402/promo.py's own docstring -- is fed straight in as the
    # ACTING IDENTITY on every one of these routes (register/post/comment/
    # react/follow/group-create/group-join/report/case-vote). See
    # x402_social/api/routes.py's own module docstring for the incident.
    "x402-social-register",
    "x402-social-post",
    "x402-social-comment",
    "x402-social-react",
    "x402-social-follow",
    "x402-social-group-create",
    "x402-social-group-join",
    "x402-social-report",
    "x402-social-case-vote",
    # Agent Discovery Search (added 2026-09-03): payer here is only payment
    # attribution for a read, not an identity claim like the writes above --
    # promo WOULD be fine in principle -- but this stays consistent with the
    # rest of x402_social's current promo-off stance rather than an
    # independent judgment call. Flagged in the shipping report as a
    # candidate for `supports_promo=True` if this module's promo stance is
    # ever revisited.
    "x402-social-agent-search",
    # x402_storage's two paid routes (backup create, renew): same reasoning
    # as x402_social's reversal above -- PaymentResult.payer is fed straight
    # in as the ROW'S OWNING WALLET (x402_storage_backups is partitioned by
    # it), not just payment attribution. A promo bypass has no proven payer
    # at all (modules/x402/promo.py's own docstring), which would make the
    # backup's owner an unauthenticated caller-supplied identity with no
    # wallet-control proof behind it -- exactly the gap the social reversal
    # closed for the same reason.
    "x402-storage-backup-create",
    "x402-storage-backup-renew",
}


def test_every_paid_route_supports_promo_except_the_unwired_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """supports_promo is opt-in per route -- wired everywhere except _PROMO_UNWIRED_RESOURCES."""
    _all_gates_on(monkeypatch)
    routes = _catalog_routes(monkeypatch)
    by_resource = {route["resource"]: route for route in routes if route["resource"]}
    assert by_resource
    for resource, route in by_resource.items():
        if resource in _PROMO_UNWIRED_RESOURCES:
            assert route["supports_promo"] is False, resource
        else:
            assert route["supports_promo"] is True, resource


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
