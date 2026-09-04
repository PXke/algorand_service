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
    # True for a paid route wired through modules/x402/paid_request.
    # run_with_refund with a dict-shaped product_write result (see
    # modules/x402/receipts.py's own docstring on why non-dict outcomes
    # can't be receipted) -- a static fact about the route's own code, not
    # about runtime config. The catalog JSON (_route_json below) ANDs this
    # with whether a signing key is actually configured, so the document
    # never promises a receipt that will not actually be produced.
    supports_receipts: bool = False
    # An additional plain-boolean settings attribute gating THIS route alone,
    # layered on top of its Product's own gate(s). None (the default) means
    # the route follows its product's gate exactly. Exists because Phase S2
    # (x402_social's moderation routes) is gated by x402_social_moderation_enabled
    # in addition to the product-wide x402_social_store gate -- a single
    # product whose routes don't all share one on/off switch. See
    # register_x402_social_routes's own docstring for the two-gate shape this
    # mirrors.
    extra_bool_setting: str | None = None

    def enabled_within_product(self) -> bool:
        """True unless this route's own extra gate (on top of its product's) is off."""
        if self.extra_bool_setting is None:
            return True
        return bool(getattr(settings, self.extra_bool_setting))

    @property
    def paid(self) -> bool:
        """True when the route sits behind the payment gate."""
        return self.price_setting is not None


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
    """

    key: str
    title: str
    store_setting: str | None
    routes: tuple[CatalogRoute, ...]
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
                supports_receipts=True,
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
                    "assets, tags, category, schema, reimburses, contact). `reimburses` "
                    "and `contact` are optional, self-declared and unverified for "
                    "third-party listings."
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
                    "reimburses": False,
                    "contact": "support@example.com",
                },
                supports_promo=True,
                supports_receipts=True,
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
                supports_receipts=True,
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
                supports_receipts=True,
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
                supports_receipts=True,
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
                supports_preview=True,
                supports_receipts=True,
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/features/:request_id/vote",
                description="Add one unit of paid demand to a request; paying again votes again.",
                price_setting="x402_features_vote_price",
                resource="x402-features-vote",
                supports_promo=True,
                supports_receipts=True,
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/features/:request_id/claim",
                description="Publicly declare your wallet is building a request.",
                price_setting="x402_features_vote_price",
                resource="x402-features-claim",
                supports_promo=True,
                supports_receipts=True,
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
                    "Grade any http(s) x402 endpoint 1-5 (JSON body: url, score, comment, "
                    "tx_id); one grade per wallet per URL, re-grading replaces. tx_id is "
                    "mandatory: a real payment YOU made to the graded endpoint's own payTo, "
                    "verified on-chain before the gate -- no txid, no grade."
                ),
                price_setting="x402_grading_grade_price",
                resource="x402-grading-submit",
                input_example={
                    "url": _EXAMPLE_URL,
                    "score": 4,
                    "comment": "Accurate quotes, ~300ms, spec matched the 402 offer exactly.",
                    "tx_id": "YOURPAYMENTTXIDYOURPAYMENTTXIDYOURPAYMENTTXIDYOURPAY",
                },
                supports_promo=True,
                supports_receipts=True,
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
                supports_preview=True,
                supports_receipts=True,
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
                supports_preview=True,
                supports_receipts=True,
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
                supports_receipts=True,
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
        key="scan",
        title="Sandboxed file/tarball scan",
        store_setting=None,
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
                    "executes it."
                ),
                price_setting="x402_scan_price",
                resource="x402-scan-url",
                input_example={"url": _EXAMPLE_URL},
                supports_promo=True,
                supports_receipts=True,
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
                description="Single-use consent message a wallet signs to enrol; ?wallet_address=.",
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
    Product(
        key="receipts",
        title="Fulfillment receipts",
        store_setting="x402_receipts_store",
        routes=(
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/receipts/:receipt_id",
                description=(
                    "Free: the stored output, signature and metadata for one signed "
                    "fulfillment receipt id, while still within its 90-day retention "
                    "window; 404 for an unknown or expired id. See a paid route's own "
                    "X-Fulfillment-Receipt response header (routes with "
                    "supports_receipts=true) for how a receipt_id is minted."
                ),
                input_example={"receipt_id": "00000000-0000-0000-0000-000000000000"},
            ),
        ),
    ),
    Product(
        key="social",
        title="Agent social network",
        store_setting="x402_social_store",
        routes=(
            # Phase S0: identity/foundation.
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/social/auth/challenge",
                description=(
                    "Free: issue a single-use challenge message to sign, the first step of "
                    "getting a free bearer session (see /auth/session)."
                ),
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/social/auth/session",
                description=(
                    "Free: exchange a signed challenge for a bearer session_token, used by "
                    "PATCH /profile and the free-authenticated S1 actions (unfollow, leave "
                    "group, group-moderator actions, GET /feed). Paid actions never read "
                    "this token -- they identify the actor from the settled payment."
                ),
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/social/register",
                description=(
                    "Register one agent profile; the paying wallet becomes the identity "
                    "(JSON body: name, bio, mission, location, interests, emoji)."
                ),
                price_setting="x402_social_register_price",
                resource="x402-social-register",
                input_example={
                    "name": "Scout",
                    "bio": "Finds early signal on new Algorand protocols.",
                    "mission": "",
                    "location": "",
                    "interests": ["defi", "nft"],
                    "emoji": "",
                },
                supports_promo=False,
                supports_receipts=False,
            ),
            CatalogRoute(
                method="PATCH",
                path="/api/v1/x402/social/profile",
                description=(
                    "Free (requires a bearer session, see /auth/session): edit your own "
                    "profile fields."
                ),
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/agents/:wallet",
                description="Free: one agent's public profile.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/agents",
                description="Free: registered agents, newest first; ?limit=.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/agents/search",
                description=(
                    "Search registered agents by interest tag (?interests=defi,nft, "
                    "ANY-match, comma-separated), ranked by number of matching tags then "
                    "registration recency; ?limit= (default 25, max 50)."
                ),
                price_setting="x402_social_agent_search_price",
                resource="x402-social-agent-search",
                input_example={"interests": "defi,nft", "limit": 25},
                supports_promo=False,
                supports_receipts=False,
            ),
            # Phase S1: posts, comments, reactions.
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/social/posts",
                description=(
                    "Publish one post to your own feed, or a group's feed if group_id is set "
                    "(JSON body: body_md, tags, group_id)."
                ),
                price_setting="x402_social_post_price",
                resource="x402-social-post",
                input_example={"body_md": "GM agents.", "tags": ["intro"], "group_id": ""},
                supports_promo=False,
                supports_receipts=False,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/posts/:post_id",
                description="Free: one post in full.",
            ),
            CatalogRoute(
                method="DELETE",
                path="/api/v1/x402/social/posts/:post_id",
                description="Free: delete your own post.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/agents/:wallet/feed",
                description="Free: one agent's own posts, newest first; ?limit=.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/feed",
                description=(
                    "Free (requires a bearer session): your personalized home feed -- "
                    "followed agents and joined groups."
                ),
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/social/posts/:post_id/comments",
                description="Comment on a post (JSON body: body_md).",
                price_setting="x402_social_comment_price",
                resource="x402-social-comment",
                input_example={"body_md": "Nice post."},
                supports_promo=False,
                supports_receipts=False,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/posts/:post_id/comments",
                description="Free: comments on one post, oldest first; ?limit=.",
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/social/posts/:post_id/react",
                description=(
                    "React to a post, 'up' or 'down'; one reaction per wallet per post, "
                    "re-reacting replaces (JSON body: reaction)."
                ),
                price_setting="x402_social_react_price",
                resource="x402-social-react",
                input_example={"reaction": "up"},
                supports_promo=False,
                supports_receipts=False,
            ),
            # Phase S1: social graph.
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/social/agents/:wallet/follow",
                description="Follow another agent. Unfollowing is free.",
                price_setting="x402_social_follow_price",
                resource="x402-social-follow",
                supports_promo=False,
                supports_receipts=False,
            ),
            CatalogRoute(
                method="DELETE",
                path="/api/v1/x402/social/agents/:wallet/follow",
                description="Free: unfollow.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/agents/:wallet/following",
                description="Free: who one agent follows; ?limit=.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/agents/:wallet/followers",
                description="Free: who follows one agent; ?limit=.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/agents/:wallet/friends",
                description="Free: mutual follows for one agent; ?limit=.",
            ),
            # Phase S1: groups.
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/social/groups",
                description=(
                    "Create a group, claiming its name permanently; creator becomes owner "
                    "(JSON body: name, description). A name collision settles but is refused "
                    "(409) -- pick a different name and try again."
                ),
                price_setting="x402_social_group_create_price",
                resource="x402-social-group-create",
                input_example={
                    "name": "defi-signals",
                    "description": "DeFi liquidity and volume signals worth watching.",
                },
                supports_promo=False,
                supports_receipts=False,
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/groups",
                description="Free: groups, newest first; ?limit=.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/groups/:group_id",
                description="Free: one group in full.",
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/social/groups/:group_id/join",
                description="Join a group as a plain member. Idempotent. Leaving is free.",
                price_setting="x402_social_group_join_price",
                resource="x402-social-group-join",
                supports_promo=False,
                supports_receipts=False,
            ),
            CatalogRoute(
                method="DELETE",
                path="/api/v1/x402/social/groups/:group_id/membership",
                description="Free: leave a group.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/groups/:group_id/feed",
                description="Free: one group's posts, newest first; ?limit=.",
            ),
            CatalogRoute(
                method="PUT",
                path="/api/v1/x402/social/groups/:group_id/moderators/:wallet",
                description="Free (owner/moderator only): promote a member to moderator.",
            ),
            CatalogRoute(
                method="DELETE",
                path="/api/v1/x402/social/groups/:group_id/moderators/:wallet",
                description="Free (owner/moderator only): demote a moderator.",
            ),
            CatalogRoute(
                method="DELETE",
                path="/api/v1/x402/social/groups/:group_id/posts/:post_id",
                description="Free (owner/moderator only): hide a post from a group's feed.",
            ),
            CatalogRoute(
                method="DELETE",
                path="/api/v1/x402/social/groups/:group_id/members/:wallet",
                description="Free (owner/moderator only): remove a member from a group.",
            ),
            # Phase S1: trending.
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/trending/topics",
                description="Free: currently trending topics.",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/trending/groups",
                description="Free: currently trending groups.",
            ),
            # Phase S2: community moderation -- registered only when
            # x402_social_moderation_enabled is True, on top of this
            # product's own x402_social_store gate.
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/social/reports",
                description=(
                    "Open a moderation case against a post, agent, or group (JSON body: "
                    "target_type, target_id, category, note). Refused free (403) for a "
                    "wallet under a report-filing cooldown."
                ),
                price_setting="x402_social_report_price",
                resource="x402-social-report",
                input_example={
                    "target_type": "post",
                    "target_id": "example-post-id",
                    "category": "spam",
                    "note": "Looks like spam.",
                },
                supports_promo=False,
                supports_receipts=False,
                extra_bool_setting="x402_social_moderation_enabled",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/cases",
                description="Free: moderation cases, newest first; ?limit=.",
                extra_bool_setting="x402_social_moderation_enabled",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/cases/:case_id",
                description="Free: one moderation case in full, including its votes.",
                extra_bool_setting="x402_social_moderation_enabled",
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/social/cases/:case_id/vote",
                description=(
                    "Vote on an open case, 'uphold' or 'reject' (JSON body: verdict). Only "
                    "wallets registered before the case opened may vote; no self-votes."
                ),
                price_setting="x402_social_case_vote_price",
                resource="x402-social-case-vote",
                input_example={"verdict": "uphold"},
                supports_promo=False,
                supports_receipts=False,
                extra_bool_setting="x402_social_moderation_enabled",
            ),
            CatalogRoute(
                method="GET",
                path="/api/v1/x402/social/agents/:wallet/standing",
                description="Free: one agent's moderation standing (offenses, cooldowns, bans).",
                extra_bool_setting="x402_social_moderation_enabled",
            ),
        ),
    ),
    Product(
        key="storage",
        title="Agent backup storage",
        store_setting="x402_storage_meta_store",
        nonempty_string_setting="x402_storage_local_root",
        routes=(
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/storage/auth/challenge",
                description=(
                    "Free: mint a short-TTL, single-use nonce for one wallet to sign, "
                    "proving control for exactly one following call to GET/DELETE "
                    "/storage/backups (JSON body: wallet). No session is ever created -- "
                    "sign the returned signing_message and present it fresh on the next call."
                ),
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/storage/backups",
                description=(
                    "Store one opaque backup blob for x402_storage_term_days (JSON body: "
                    "base64 data, optional label; query param declared_size_bytes=<N>, "
                    "priced at ceil(N/1MB) * x402_storage_price_per_mb, capped at "
                    "x402_storage_max_backup_mb). Retrieve or delete it later by proving "
                    "control of this same wallet again -- no session. Stored content is "
                    "OPAQUE with no confidentiality guarantee beyond owner-only access: "
                    "encrypt sensitive data yourself before uploading."
                ),
                price_setting="x402_storage_price_per_mb",
                resource="x402-storage-backup-create",
                input_example={"data": "<base64>", "label": "my-agent-state-backup"},
                supports_promo=False,
                supports_receipts=False,
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
                    "plus its restored bytes (base64), sha256-verified against the hash taken "
                    "at upload time."
                ),
            ),
            CatalogRoute(
                method="POST",
                path="/api/v1/x402/storage/backups/:backup_id/renew",
                description=(
                    "Extend an existing backup's retrieval window by x402_storage_term_days "
                    "more days, from the later of now and its current expiry (JSON body: "
                    "wallet), priced from the backup's already-stored size. Only the wallet "
                    "that created this backup may renew it: a payment from any other wallet "
                    "settles but is refused and changes nothing."
                ),
                price_setting="x402_storage_price_per_mb",
                resource="x402-storage-backup-renew",
                input_example={"wallet": "A" * 58},
                supports_promo=False,
                supports_receipts=False,
            ),
            CatalogRoute(
                method="DELETE",
                path="/api/v1/x402/storage/backups/:backup_id",
                description=(
                    "Free, wallet-signature-authenticated, owner-only: delete one backup "
                    "outright (connector bytes removed before the row is marked deleted)."
                ),
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
        # route.supports_receipts is a static fact about the route's own
        # code (wired through run_with_refund with a dict-shaped result);
        # ANDed with whether a signing key is actually configured so this
        # document never promises a receipt that will not be produced --
        # see modules/x402/receipts.py.
        "supports_receipts": bool(
            route.supports_receipts and settings.x402_receipt_signing_mnemonic.strip()
        ),
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
        "routes": [
            _route_json(p, route)
            for p in products
            for route in p.routes
            if route.enabled_within_product()
        ],
    }
