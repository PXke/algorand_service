"""All API request/response models, consolidated as msgspec.Struct.

Single source of truth for the wire schemas (replaces the per-module pydantic
schema files, which now re-export from here). Every struct is `kw_only=True`
(pydantic models were keyword-only too, so construction is unchanged and field
ordering is unconstrained). Constraints use `Annotated[..., msgspec.Meta(...)]`;
the CAIP-122 kebab-case keys use `field(name=...)`; cross-field / normalising
validation lives in `__post_init__`.
"""

from __future__ import annotations

from typing import Annotated, Literal

import msgspec
from msgspec import Meta, field

from app.modules.admin.classifier_constants import (
    QUALITY_LEVELS,
    normalize_content_category,
)

# ── Common constrained aliases ────────────────────────────────────────────────
WalletAddress = Annotated[str, Meta(min_length=58, max_length=58)]


# ── News ──────────────────────────────────────────────────────────────────────
class ArticleFeedItem(msgspec.Struct, kw_only=True):
    """One article's feed-list representation."""

    article_id: str
    service_id: str
    title: str
    summary: str
    published_at_epoch: int
    trigger_txid: str | None = None
    trigger_round: int | None = None
    tags: list[str] = field(default_factory=list)
    # Permanent URL slug. None on rows written before migration 056, which fall
    # back to the article id so their URLs keep resolving.
    slug: str | None = None
    trigger_kind: str = "editorial"
    image_url: str | None = None
    source_url: str | None = None
    # Read tally — populated on hot/top rankings and the regular feed.
    views: int | None = None
    # Last content revision (edit/recompose); None = never revised.
    updated_at_epoch: int | None = None
    # Original first publication; set only after a recompose re-publish
    # re-stamped published_at. None = published_at IS the original date.
    first_published_at_epoch: int | None = None


class ArticleDetail(msgspec.Struct, kw_only=True):
    """Full article detail for the article-detail route."""

    article_id: str
    service_id: str
    title: str
    summary: str
    body: str
    published_at_epoch: int
    trigger_txid: str | None = None
    trigger_round: int | None = None
    source_url: str | None = None
    tags: list[str] = field(default_factory=list)
    slug: str | None = None
    trigger_kind: str = "editorial"
    views: int = 0
    image_url: str | None = None
    updated_at_epoch: int | None = None


# ── Auth ──────────────────────────────────────────────────────────────────────
class NonceRequest(msgspec.Struct, kw_only=True):
    """Request body for issuing a wallet-auth nonce."""

    wallet_address: WalletAddress


class Caip122Payload(msgspec.Struct, kw_only=True):
    """CAIP-122 message fields the client signs for login."""

    domain: str
    account_address: str
    uri: str
    chain_id: str
    nonce: str
    version: str = "1"
    type: str = "ed25519"
    statement: str | None = None
    # CAIP-122 wire keys are kebab-case; the previous pydantic model used aliases.
    issued_at: str | None = field(name="issued-at", default=None)
    expiration_time: str | None = field(name="expiration-time", default=None)
    not_before: str | None = field(name="not-before", default=None)
    request_id: str | None = field(name="request-id", default=None)
    resources: list[str] | None = None


class Arc0060Proof(msgspec.Struct, kw_only=True):
    """ARC-60 (WebAuthn-shaped) signature proof for wallet login."""

    data_b64: str
    signature_b64: str
    authenticator_data_b64: str
    domain: str
    request_id: str | None = None


class VerifyRequest(msgspec.Struct, kw_only=True):
    """Request body for verifying a wallet-auth signature."""

    wallet_address: WalletAddress
    nonce: str
    proof_method: Literal["arc0025_txn", "arc0060", "legacy_message", "signed_bytes"] = "arc0060"
    signature_b64: str | None = None
    signed_txn_b64: str | None = None
    arc0060: Arc0060Proof | None = None

    def __post_init__(self) -> None:
        """Require the proof fields matching `proof_method`."""
        if self.proof_method == "arc0060":
            if self.arc0060 is None:
                raise ValueError("arc0060 proof is required when proof_method is arc0060")
        elif self.proof_method == "arc0025_txn" and not self.signed_txn_b64:
            raise ValueError("signed_txn_b64 is required when proof_method is arc0025_txn")
        elif self.proof_method in ("legacy_message", "signed_bytes") and not self.signature_b64:
            raise ValueError(f"signature_b64 is required when proof_method is {self.proof_method}")


