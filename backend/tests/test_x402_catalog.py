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
# "storage" needs BOTH this store durable AND a connector root configured
# (see the "storage" Product's own nonempty_string_setting in
# services/catalog.py), so toggling x402_storage_meta_store alone (as
# test_each_product_gate_matches_create_app below does for every entry in
# this dict) is only correct because _configure() below ALWAYS sets
# x402_storage_local_root to a valid placeholder in its baseline --
# test_storage_requires_both_meta_store_and_local_root separately proves the
# second half of the AND actually matters.
_STORE_GATES = {
    "news": "news_store",
    "storage": "x402_storage_meta_store",
}

_STORAGE_LOCAL_ROOT_PLACEHOLDER = "/tmp/x402-storage-catalog-test-root"

# Same idea for a product gated on a plain boolean instead of a store setting
# (Product.bool_setting). This file's "enable everything" helpers must know
# about every gate shape a product can use, or the cross-check tests pass
# vacuously without ever exercising the gated routes.
_BOOL_GATES = {
    "scan": "x402_scan_enabled",
}

# Routes the marketplace no longer has (owner decision 2026-09-10): every one
# must 404 no matter which kept gates are on. One representative path per
# removed product.
_REMOVED_PATHS = (
    "/api/v1/x402/list",
    "/api/v1/x402/search",
    "/api/v1/x402/directory/listings",
    "/api/v1/x402/board",
    "/api/v1/x402/features",
    "/api/v1/x402/requests",
    "/api/v1/x402/grades",
    "/api/v1/x402/social/agents",
    "/api/v1/x402/social/posts",
    "/api/v1/x402/uptime/check",
    "/api/v1/x402/uptime/checks",
    "/api/v1/kyc/verify",
    "/api/v1/kyc/enroll",
    "/api/v1/x402/receipts/x",
    "/api/v1/x402/ping",
)


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
    for setting, value in stores.items():
        monkeypatch.setattr(settings, setting, value)


def _all_gates_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn on every store AND every bool gate -- the "everything is on" cross-check state."""
    _configure(
        monkeypatch,
        **dict.fromkeys(_STORE_GATES.values(), "cassandra"),
        **dict.fromkeys(_BOOL_GATES.values(), True),
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
    _configure(monkeypatch, news_store="cassandra")
    products = {route["product"] for route in _catalog_routes(monkeypatch)}
    assert products == {"catalog", "news"}


def test_catalog_is_empty_of_products_when_x402_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """With x402 off, nothing is registered, so nothing is listed -- a durable store alone does not enable a product."""
    _configure(monkeypatch, enabled=False, news_store="cassandra")
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

    Walks the compiled router for every /api/v1/x402/* template create_app()
    registered and demands a catalog entry per (method, path). Admin routes
    are exempt by design.
    """
    _all_gates_on(monkeypatch)
    app = create_app()
    listed = {(route["method"], route["path"]) for route in _catalog_routes(monkeypatch)}
    registered: set[tuple[str, str]] = set()
    for template, resource in _walk_router(app):
        if not template.startswith("/api/v1/x402"):
            continue
        if template.startswith("/api/v1/admin/"):
            continue
        for method in resource._methods:
            registered.add((method, re.sub(r"\{(\w+)\}", r":\1", template)))
    assert registered == listed


def test_the_registered_route_set_is_exactly_the_kept_products_and_nothing_removed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With every kept gate on, create_app() registers exactly catalog + news + scan + storage (plus the two well-known bootstrap paths) -- and not one path of a removed product.

    Owner decision 2026-09-10: the marketplace is catalog, news, scan and
    storage. This pins the route table itself, so a removed product's
    registration cannot quietly come back through a stray import.
    """
    _all_gates_on(monkeypatch)
    app = create_app()
    registered = {
        template
        for template, _resource in _walk_router(app)
        if template.startswith("/api/v1/x402") or template.startswith("/api/v1/kyc")
    }
    non_admin = {t for t in registered if not t.startswith("/api/v1/admin/")}
    expected = {
        re.sub(r":(\w+)", r"{\1}", route.path)
        for product in catalog_service.PRODUCTS
        for route in product.routes
    }
    assert non_admin == expected
    assert {p.key for p in catalog_service.PRODUCTS} == {"catalog", "news", "scan", "storage"}
    assert [s.key for s in catalog_service.SECTIONS] == ["meta", "services"]

    client = testing.TestClient(app)
    for path in _REMOVED_PATHS:
        assert client.simulate_get(path).status_code == 404, path
        assert client.simulate_post(path).status_code == 404, path
    assert client.simulate_get("/.well-known/x402").status_code == 200
    assert client.simulate_get("/openapi.json").status_code == 200


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
    _all_gates_on(monkeypatch)
    monkeypatch.setattr(settings, "x402_scan_price", "$1.23")
    monkeypatch.setattr(settings, "x402_news_search_price", "$0.07")
    by_key = {(r["method"], r["path"]): r for r in _catalog_routes(monkeypatch)}
    assert by_key[("POST", "/api/v1/x402/scan/url")]["price_usd"] == "$1.23"
    assert by_key[("GET", "/api/v1/x402/news/search")]["price_usd"] == "$0.07"
    assert by_key[("GET", "/api/v1/x402/news/articles/:article_id")]["price_usd"] is None
    assert by_key[("GET", "/api/v1/x402/news/articles/:article_id")]["paid"] is False
    assert by_key[("GET", "/api/v1/x402/news")]["price_usd"] is None
    assert by_key[("GET", "/api/v1/x402/news")]["paid"] is False
    assert by_key[("POST", "/api/v1/x402/scan/url")]["paid"] is True


def test_storage_paid_routes_advertise_price_per_kb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Storage create/renew/add-version are per-KB: price_usd is the rate, price_unit is KB, unlike every flat-priced route."""
    _all_gates_on(monkeypatch)
    by_key = {(r["method"], r["path"]): r for r in _catalog_routes(monkeypatch)}
    create = by_key[("POST", "/api/v1/x402/storage/backups")]
    renew = by_key[("POST", "/api/v1/x402/storage/backups/:backup_id/renew")]
    assert create["price_usd"] == settings.x402_storage_price_per_kb_per_90d
    assert create["price_unit"] == "KB"
    assert renew["price_unit"] == "KB"
    scan = by_key[("POST", "/api/v1/x402/scan/url")]
    assert scan["price_unit"] is None


