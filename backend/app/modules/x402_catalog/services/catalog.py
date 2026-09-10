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

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
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
    price; None means the route is free. `price_unit` is None for a flat
    `price_usd`, or a unit name ("KB") when `price_usd` is a per-unit rate
    rather than the amount actually charged -- storage's paid routes multiply
    this rate by ceil(size/1KB) and by retention_days/x402_storage_term_days
    (floored at x402_storage_price_floor) in the live 402 offer. `resource`
    is the stable id the 402 offer and the settlement ledger use (paid
    routes only).
    `input_example` mirrors the example the route declares in its Bazaar
    discovery extension, where it declares one.
    """

    method: str
    path: str
    description: str
    price_setting: str | None = None
    resource: str | None = None
    price_unit: str | None = None
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


def _renamed(route: CatalogRoute, path: str) -> CatalogRoute:
    """A second, alias CatalogRoute entry at `path` for a route the ux audit renamed.

    docs/x402-marketplace-ux-audit.md section 3.3 renames these paths for
    clarity; section 3.5 requires the old path to keep working forever (this
    marketplace is live on mainnet), so both stay listed. Derived with
    dataclasses.replace rather than a second literal so price/resource/promo/
    preview can never drift from the route this aliases -- the
    handler backing both paths is, and must stay, the identical Python
    function (register_x402_*_routes registers the alias path against it
    directly; see the module's own docstring).
    """
    return replace(route, path=path)


@dataclass(frozen=True)
class Section:
    """One entry in the catalog's `sections[]` -- a visitor-intent grouping, not an author boundary.

    Every Product names exactly one section by key (`Product.section`).
    Sections are listed in the fixed order SECTIONS declares them, and
    `products[]` is emitted in that same section order (see build_catalog),
    so an agent reading the document top-to-bottom sees "how to pay" before
    "what to buy", not roster insertion order.
    """

    key: str
    title: str
    summary: str


SECTIONS: tuple[Section, ...] = (
    Section(
        key="meta",
        title="Start here",
        summary="Learn how to pay, and see the real settlements this marketplace has taken.",
    ),
    Section(
        key="services",
        title="Services",
        summary="PXke's own pay-per-call products: news, file scanning, backup storage.",
    ),
)

_SECTION_ORDER: dict[str, int] = {section.key: i for i, section in enumerate(SECTIONS)}


@dataclass(frozen=True)
class Product:
    """One x402 product: its routes and the gate(s) on its registration.

    `store_setting` is the settings attribute falcon_main.py compares against
    "memory" before registering the product. None means the product has no
    store gate of its own.
    `bool_setting` is a plain boolean settings attribute that must be True
    (e.g. x402_scan_enabled -- a stateless prototype product with no store of
    its own to gate on). None means no such gate. Found missing 2026-09-01:
    x402_scan was registered in falcon_main.py and live-reachable but had no
    PRODUCTS entry at all, so it never appeared in the catalog or
    /.well-known/x402 -- store_setting alone had no way to express a plain
    boolean gate, so a product like this was easy to add to falcon_main.py
    and simply forget here.
    `nonempty_string_setting` is a plain string settings attribute that must
    be non-empty once trimmed (e.g. x402_storage_local_root -- "empty path =
    disabled", the same convention geoip_db_path uses). None means no such
    gate. Added for x402_storage (2026-09-04): its registration needs BOTH
    its own store durable AND a connector root actually configured, and
    neither store_setting nor bool_setting alone could express the second,
    inherently-a-path condition.
    All set gates apply (AND'd); a product with none of the three (only the
    catalog itself) is registered whenever x402 is enabled.

    `section` names one SECTIONS key. `summary` is one sentence for a reader
    who has never seen this product before -- not the title restated.
    `entry` is the one route (as "METHOD /path") worth calling first to use
    this product. `auth` is a short plain-language description of what a
    caller needs: free reads, a settled payment, a wallet signature, etc.
    """

    key: str
    title: str
    store_setting: str | None
    routes: tuple[CatalogRoute, ...]
    section: str
    summary: str
    entry: str
    auth: str
    bool_setting: str | None = None
    nonempty_string_setting: str | None = None

    def enabled(self) -> bool:
        """The same condition falcon_main.py registers this product under."""
        if not settings.x402_enabled:
            return False
        if self.store_setting is not None and getattr(settings, self.store_setting) == "memory":
            return False
        if self.bool_setting is not None and not bool(getattr(settings, self.bool_setting)):
            return False
        return self.nonempty_string_setting is None or bool(
            str(getattr(settings, self.nonempty_string_setting)).strip()
        )

    def status(self) -> str:
        """Live once enabled() is true, else gated -- computed, never hand-set, so it cannot drift."""
        return "live" if self.enabled() else "gated"


_EXAMPLE_URL = "https://api.example.com/v1/quote"

# The facilitator's own merchant id for our payTo (keyed by the facilitator
# itself via data/merchants/{id}, not something we assign -- same id echoed
# into build_catalog()'s "merchant" block below). Its public merchant page
# shows independently-verifiable settle count and success rate, so an empty
# recent_settlements_json() result (genuinely low third-party volume so far,
# see that function's own docstring) can point somewhere honest instead of
# reading as "this feed is broken" or "this marketplace has zero activity".
_GOPLAUSIBLE_MERCHANT_ID = "3e5946af2c9756b6"
_GOPLAUSIBLE_MERCHANT_URL = (
    f"https://facilitator.goplausible.xyz/data/merchants/{_GOPLAUSIBLE_MERCHANT_ID}"
)

PRODUCTS: tuple[Product, ...] = (
    Product(
        key="catalog",
        title="Catalog",
        store_setting=None,
        section="meta",
        summary=(
            "The merchant-wide discovery document: every live route, its price and how to "
            "pay, generated fresh from what create_app() actually registered."
        ),
        entry="GET /api/v1/x402",
        auth="free",
        routes=(
            CatalogRoute(
                method="GET",
                path=CATALOG_PATH,
                description="This document: every live x402 route, its price and how to pay.",
            ),
            (
                _settlements_recent := CatalogRoute(
                    method="GET",
                    path="/api/v1/x402/settlements/recent",
                    description=(
                        "Free, rate-limited proof-of-volume feed: real (non-operator) "
                        "settlements across every product, newest first, bounded. Our own "
                        "probe payments are excluded, never counted here as customer volume. "
                        "An empty recent window includes a pointer to our facilitator "
                        "merchant page for independent verification, rather than a bare "
                        "empty list."
                    ),
                )
            ),
            # x402-marketplace-ux-audit.md section 3.3 "meta": /recent was the
            # feed's only mode, so /settlements is the clarified name; both
            # are registered to the identical handler, old path never removed.
            _renamed(_settlements_recent, "/api/v1/x402/settlements"),
        ),
    ),
    Product(
        key="news",
        title="News Engine",
        store_setting="news_store",
        section="services",
        summary=(
            "Pay-per-call access to the newspaper's headlines, tag taxonomy, full-text "
            "search and full article bodies."
        ),
        entry="GET /api/v1/x402/news",
        auth="free headlines, tags and articles; paid full-text search",
        routes=(
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/news",
                description=(
                    "Latest published headlines with ids and slugs; ?tag=, ?limit= and "
                    "?lang= (title/summary in a stored translation language where one "
                    "exists)."
                ),
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/news/tags",
                description=(
                    "Free: the newspaper's live tag taxonomy -- per-tag article count, "
                    "readership and recency, sorted by coverage; ?limit=. The discovery "
                    "surface for the headline list's ?tag= filter."
                ),
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/news/search",
                description=(
                    "Ranked full-text search over every published article; ?q= and "
                    "?limit=. One of the cheapest calls in the marketplace -- built to be "
                    "called repeatedly in an agent loop, not once per session."
                ),
                price_setting="x402_news_search_price",
                resource="x402-news-search",
                input_example={"q": "tinyman volume", "limit": 10},
                supports_promo=True,
                supports_preview=True,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/news/articles/:article_id",
                description=(
                    "Free: one published article in full (body markdown, sources, "
                    "translations) by uuid or slug; ?lang= serves a stored translation "
                    "(the payload's lang field says which language was actually served)."
                ),
                input_example={"article_id": "tinyman-v2-crosses-1b-cumulative-volume"},
            ),
        ),
    ),
    Product(
        key="scan",
        title="Sandboxed file/tarball scan",
        store_setting=None,
        section="services",
        summary=(
            "Fetch a URL server-side and run it through a network-isolated malware/archive "
            "scan that never executes the target."
        ),
        entry="POST /api/v1/x402/scan/url",
        auth="paid per scan",
        bool_setting="x402_scan_enabled",
        routes=(
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/scan/url",
                description=(
                    "Static security scan of a file fetched from a URL: known-malware "
                    "signature match (ClamAV), file-type verification, entropy, embedded "
                    "URL/IP extraction, and a zip/tar-bomb-safe archive member listing "
                    "with the same checks applied per member. The target is downloaded "
                    "server-side and scanned in a network-isolated sandbox that never "
                    "executes it. Supports ?preview=true for a free, redacted, unpaid, "
                    "rate-limited fixed example of the same response shape."
                ),
                price_setting="x402_scan_price",
                resource="x402-scan-url",
                input_example={"url": _EXAMPLE_URL},
                supports_preview=True,
                supports_promo=True,
            ),
        ),
    ),
    Product(
        key="storage",
        title="Agent backup storage",
        store_setting="x402_storage_meta_store",
        section="services",
        summary=(
            "Store an opaque backup blob billed by the KB actually used, retrieved later by "
            "proving control of the same wallet -- no session, no login."
        ),
        entry="POST /api/v1/x402/storage/backups",
        auth="paid to write, by the KB; free wallet-signature-authenticated reads",
        nonempty_string_setting="x402_storage_local_root",
        routes=(
            (
                _storage_auth_challenge := CatalogRoute(
                    method="POST",
                    path="/api/v1/x402/storage/auth/challenge",
                    description=(
                        "Free: mint a short-TTL, single-use nonce for one wallet to sign, "
                        "proving control for exactly one following call to GET/DELETE "
                        "/storage/backups (JSON body: wallet). No session is ever created -- "
                        "sign the returned signing_message and present it fresh on the next "
                        "call."
                    ),
                )
            ),
            # x402-marketplace-ux-audit.md section 3.3 "identity primitive"
            # (N6): social's "auth/challenge" returns a session-token bearer;
            # this route's "challenge" is a single-use nonce -- opposite
            # contract, same path segment. /auth/nonce disambiguates; the old
            # path is never removed (section 3.5).
            _renamed(_storage_auth_challenge, "/api/v1/x402/storage/auth/nonce"),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/storage/backups",
                description=(
                    "Store one opaque backup blob (JSON body: base64 data, optional "
                    "label; query param declared_size_bytes=<N>, capped at "
                    "{x402_storage_max_backup_mb} MB, billed by the KB actually used, "
                    "never rounded up to a whole MB; optional query param retention_days=<1-"
                    "{x402_storage_term_days}>, default {x402_storage_term_days}), priced "
                    "at max({x402_storage_price_floor}, ceil(N/1KB) * "
                    "{x402_storage_price_per_kb_per_90d} per KB * "
                    "retention_days/{x402_storage_term_days}). Overall remaining life is "
                    "still capped at {x402_storage_max_remaining_days} days from now. "
                    "Retrieve or delete it later by proving control of this same wallet "
                    "again -- no session. Stored content is OPAQUE: never scanned, "
                    "indexed, or acted on by us. Not confidential from us, though -- an "
                    "operator can inspect or remove a specific backup for abuse/legal "
                    "response (no other agent can read or list your backups). Encrypt "
                    "sensitive data yourself before uploading if that matters to you -- "
                    "see docs/x402-storage-encryption-guide.md for a copy-paste recipe. "
                    "Need more than one version? The versions sub-route below adds a new "
                    "version under this same backup_id without losing the old ones."
                ),
                price_setting="x402_storage_price_per_kb_per_90d",
                resource="x402-storage-backup-create",
                price_unit="KB",
                input_example={"data": "<base64>", "label": "my-agent-state-backup"},
                supports_promo=False,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/storage/backups",
                description=(
                    "Free, wallet-signature-authenticated (query params: wallet, nonce, "
                    "proof_method, signature_b64 from the challenge above): this wallet's "
                    "own active, unexpired backups, newest first."
                ),
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/storage/backups/:backup_id",
                description=(
                    "Free, wallet-signature-authenticated, owner-only: one backup's metadata "
                    "(including current_version) plus its CURRENT version's restored bytes "
                    "(base64), sha256-verified against the hash taken at upload time. See "
                    "the versions sub-routes below to fetch an older version instead."
                ),
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/storage/backups/:backup_id/renew",
                description=(
                    "Extend an existing backup's retrieval window by one full "
                    "{x402_storage_term_days}-day term, capped at "
                    "{x402_storage_max_remaining_days} days remaining from now (JSON body: "
                    "wallet), priced from the backup's already-stored size at "
                    "max({x402_storage_price_floor}, ceil(size/1KB) * "
                    "{x402_storage_price_per_kb_per_90d} per KB) -- always a full term, "
                    "never a caller-chosen shorter retention (that is create()/"
                    "add-version()'s own parameter, not this route's). Works during the "
                    "{x402_storage_reaper_grace_days}-day grace window after expiry. "
                    "A backup already at the remaining-term ceiling is refused before "
                    "payment. Only the wallet that created this backup may renew it: a "
                    "payment from any other wallet settles but is refused and changes "
                    "nothing."
                ),
                price_setting="x402_storage_price_per_kb_per_90d",
                resource="x402-storage-backup-renew",
                price_unit="KB",
                input_example={"wallet": "A" * 58},
                supports_promo=False,
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/storage/backups/:backup_id/versions",
                description=(
                    "Store a NEW version's bytes under an EXISTING backup_id without "
                    "discarding the versions already stored under it (query param "
                    "declared_size_bytes=<N>, billed by the KB actually used; optional "
                    "query param retention_days=<1-{x402_storage_term_days}>, default "
                    "{x402_storage_term_days}, priced identically to the initial store: "
                    "max({x402_storage_price_floor}, ceil(N/1KB) * "
                    "{x402_storage_price_per_kb_per_90d} per KB * "
                    "retention_days/{x402_storage_term_days}); JSON body: base64 data, "
                    "optional label; wallet as a query param, same lookup-hint shape as "
                    "renew above). This version becomes the backup's current content, on "
                    "its own independent expiry sized by its own retention_days -- older "
                    "versions keep expiring on their own original schedule, and the "
                    "backup's own overall retrieval window (capped at "
                    "{x402_storage_max_remaining_days} days) is untouched by this call. "
                    "Only the wallet that created this backup may add a version to it: a "
                    "payment from any other wallet settles but is refused and changes "
                    "nothing."
                ),
                price_setting="x402_storage_price_per_kb_per_90d",
                resource="x402-storage-backup-add-version",
                price_unit="KB",
                input_example={"data": "<base64>", "label": "my-agent-state-backup-v2"},
                supports_promo=False,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/storage/backups/:backup_id/versions",
                description=(
                    "Free, wallet-signature-authenticated, owner-only: metadata (version "
                    "number, size, content_hash, created_at/expires_at) for every version "
                    "of one backup, newest-version first."
                ),
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/storage/backups/:backup_id/versions/:version",
                description=(
                    "Free, wallet-signature-authenticated, owner-only: one specific "
                    "version's metadata plus its restored bytes (base64), sha256-verified."
                ),
            ),
            CatalogRoute(
                method="DELETE",
                path="/api/v1/x402/storage/backups/:backup_id",
                description=(
                    "Free, wallet-signature-authenticated, owner-only: delete one backup "
                    "outright, every version included (row marked deleted, then connector "
                    "bytes removed)."
                ),
            ),
        ),
    ),
)


def enabled_products() -> list[Product]:
    """The products create_app() registered under the current settings, roster order."""
    return [product for product in PRODUCTS if product.enabled()]


def _ordered_products() -> list[Product]:
    """Every product in PRODUCTS (live and gated), section order then roster order.

    Unlike `enabled_products()`, this does NOT drop a gated product -- the
    document's `products[]` lists every product this backend can register,
    each carrying its own live-computed `status` (Product.status()), so an
    agent can see "this exists, but is gated off" instead of the product
    silently vanishing. `routes[]` stays scoped to enabled_products() below: a gated product's routes are
    not registered, so listing them would point an agent at a 404.
    """
    indexed = sorted(
        enumerate(PRODUCTS), key=lambda item: (_SECTION_ORDER[item[1].section], item[0])
    )
    return [product for _, product in indexed]


def _product_json(product: Product) -> dict[str, Any]:
    return {
        "key": product.key,
        "title": product.title,
        "section": product.section,
        "summary": product.summary,
        "status": product.status(),
        "entry": product.entry,
        "auth": product.auth,
    }


def _live_section_keys() -> list[str]:
    """SECTIONS keys with at least one live product, in SECTIONS order."""
    live = {p.section for p in PRODUCTS if p.enabled()}
    return [section.key for section in SECTIONS if section.key in live]


def _generated_description() -> str:
    """One sentence naming only the sections with a live product right now, never hand-written."""
    titles = [section.title.lower() for section in SECTIONS if section.key in _live_section_keys()]
    if not titles:
        return "Composite x402 marketplace on Algorand, currently registering no products."
    return "Composite x402 marketplace on Algorand: " + "; ".join(titles) + ". All under one payTo."


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


class _SettingsFormatMap(dict):
    """format_map source resolving `{setting_name}` placeholders to live values.

    Lets a route description say "for {x402_storage_term_days} days" and have
    the published document read "for 90 days" -- the value is read from
    settings at request time, same no-drift rule as prices. An unknown
    placeholder raises AttributeError at request time; the roster tests
    render every description, so a typo fails the suite, not production.
    """

    def __missing__(self, key: str) -> object:
        return getattr(settings, key)


def _resolved_description(route: CatalogRoute) -> str:
    if "{" not in route.description:
        return route.description
    return route.description.format_map(_SettingsFormatMap())


def _price_display(route: CatalogRoute) -> str | None:
    """Human-scale price string for `_route_json`, e.g. "$0.002 / MB / 90 days".

    None for a free route. A flat `price_usd` (route.price_unit is None) is
    already human-scale ("$0.02") and is echoed as-is. A per-KB rate
    (route.price_unit == "KB", storage's own convention -- see CatalogRoute's
    docstring) is rescaled to per-MB, because a cent-scale per-KB rate reads
    as an unreadable string of leading zeros -- "$0.000001953125 / KB", the
    exact artifact the 2026-09-07 UX audit flagged as N15. The scaled figure
    is computed from the live settings at request time, the same no-drift
    rule `price_usd` itself already follows -- never a second hand-typed
    number, and it does not change what create()/renew()/add_version() (see
    x402_storage's own routes) actually charge, which still floors and
    scales by the caller's chosen retention_days at request time.
    """
    if not route.paid:
        return None
    raw = getattr(settings, route.price_setting)
    if route.price_unit != "KB":
        return raw
    try:
        per_kb = Decimal(str(raw).lstrip("$"))
    except InvalidOperation:
        return f"{raw} / {route.price_unit}"
    per_mb = (per_kb * 1024).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    text = f"{per_mb:.4f}".rstrip("0")
    if text.endswith("."):
        text += "00"
    elif len(text.split(".", 1)[1]) < 2:
        text += "0" * (2 - len(text.split(".", 1)[1]))
    return f"${text} / MB / {settings.x402_storage_term_days} days"


def _route_json(product: Product, route: CatalogRoute) -> dict[str, Any]:
    return {
        "product": product.key,
        "method": route.method,
        "path": route.path,
        "paid": route.paid,
        "price_usd": getattr(settings, route.price_setting) if route.price_setting else None,
        "price_unit": route.price_unit,
        "price_display": _price_display(route),
        "resource": route.resource,
        "description": _resolved_description(route),
        "input_example": route.input_example,
        "supports_preview": route.supports_preview,
        "supports_promo": route.supports_promo,
    }


def recent_settlements_json(*, limit: int = 25) -> dict[str, Any]:
    """Public JSON for the free proof-of-volume feed.

    Reads modules/x402/settlement.py's recent_real_settlements() (already
    probe-payer-excluded) and serializes it with human units and asset
    symbols, never raw atomic amounts a reader would have to decode.

    An empty `items` list is a real, expected state right now -- this feed
    only ever shows real (non-operator) settlements, and third-party volume
    is genuinely low this early. Rather than a bare `{"items": []}`, which a
    skeptical reader can't distinguish from "this feed is broken", an empty
    result adds an honest `note` plus a `verify_at` pointer to the
    facilitator's own public merchant page -- independently showing our real
    settle count and success rate. Never fabricate an entry here to fill the
    gap (CLAUDE.md section 9: no wash volume, no misleading claims).
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
    if not items:
        return {
            "items": items,
            "note": (
                "No settlements in the recent window. This feed only ever shows real, "
                "non-operator payments, so an early marketplace can legitimately be empty "
                "here -- it is not an error and nothing is hidden. Verify total settle "
                "count and success rate independently on our facilitator merchant page."
            ),
            "verify_at": _GOPLAUSIBLE_MERCHANT_URL,
        }
    return {"items": items}


def build_catalog() -> dict[str, Any]:
    """The catalog document: marketplace-wide payment facts plus every live route.

    `sections`/`products[].section,summary,status,entry,auth` (2026-09-07 v2,
    see docs/x402-marketplace-product-redesign.md section 3.4): products[]
    now lists the FULL roster (live and gated), each carrying a live-computed
    `status` -- see `_ordered_products()`'s own docstring for why this
    deliberately differs from `routes[]`, which stays scoped to what
    create_app() actually registered.
    """
    network = settings.x402_network
    products = enabled_products()
    return {
        "name": "PXke x402 marketplace",
        "description": _generated_description(),
        "website": settings.x402_public_site_url,
        "logo": "https://algorand.pxke.me/favicon.svg",
        # Generated from live sections, never hand-written.
        "categories": _live_section_keys(),
        "sections": [
            {"key": section.key, "title": section.title, "summary": section.summary}
            for section in SECTIONS
        ],
        # Top-level `url` + `merchant` block: live-verified 2026-09-06 that the
        # facilitator's metadata enrichment reads a top-level name/description/
        # url plus a merchant{id,name,payTo} block on other merchants' own
        # well-known docs (e.g. x402-echo-service) and populates their Bazaar
        # profile from it -- our own well-known's top-level "name" above was
        # NOT reaching the facilitator's top-level merchant name (only
        # enrich.agent[0].name), which is why our profile shows empty
        # name/description/categories in `discovery/merchants` search despite
        # being listed. `id` is our current live merchant id from
        # `data/merchants/{id}` (keyed on payTo by the facilitator itself, not
        # something we assign) -- echoing it back is a no-op if the
        # facilitator ignores it and free confirmation if it reads it.
        "url": "https://algorand-api.pxke.me/",
        "merchant": {
            "id": _GOPLAUSIBLE_MERCHANT_ID,
            "name": "PXke x402 marketplace",
            "payTo": settings.x402_pay_to_address,
        },
        "network": network,
        "network_name": _NETWORK_NAMES.get(network, "unknown"),
        "pay_to": settings.x402_pay_to_address,
        "facilitator_url": settings.x402_facilitator_url,
        "challenge_tag": CHALLENGE_TAG,
        "payment_scheme": "exact",
        "assets": _asset_json(network),
        "products": [_product_json(p) for p in _ordered_products()],
        "routes": [_route_json(p, route) for p in products for route in p.routes],
    }