class SessionInfo(msgspec.Struct, kw_only=True):
    """The current session's wallet address and expiry."""

    wallet_address: str
    issued_at_epoch: int
    expires_in_epoch: int


# ── Ingest ────────────────────────────────────────────────────────────────────
class IngestSignalRequest(msgspec.Struct, kw_only=True):
    """Request body for pushing an external ingest signal."""

    """Push official announcements when bots cannot join Discord/Telegram."""

    service_id: Annotated[str, Meta(min_length=1, max_length=128)]
    display_name: Annotated[str, Meta(min_length=1, max_length=256)]
    page_text: Annotated[str, Meta(min_length=1, max_length=100_000)]
    page_title: Annotated[str, Meta(max_length=512)] = "Announcement"
    source_url: Annotated[str, Meta(max_length=2048)] = ""
    source_kind: Annotated[str, Meta(max_length=32)] = "push"
    match_kind: Annotated[str, Meta(max_length=64)] = "push"
    match_value: Annotated[str, Meta(max_length=512)] = ""
    mail_from: Annotated[str, Meta(max_length=512)] = ""


# ── Search ────────────────────────────────────────────────────────────────────
class SearchHit(msgspec.Struct, kw_only=True):
    """One article search result."""

    article_id: str
    title: str
    summary: str
    service_id: str | None = None
    published_at_epoch: int | None = None
    score: float | None = None
    # Typesense highlight snippet (HTML <mark> tags) around the matched terms.
    snippet: str | None = None
    title_highlight: str | None = None
    # Permanent URL slug. None for a document indexed before the slug field
    # existed and not yet backfilled -- the frontend falls back to a raw
    # article_id URL in that case (see frontend/src/lib/seo.ts
    # articleCanonicalPath).
    slug: str | None = None


class SearchResponse(msgspec.Struct, kw_only=True):
    """Article search results for a query."""

    query: str
    engine: str
    items: list[SearchHit] = field(default_factory=list)


# ── Analytics ─────────────────────────────────────────────────────────────────
class PageviewBeaconRequest(msgspec.Struct, kw_only=True):
    """Request body for a reader pageview beacon."""

    """Client-side beacon for a Flutter in-app route change (no full document request, so the SSR pageview record never sees it)."""

    path: Annotated[str, Meta(min_length=1, max_length=200)]


# ── Contact ───────────────────────────────────────────────────────────────────
class ContactMessageRequest(msgspec.Struct, kw_only=True):
    """Request body for a contact-form submission."""

    message: Annotated[str, Meta(min_length=10, max_length=4000)]
    name: Annotated[str, Meta(max_length=120)] = ""
    email: Annotated[str, Meta(max_length=254)] = ""
    # Honeypot: hidden in the UI, so a human never fills it — a non-empty value
    # marks a bot and the message is silently dropped.
    website: Annotated[str, Meta(max_length=254)] = ""


class ContactMessageItem(msgspec.Struct, kw_only=True):
    """One stored contact message."""

    message_id: str
    name: str
    email: str
    message: str
    created_at_epoch: int


# ── Draft sharing ────────────────────────────────────────────────────────────
class ShareLinkCreateRequest(msgspec.Struct, kw_only=True):
    """Admin request to mint a new share link for one article."""

    label: Annotated[str, Meta(max_length=120)] = ""


class ShareLinkItem(msgspec.Struct, kw_only=True):
    """One share link, admin-facing.

    The token itself is included only here — the one response an
    admin-authenticated caller ever sees it in.
    """

    token: str
    article_id: str
    label: str
    created_at_epoch: int
    created_by: str
    revoked: bool
    revoked_at_epoch: int | None = None


class CommentQuoteAnchor(msgspec.Struct, kw_only=True):
    """A W3C TextQuoteSelector-style anchor.

    The exact selected text plus a short span of surrounding context,
    resolved client-side against the rendered DOM at read time (see
    frontend/src/lib/textHighlight.ts) — never raw offsets into the
    markdown source.
    """

    quote: Annotated[str, Meta(min_length=1, max_length=4000)]
    prefix: Annotated[str, Meta(max_length=200)] = ""
    suffix: Annotated[str, Meta(max_length=200)] = ""