def test_storage_price_display_is_a_readable_per_mb_rate_not_the_raw_per_kb_float(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """price_display must rescale storage's per-KB rate to a readable per-MB figure.

    2026-09-07 UX audit N15: price_usd for storage's per-KB routes is an unreadable raw
    float ("$0.000001953125"); price_display must rescale it to a sane per-MB figure instead
    of repeating that artifact, while price_usd itself is left untouched (still the exact rate
    create()/renew()/add_version() actually charge from).
    """
    _all_gates_on(monkeypatch)
    monkeypatch.setattr(settings, "x402_storage_price_per_kb_per_90d", "$0.000001953125")
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    by_key = {(r["method"], r["path"]): r for r in _catalog_routes(monkeypatch)}
    create = by_key[("POST", "/api/v1/x402/storage/backups")]
    assert create["price_usd"] == "$0.000001953125"
    assert create["price_display"] == "$0.002 / MB / 90 days"
    # A flat-priced route's price_display is just its already-human price_usd, echoed as-is.
    scan = by_key[("POST", "/api/v1/x402/scan/url")]
    assert scan["price_display"] == scan["price_usd"]
    # A free route has no price_display at all.
    headlines = by_key[("GET", "/api/v1/x402/news")]
    assert headlines["price_display"] is None


def test_products_v2_fields_present_and_status_reflects_actual_gating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every products[] entry carries section/summary/status/entry/auth, status live-computed.

    status is computed from the SAME gate create_app() uses -- never a hand-set flag that
    could drift. With every store gate on and scan's bool gate left OFF, scan must be the
    only product marked "gated"; every other product in the roster is "live".
    """
    _configure(
        monkeypatch,
        news_store="cassandra",
        x402_storage_meta_store="cassandra",
        # x402_scan_enabled is left at _configure's False baseline.
    )
    doc = catalog_service.build_catalog()
    products_by_key = {p["key"]: p for p in doc["products"]}
    roster_by_key = {p.key: p for p in catalog_service.PRODUCTS}
    assert set(products_by_key) == set(roster_by_key)
    section_keys = {s["key"] for s in doc["sections"]}
    for key, product in products_by_key.items():
        for field in ("section", "summary", "status", "entry", "auth"):
            assert product[field], f"{key}.{field} is empty"
        assert product["section"] in section_keys, key
        assert product["status"] in {"live", "gated"}, key
        method, _, path = product["entry"].partition(" ")
        assert method in {"GET", "POST", "PUT", "PATCH", "DELETE"}, key
        assert path.startswith("/api/v1/"), key
        # entry must be one of this product's OWN routes, not a typo pointing elsewhere.
        own_paths = {(r.method, r.path) for r in roster_by_key[key].routes}
        assert (method, path) in own_paths, key
    assert products_by_key["scan"]["status"] == "gated"
    live_keys = {key for key, p in products_by_key.items() if p["status"] == "live"}
    assert live_keys == {product.key for product in catalog_service.PRODUCTS} - {"scan"}


def test_products_v2_gated_products_are_absent_from_routes_but_present_in_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """routes[] excludes gated products even though products[] now lists everything.

    A gated product's routes are not registered (calling one would 404), so routes[] must
    still exclude them -- only products[] gained the "list everything, mark the status" shape.
    """
    _configure(monkeypatch)  # every gate off (the harness's "memory"/False baseline)
    doc = catalog_service.build_catalog()
    products_by_key = {p["key"]: p for p in doc["products"]}
    assert set(products_by_key) == {p.key for p in catalog_service.PRODUCTS}
    assert all(p["status"] == "gated" for k, p in products_by_key.items() if k != "catalog")
    assert {r["product"] for r in doc["routes"]} == {"catalog"}


def test_sections_cover_every_product_and_categories_are_generated_not_hand_written(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`categories`/`description` are generated from which sections have a live product now, never hand-written."""
    _all_gates_on(monkeypatch)
    doc = catalog_service.build_catalog()
    section_keys = {s["key"] for s in doc["sections"]}
    assert section_keys == {"meta", "services"}
    product_sections = {p["section"] for p in doc["products"]}
    assert product_sections <= section_keys
    assert doc["categories"], "categories must not be empty when every product is live"
    assert set(doc["categories"]) <= section_keys
    assert "identity" not in doc["categories"]
    assert "KYA" not in doc["description"]


def test_descriptions_resolve_setting_placeholders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Published descriptions carry live values ("30 days"), never a raw setting name.

    The roster writes `{x402_storage_term_days}`-style placeholders;
    _route_json must resolve every one from settings, so no document surface
    (catalog, /.well-known/x402, /openapi.json) ever shows an agent a config
    attribute name instead of the number it stands for. Rendering every
    description here also makes a typo'd placeholder fail the suite instead
    of raising in production.
    """
    _all_gates_on(monkeypatch)
    monkeypatch.setattr(settings, "x402_storage_term_days", 90)
    routes = _catalog_routes(monkeypatch)
    assert routes
    for route in routes:
        description = route["description"]
        assert "{" not in description, route["path"]
        assert "}" not in description, route["path"]
        assert not re.search(r"\bx402_[a-z0-9_]+\b", description), route["path"]
    by_key = {(r["method"], r["path"]): r for r in routes}
    renew = by_key[("POST", "/api/v1/x402/storage/backups/:backup_id/renew")]
    assert "90-day term" in renew["description"]


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
    assert len(renew_routes) >= 1
    for route in renew_routes:
        text = route.description.lower()
        assert "settles" in text, route.path
        assert "refused" in text, route.path


_PROMO_UNWIRED_RESOURCES = {
    # x402_storage's paid routes (backup create, renew, add-version):
    # PaymentResult.payer is fed straight in as the ROW'S OWNING WALLET
    # (x402_storage_backups is partitioned by it), not just payment
    # attribution. A promo bypass has no proven payer at all
    # (modules/x402/promo.py's own docstring), which would make the backup's
    # owner an unauthenticated caller-supplied identity with no wallet-control
    # proof behind it; add_version's and renew's ownership checks compare
    # result.payer straight against that owning wallet.
    "x402-storage-backup-create",
    "x402-storage-backup-renew",
    "x402-storage-backup-add-version",
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
    assert [a["symbol"] for a in mainnet["assets"]] == ["USDC", "EURQ", "USDQ", "goBTC"]
    assert mainnet["assets"][0]["asa_id"] == 31566704


# --------------------------------------------------------------------------- #
# Rate limit
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# Recent-settlements proof-of-volume feed
# --------------------------------------------------------------------------- #
def test_recent_settlements_json_empty_state_points_to_facilitator_merchant_page() -> None:
    """An empty recent window is honest, not a bare `{"items": []}` with no context.

    Real (non-operator) third-party volume is genuinely low this early --
    the response must say so plainly and point somewhere independently
    verifiable, never fabricate an entry to fill the gap (CLAUDE.md section 9).
    """
    from app.modules.x402.settlement import InMemorySettlementStore, set_settlement_store

    set_settlement_store(InMemorySettlementStore())
    try:
        result = catalog_service.recent_settlements_json()
        assert result["items"] == []
        assert result["note"]
        assert "facilitator" in result["note"].lower()
        assert result["verify_at"] == catalog_service._GOPLAUSIBLE_MERCHANT_URL
        assert result["verify_at"].startswith("https://facilitator.goplausible.xyz/")
    finally:
        set_settlement_store(None)


def test_recent_settlements_json_nonempty_has_no_empty_state_extras() -> None:
    """A non-empty window is just the plain items list -- no note/verify_at clutter."""
    from datetime import UTC, datetime

    from app.modules.x402.settlement import (
        InMemorySettlementStore,
        SettlementRecord,
        set_settlement_store,
    )

    store = InMemorySettlementStore()
    store.record_settlement(
        SettlementRecord(
            tx_id="tx-1",
            asset_id="31566704",
            amount_atomic="1000",
            payer="Q" * 58,
            resource="x402-news-search",
            network=ALGORAND_MAINNET_CAIP2,
            settled_at_epoch=int(datetime.now(tz=UTC).timestamp()),
            eur_value=0.001,
            fulfilled=True,
        )
    )
    set_settlement_store(store)
    try:
        result = catalog_service.recent_settlements_json()
        assert [item["tx_id"] for item in result["items"]] == ["tx-1"]
        assert "note" not in result
        assert "verify_at" not in result
    finally:
        set_settlement_store(None)


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
