"""The x402 product roster and the catalog document built from it.

PRODUCTS is a static list: every x402 product route this backend can
register, which settings attribute gates the product's registration in
falcon_main.py, and which settings attribute holds each paid route's price.
Prices and the gate are READ from settings at request time, never copied
here, so the catalog cannot drift from what the 402 offer actually charges
or from which routes create_app() actually registered.

The gate mirrors falcon_main.py exactly: `settings.x402_enabled` must be on,
and the product's own store setting must not be "memory". A product whose
store is "memory" is not registered there (a clean 404, nothing charged) and
is therefore not listed here either. tests/test_x402_catalog.py cross-checks
this roster against create_app()'s real route table in both directions.

Admin routes (`/api/v1/admin/...`) are deliberately absent: they are wallet-
gated operator tools, not marketplace products.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from x402.mechanisms.avm.constants import ALGORAND_MAINNET_CAIP2, ALGORAND_TESTNET_CAIP2

from app.core.config import settings
from app.modules.x402.assets import ACCEPTED_ASSETS
from app.modules.x402.client import CHALLENGE_TAG

CATALOG_PATH = "/api/v1/x402"

_NETWORK_NAMES = {ALGORAND_MAINNET_CAIP2: "mainnet", ALGORAND_TESTNET_CAIP2: "testnet"}


@dataclass(frozen=True)
class CatalogRoute:
    """One product route as the catalog describes it.

    `price_setting` names the settings attribute holding the route's Money
    price; None means the route is free. `resource` is the stable id the 402
    offer and the settlement ledger use for the route (paid routes only).
    `input_example` mirrors the example the route declares in its Bazaar
    discovery extension, where it declares one.
    """

    method: str
    path: str
    description: str
    price_setting: str | None = None
    resource: str | None = None
    input_example: dict[str, Any] | None = None
    # True for a paid route that also accepts `?preview=true` (see
    # app/modules/x402/preview.py): a redacted, unpaid, rate-limited response
    # of the same JSON shape. False (the default) for every route until it is
    # explicitly wired -- preview is opt-in per route, not automatic.
    supports_preview: bool = False
    # True for a paid route that also accepts an admin-issued
    # `?promo=CODE&promo_wallet=ADDRESS` bypass (see app/modules/x402/promo.py):
    # a real (non-redacted) response with no settlement. False (the default)
    # for every route until it is explicitly wired -- same opt-in-per-route
    # shape as supports_preview. There is no public way to create a promo
    # code; this flag only says a route honors one if presented.
    supports_promo: bool = False

    @property
    def paid(self) -> bool:
        """True when the route sits behind the payment gate."""
        return self.price_setting is not None


@dataclass(frozen=True)
class Product:
    """One x402 product: its routes and the store setting gating its registration.

    `store_setting` is the settings attribute falcon_main.py compares against
    "memory" before registering the product. None means the product has no
    store gate of its own and is registered whenever x402 is enabled (only the
    catalog itself).
    """

    key: str
    title: str
    store_setting: str | None
    routes: tuple[CatalogRoute, ...]

    def enabled(self) -> bool:
        """The same condition falcon_main.py registers this product under."""
        if not settings.x402_enabled:
            return False
        if self.store_setting is None:
            return True
        return getattr(settings, self.store_setting) != "memory"


_EXAMPLE_URL = "https://api.example.com/v1/quote"

PRODUCTS: tuple[Product, ...] = (
    Product(
        key="catalog",
        title="Catalog",
        store_setting=None,
        routes=(
            CatalogRoute(
                method="GET",
                path=CATALOG_PATH,
                description="This document: every live x402 route, its price and how to pay.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/settlements/recent",
                description=(
                    "Free, rate-limited proof-of-volume feed: real (non-operator) "
                    "settlements across every product, newest first, bounded. Our own "
                    "probe payments are excluded, never counted here as customer volume."
                ),
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/ping",
                description=(
                    "The marketplace's lowest price, for testing that your x402 client can "
                    "build, sign and settle a real payment here before risking money on a "
                    "real product. No product data in the response, just a receipt. "
                    "Supports ?preview=true (redacted, unpaid, rate-limited) and an "
                    "admin-issued ?promo=CODE&promo_wallet=ADDRESS bypass, as the reference "
                    "for every future route that wires either in."
                ),
                price_setting="x402_ping_price",
                resource="x402-ping",
                input_example=None,
                supports_preview=True,
                supports_promo=True,
            ),
        ),
    ),
    Product(
        key="directory",
        title="Endpoint directory",
        store_setting="x402_directory_store",
        routes=(
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/list",
                description=(
                    "List one x402 endpoint in the public directory for "
                    "x402_listing_term_days days (JSON body: url, price, description, "
                    "assets, tags, category, schema)."
                ),
                price_setting="x402_listing_price",
                resource="x402-directory-list",
                input_example={
                    "url": _EXAMPLE_URL,
                    "price": "$0.01",
                    "description": "Live FX quote, one currency pair per call.",
                    "assets": ["USDC"],
                    "tags": ["fx", "market-data"],
                    "category": "finance",
                },
                supports_promo=True,
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/list/renew",
                description=(
                    "Extend an existing listing by x402_listing_term_days more days from "
                    "the later of now and its term end (JSON body: url); owner only while "
                    "the listing is live -- a payment from any other wallet settles but is "
                    "refused (403) and changes nothing."
                ),
                price_setting="x402_listing_price",
                resource="x402-directory-renew",
                input_example={"url": _EXAMPLE_URL},
                supports_promo=True,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/search",
                description=(
                    "Unexpired directory listings, newest first; optional ?tag=, "
                    "?category= and ?limit=."
                ),
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/listings",
                description=(
                    "One live listing in full plus its newest probe result, for ?url=; "
                    "404 if unlisted or expired."
                ),
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/directory/probe",
                description=(
                    "Newest unpaid probe result (reachability, latency, 402 validity) "
                    "for one listed ?url=."
                ),
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/directory/probe/history",
                description=(
                    "Up to x402_probe_history_max_results past probe results "
                    "(reachability, latency, 402 validity), newest first, for one "
                    "listed ?url=; optional ?limit=. Free -- real measured uptime "
                    "history as a trust signal, not its own paid product."
                ),
                input_example={"url": _EXAMPLE_URL},
            ),
        ),
    ),
    Product(
        key="board",
        title="Visibility board",
        store_setting="x402_board_store",
        routes=(
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/board",
                description=(
                    "Place one link, name and pitch on the public board for "
                    "x402_board_term_days days (JSON body)."
                ),
                price_setting="x402_board_price",
                resource="x402-board-place",
                input_example={
                    "link": "https://agent.example.com",
                    "name": "Example Agent",
                    "pitch": "Autonomous FX arbitrage agent. Live on Algorand since 2026.",
                },
                supports_promo=True,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/board",
                description="Live board placements with click counts, newest first; ?limit=.",
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/board/:entry_id/renew",
                description=(
                    "Extend your own placement by one more term; a payment from any other "
                    "wallet settles but is refused (403)."
                ),
                price_setting="x402_board_price",
                resource="x402-board-renew",
                supports_promo=True,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/board/:entry_id/go",
                description="302 to a live placement's link, counting the click-through.",
            ),
        ),
    ),
    Product(
        key="features",
        title="Feature-request board",
        store_setting="x402_features_store",
        routes=(
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/features",
                description="File one anonymous feature request (JSON body: title, description).",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/features",
                description="Filed requests without vote totals, newest first; ?limit=.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/features/demand",
                description="Requests ranked by paid demand, vote totals included; ?limit=.",
                price_setting="x402_features_demand_price",
                resource="x402-features-demand",
                input_example={"limit": 25},
                supports_promo=True,
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/features/:request_id/vote",
                description="Add one unit of paid demand to a request; paying again votes again.",
                price_setting="x402_features_vote_price",
                resource="x402-features-vote",
                supports_promo=True,
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/features/:request_id/claim",
                description="Publicly declare your wallet is building a request.",
                price_setting="x402_features_vote_price",
                resource="x402-features-claim",
                supports_promo=True,
            ),
        ),
    ),
    Product(
        key="grading",
        title="Endpoint grading",
        store_setting="x402_grading_store",
        routes=(
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/grades",
                description=(
                    "Grade any http(s) x402 endpoint 1-5 (JSON body: url, score, comment); "
                    "one grade per wallet per URL, re-grading replaces."
                ),
                price_setting="x402_grading_grade_price",
                resource="x402-grading-submit",
                input_example={
                    "url": _EXAMPLE_URL,
                    "score": 4,
                    "comment": "Accurate quotes, ~300ms, spec matched the 402 offer exactly.",
                },
                supports_promo=True,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/grades",
                description="Every endpoint with at least one grade, no scores; ?limit=.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/grades/score",
                description=(
                    "Credibility-weighted aggregate, distribution and every grade for one ?url=."
                ),
                price_setting="x402_grading_score_price",
                resource="x402-grading-score",
                input_example={"url": _EXAMPLE_URL},
                supports_promo=True,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/grades/summary",
                description="Grade count and last-graded time for one ?url=, never a score.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/grades/top",
                description="Top graded endpoints among directory listings carrying one ?tag=.",
                price_setting="x402_grading_score_price",
                resource="x402-grading-top",
                input_example={"tag": "pricing"},
                supports_promo=True,
            ),
        ),
    ),
    Product(
        key="news",
        title="News Engine",
        store_setting="news_store",
        routes=(
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/news",
                description="Latest published headlines with ids and slugs; ?tag= and ?limit=.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/news/search",
                description="Ranked full-text search over every published article; ?q= and ?limit=.",
                price_setting="x402_news_search_price",
                resource="x402-news-search",
                input_example={"q": "tinyman volume", "limit": 10},
                supports_promo=True,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/news/articles/:article_id",
                description=(
                    "Free: one published article in full (body markdown, sources, "
                    "translations) by uuid or slug."
                ),
                input_example={"article_id": "tinyman-v2-crosses-1b-cumulative-volume"},
            ),
        ),
    ),
    Product(
        key="kya",
        title="Know Your Agent",
        store_setting="kyc_store",
        routes=(
            CatalogRoute(
                method="GET",
                path="/api/v1/kyc/consent-message",
                description="The consent message a wallet signs to enrol; ?wallet_address=.",
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/kyc/enroll",
                description=(
                    "Enrol a wallet with its signed consent (JSON body: wallet_address, "
                    "consent_signature_b64); trust signals computed from the public indexer."
                ),
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/kyc/verify",
                description=(
                    "Check one ?wallet='s KYA status; charged on a miss too, half the fee "
                    "is paid out to the enrolled wallet on a hit."
                ),
                price_setting="kyc_lookup_price",
                resource="kyc-verify",
                input_example={"wallet": "ALGORAND_ADDRESS"},
            ),
        ),
    ),
)


def enabled_products() -> list[Product]:
    """The products create_app() registered under the current settings, roster order."""
    return [product for product in PRODUCTS if product.enabled()]


def _asset_json(network: str) -> list[dict[str, Any]]:
    """The assets the payment gate can offer on `network`, offer order (USDC first).

    Mirrors assets.py's per-network existence rule. The live 402 offer may
    still omit a non-USDC asset whose USD rate is momentarily unknown (see
    client.build_payment_offer); the offer is authoritative per request.
    """
    payload = []
    for asset in ACCEPTED_ASSETS:
        asa_id = asset.asa_id_for(network)
        if asa_id is None:
            continue
        payload.append({"symbol": asset.symbol, "asa_id": asa_id, "decimals": asset.decimals})
    return payload


def _route_json(product: Product, route: CatalogRoute) -> dict[str, Any]:
    return {
        "product": product.key,
        "method": route.method,
        "path": route.path,
        "paid": route.paid,
        "price_usd": getattr(settings, route.price_setting) if route.price_setting else None,
        "resource": route.resource,
        "description": route.description,
        "input_example": route.input_example,
        "supports_preview": route.supports_preview,
        "supports_promo": route.supports_promo,
    }


def recent_settlements_json(*, limit: int = 25) -> dict[str, Any]:
    """Public JSON for the free proof-of-volume feed.

    Reads modules/x402/settlement.py's recent_real_settlements() (already
    probe-payer-excluded) and serializes it with human units and asset
    symbols, never raw atomic amounts a reader would have to decode.
    """
    from app.modules.x402.assets import asset_for_asa_id
    from app.modules.x402.settlement import EUR_VALUE_UNAVAILABLE, recent_real_settlements

    records = recent_real_settlements(limit=limit)
    items = []
    for r in records:
        asset = asset_for_asa_id(r.asset_id, r.network)
        amount = None
        if asset is not None:
            try:
                amount = int(r.amount_atomic) / (10**asset.decimals)
            except (TypeError, ValueError):
                amount = None
        items.append(
            {
                "tx_id": r.tx_id,
                "asset": asset.symbol if asset is not None else r.asset_id,
                "amount": amount,
                "payer": r.payer,
                "resource": r.resource,
                "settled_at_epoch": r.settled_at_epoch,
                "eur_value": None if r.eur_value == EUR_VALUE_UNAVAILABLE else r.eur_value,
                "fulfilled": r.fulfilled,
            }
        )
    return {"items": items}


def build_catalog() -> dict[str, Any]:
    """The catalog document: marketplace-wide payment facts plus every live route."""
    network = settings.x402_network
    products = enabled_products()
    return {
        "name": "PXke x402 marketplace",
        "network": network,
        "network_name": _NETWORK_NAMES.get(network, "unknown"),
        "pay_to": settings.x402_pay_to_address,
        "facilitator_url": settings.x402_facilitator_url,
        "challenge_tag": CHALLENGE_TAG,
        "payment_scheme": "exact",
        "assets": _asset_json(network),
        "products": [{"key": p.key, "title": p.title} for p in products],
        "routes": [_route_json(p, route) for p in products for route in p.routes],
    }