class CreateCommentRequest(msgspec.Struct, kw_only=True):
    """Public (token-gated) request to add a comment, optionally anchored to a text quote."""

    body: Annotated[str, Meta(min_length=1, max_length=2000)]
    author_name: Annotated[str, Meta(max_length=80)] = ""
    anchor: CommentQuoteAnchor | None = None  # None = a general, unanchored comment


class CommentItem(msgspec.Struct, kw_only=True):
    """One stored comment.

    Same shape for the admin read and the public (token-gated) read — the
    thread is shared, per product decision.
    """

    comment_id: str
    article_id: str
    body: str
    author_name: str
    created_at_epoch: int
    anchor_quote: str | None = None
    anchor_prefix: str | None = None
    anchor_suffix: str | None = None


class SharedArticleResponse(msgspec.Struct, kw_only=True):
    """What a share-token holder sees: the article plus enough link metadata for the page header."""

    article: ArticleDetail
    is_draft: bool
    link_label: str


# ── Suggestions ───────────────────────────────────────────────────────────────
class CreateSuggestionRequest(msgspec.Struct, kw_only=True):
    """Request body for a treasury-payment-gated service suggestion."""

    title: Annotated[str, Meta(min_length=3, max_length=200)]
    body: Annotated[str, Meta(min_length=10, max_length=5000)]
    submission_txid: Annotated[str, Meta(min_length=52, max_length=52)]


class SuggestionResponse(msgspec.Struct, kw_only=True):
    """A stored service suggestion."""

    suggestion_id: str
    wallet_address: str
    title: str
    body: str
    submission_txid: str
    status: str
    created_at_epoch: int
    upvote_count: int = 0


class SuggestionConfigResponse(msgspec.Struct, kw_only=True):
    """Treasury address and minimum payment for suggestions."""

    treasury_address: str
    min_microalgos: int
    min_algo_display: str


class UpvoteRequest(msgspec.Struct, kw_only=True):
    """Request body for upvoting a suggestion."""

    signature_b64: Annotated[str, Meta(min_length=16, max_length=2048)]


# ── KYC-as-a-service (x402 challenge) ───────────────────────────────────────
class EnrollRequest(msgspec.Struct, kw_only=True):
    """Request body for KYC enrollment."""

    wallet_address: Annotated[str, Meta(min_length=58, max_length=58)]
    consent_signature_b64: Annotated[str, Meta(min_length=16, max_length=2048)]


class EnrollResponse(msgspec.Struct, kw_only=True):
    """Stored enrollment and computed KYC level."""

    wallet_address: str
    kyc_level: str
    wallet_age_round: int | None
    recent_tx_count: int
    enrolled_at_epoch: int


class KycConsentMessageResponse(msgspec.Struct, kw_only=True):
    """The message a wallet must sign to consent to KYC enrollment."""

    message: str
    wallet_address: str


class KycLookupResponse(msgspec.Struct, kw_only=True):
    """Paid KYC-status lookup result for a wallet."""

    enrolled: bool
    wallet_address: str
    kyc_level: str | None = None
    payout_status: str | None = None


class KycPayoutRetryRequest(msgspec.Struct, kw_only=True):
    """Request body for retrying a failed KYC payout."""

    wallet_address: Annotated[str, Meta(min_length=58, max_length=58)]
    amount_atomic: Annotated[str, Meta(min_length=1, max_length=32)]


# ── x402 directory ────────────────────────────────────────────────────────────
class X402ListingRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /x402/list — one paid x402 endpoint listing.

    `price` is the listed endpoint's OWN price as free text (e.g. "$0.01"),
    describing what a caller of that endpoint pays. It is not the listing fee,
    which is fixed at settings.x402_listing_price and charged by the gate.

    `schema` mirrors the field name in the stored listing's JSON representation;
    it is persisted as a JSON string in the schema_json column (SCHEMA is a
    reserved CQL keyword and cannot be a bare column name).
    """

    url: Annotated[str, Meta(min_length=8, max_length=2048)]
    price: Annotated[str, Meta(min_length=1, max_length=64)]
    description: Annotated[str, Meta(max_length=2000)] = ""
    assets: Annotated[list[str], Meta(max_length=16)] = field(default_factory=list)
    tags: Annotated[list[str], Meta(max_length=16)] = field(default_factory=list)
    schema: dict | None = None


class X402ListingItem(msgspec.Struct, kw_only=True):
    """One directory listing as served by /x402/list and /x402/search."""

    url: str
    price: str
    description: str
    assets: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    schema: dict | None = None
    term_end_epoch: int = 0
    created_at_epoch: int = 0
    settlement_tx_id: str = ""


# ── Metrics ───────────────────────────────────────────────────────────────────
class PriceMetricsResponse(msgspec.Struct, kw_only=True):
    """Price-metrics brief for the dashboard."""

    asset_id: str
    asset_name: str
    currency: str
    price_usd: float
    change_24h_pct: float | None
    sample_count_24h: int
    available: bool
    prepared_at_epoch: int | None
    market_cap_usd: float | None = None
    volume_24h_usd: float | None = None


class MetricTile(msgspec.Struct, kw_only=True):
    """One tile in the metrics dashboard."""

    id: str
    label: str
    value: str
    hint: str | None = None
    available: bool = True


class MetricsDashboardResponse(msgspec.Struct, kw_only=True):
    """Assembled metrics-dashboard tiles."""

    tiles: list[MetricTile]


class ChainPulseKind(msgspec.Struct, kw_only=True):
    """One txn class inside a block (payments, assets, apps, …)."""

    id: str
    count: int


class ChainPulseBlock(msgspec.Struct, kw_only=True):
    """One committed round in the live chain pulse, with optional txn mix."""

    round: int
    txns: int
    ts: int | None = None
    inners: int = 0
    kinds: list[ChainPulseKind] = []


class ChainPulseResponse(msgspec.Struct, kw_only=True):
    """Last minute of blocks — one bar per round — for the front-page wire."""

    last_round: int
    txns_last_minute: int
    blocks: list[ChainPulseBlock]


class ChainPulseMix(msgspec.Struct, kw_only=True):
    """What composed one committed round — payments, assets, apps."""

    round: int
    txns: int
    inners: int = 0
    ts: int | None = None
    kinds: list[ChainPulseKind]


# ── Placements ────────────────────────────────────────────────────────────────
class FeedPlacementItem(msgspec.Struct, kw_only=True):
    """One sponsored placement merged into the reader feed."""

    placement_id: str
    slot: str
    sponsor_name: str
    headline: str
    body: str
    image_url: str | None = None
    target_url: str | None = None
    priority: int = 0


# ── Registry ──────────────────────────────────────────────────────────────────
class ServiceRegistryItem(msgspec.Struct, kw_only=True):
    """One registered service in the service registry."""

    service_id: str
    display_name: str
    match_kind: str
    match_value: str
    scrape_url: str | None = None
    enabled: bool = True
    source_kind: str = "web"
    origin: str = "seed"


# ── Admin ─────────────────────────────────────────────────────────────────────
class ArticlePatchRequest(msgspec.Struct, kw_only=True):
    """Request body for an admin in-place article edit."""

    title: Annotated[str, Meta(max_length=512)] | None = None
    summary: Annotated[str, Meta(max_length=2000)] | None = None
    body: Annotated[str, Meta(max_length=200_000)] | None = None


class ArticleDraftRequest(msgspec.Struct, kw_only=True):
    """Request body for toggling an article's admin-only draft flag."""

    draft: bool


class EditorialBriefCreate(msgspec.Struct, kw_only=True):
    """Request body for creating an editorial brief."""

    title: Annotated[str, Meta(min_length=1, max_length=256)]
    body_markdown: Annotated[str, Meta(min_length=1, max_length=100_000)]
    keywords: Annotated[str, Meta(max_length=1024)] = ""
    status: Annotated[str, Meta(max_length=32)] = "active"
    # 0 = one-off assignment; >0 = re-trigger an in-place edit of the resulting
    # article every N days (see app.tasks.newspaper.scan_editorial_brief_schedule).
    refresh_every_days: Annotated[int, Meta(ge=0, le=3650)] = 0
    # In-depth, longer treatment (see mistral_compose._SPECIAL_EDITION_DEPTH_INSTRUCTIONS)
    # instead of the standard "scale length to substance" pass every other brief gets.
    is_special_edition: bool = False


class ClassifierFeedbackCreate(msgspec.Struct, kw_only=True):
    """Request body for recording classifier training feedback."""

    url: Annotated[str, Meta(min_length=1, max_length=2048)]
    approved: bool
    text_sample: Annotated[str, Meta(max_length=8000)] = ""
    category: Annotated[str, Meta(max_length=64)] = "generic"
    predicted_category: Annotated[str, Meta(max_length=64)] | None = None
    quality: Annotated[str, Meta(max_length=32)] = "medium"
    # Multiple categories/keywords for the article (first is primary).
    categories: list[str] = field(default_factory=list)
    # Source verdict, separate from the article verdict: is the SOURCE worth
    # watching? Rejecting a low-quality article keeps a good source alive.
    source_relevant: bool = True
    predicted_publish: bool = False
    # Training mode: record the label + grade dimensions (both models learn) but
    # do NOT publish an accepted article to the live feed.
    training_only: bool = False
    # Human-corrected per-dimension scores (0-10), only the disputed ones.
    corrected_scores: dict[str, float] = field(default_factory=dict)
    review_id: Annotated[str, Meta(max_length=64)] | None = None
    article_id: Annotated[str, Meta(max_length=64)] | None = None

    def __post_init__(self) -> None:
        """Normalize category/categories and validate quality is a known level."""
        # Writer tags / pipeline labels in the category slot coerce to generic —
        # never block approve/reject on taxonomy mismatch.
        self.category = normalize_content_category(self.category)
        if self.predicted_category is not None:
            pred = self.predicted_category.strip().lower()
            self.predicted_category = pred or None
        normalized_cats: list[str] = []
        for raw in self.categories:
            cat = normalize_content_category(raw, default="")
            if cat and cat not in normalized_cats:
                normalized_cats.append(cat)
        if self.category not in normalized_cats:
            normalized_cats.insert(0, self.category)
        self.categories = normalized_cats
        quality = self.quality.strip().lower()
        if quality not in QUALITY_LEVELS:
            raise ValueError(f"quality must be one of: {', '.join(QUALITY_LEVELS)}")
        self.quality = quality


class SourceUpsertRequest(msgspec.Struct, kw_only=True):
    """Request body for adding/updating a service's web source."""

    service_id: Annotated[str, Meta(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")]
    display_name: Annotated[str, Meta(min_length=1, max_length=256)]
    scrape_url: Annotated[str, Meta(min_length=1, max_length=2048)]
    match_kind: Annotated[str, Meta(max_length=64)] = "domain"
    match_value: Annotated[str, Meta(max_length=512)] = ""
    enabled: bool = True


class ServiceMergeRequest(msgspec.Struct, kw_only=True):
    """Request body for merging duplicate services."""

    target_service_id: Annotated[
        str, Meta(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    ]
    source_service_ids: Annotated[list[str], Meta(min_length=1, max_length=32)]


class ScraperRunRequest(msgspec.Struct, kw_only=True):
    """Request body for manually triggering a scraper run."""

    action: Annotated[str, Meta(min_length=1, max_length=64)]


class GlossaryUpsertRequest(msgspec.Struct, kw_only=True):
    """Request body for creating/updating a glossary entry."""

    slug: Annotated[str, Meta(min_length=1, max_length=96, pattern=r"^[a-z0-9][a-z0-9-]*$")]
    term: Annotated[str, Meta(min_length=1, max_length=200)]
    definition: Annotated[str, Meta(min_length=1, max_length=4000)]
    aliases: Annotated[list[str], Meta(max_length=16)] = field(default_factory=list)
    status: Literal["draft", "published"] = "draft"


class DomainSetRequest(msgspec.Struct, kw_only=True):
    """Request body for setting a domain's frontier status."""

    domain: Annotated[str, Meta(min_length=3, max_length=256)]
    is_relevant: bool
    # Exactly two modes on approval (2026-07-26 simplification — replaces the
    # old independent as_seed/single_page_only pair, which allowed a third,
    # dead-end state: crawled but never monitored AND never composed into an
    # article). True: the domain becomes a permanent monitored service_registry
    # source, watched weekly for changes (articles about how it evolves).
    # False: single-page mode — fetch exactly this one URL, never follow its
    # links, excluded from every domain-wide sweep (backfills, bulk re-crawls),
    # and composed into a one-shot article about that page's content (see
    # ingest_publish_signal call in web_crawler.py's scrape_from_queue_item).
    full_site: bool = True


