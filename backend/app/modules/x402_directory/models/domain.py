"""Domain types for directory listings.

The settlement ledger's SettlementRecord moved to modules/x402/settlement.py
2026-08-30 -- it was never actually directory-specific, see that module's
docstring.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import PlatformError, http_status_for_code

# Constant partition key for x402_listings_by_recency. See migration 090 for
# why the whole feed lives in one partition and when to shard it.
DIRECTORY_PARTITION = "default"

# Fixed listing categories (migration 099). A closed enum rather than free
# text so `?category=` is a browsable facet of at most this many partitions,
# not a second, uncontrolled tag namespace. Validated BEFORE the payment gate
# by listing_service.validate_category(); DEFAULT_CATEGORY is what a listing
# gets when the field is omitted, and what a pre-099 row reads back as.
LISTING_CATEGORIES: tuple[str, ...] = (
    "data",
    "ai",
    "finance",
    "identity",
    "storage",
    "compute",
    "social",
    "tooling",
    "other",
)
DEFAULT_CATEGORY = "other"

# The category is served from the by-tag projection (096) under a reserved
# tag `category:<name>`, written and deleted alongside the listing's real
# tags, so `?category=` is one more single-partition read and needs no new
# table. User-supplied tags in this namespace are refused so a listing cannot
# forge its way into a category partition it did not declare.
CATEGORY_TAG_PREFIX = "category:"


def category_tag(category: str) -> str:
    """The reserved by-tag partition key a listing's category is projected under."""
    return f"{CATEGORY_TAG_PREFIX}{category}"


class DirectoryError(PlatformError):
    """A directory-flow error mapped to an HTTP status."""

    def __init__(self, code: str, message: str, *, http_status: int | None = None) -> None:
        """Map a directory error code to its HTTP status via http_status_for_code, unless given explicitly."""
        super().__init__(
            code,
            message,
            http_status=http_status if http_status is not None else http_status_for_code(code),
        )


@dataclass
class StoredListing:
    """One listed x402 endpoint, as stored and as served by search.

    `payer` is the wallet whose settlement paid for the CURRENT term -- see
    migration 094 and listing_service.py's ownership check. Empty string
    means unowned (a listing created before 094, or a genuine edge case),
    never used as a real wallet's identity.
    """

    url_hash: str
    url: str
    price: str
    description: str
    schema_json: str
    settlement_tx_id: str
    term_end_epoch: int
    created_at_epoch: int
    assets: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    payer: str = ""
    # Verified badge (migration 097), written only by the workers probe beat
    # when the endpoint's advertised payTo equals `payer`. Honoured by
    # readers only while verified_wallet == payer (see is_verified), so a
    # relist by a new owner never inherits the previous owner's badge.
    verified_wallet: str = ""
    verified_at_epoch: int = 0
    # One of LISTING_CATEGORIES (migration 099); a pre-099 row reads back as
    # DEFAULT_CATEGORY. Not part of `tags`, but projected next to them, see
    # projection_tags().
    category: str = DEFAULT_CATEGORY
    # Self-declared flags (migration 104), set only at list/relist time (same
    # as price/description/tags/category -- renew() changes nothing about a
    # listing but its term, see renew()'s own docstring). reimburses is the
    # owner's own unverified claim that they refund a payer on failed
    # delivery -- never checked against anything for a third-party listing.
    # contact is a bounded free-text point of contact, not shape-validated.
    # A pre-104 row reads back as unset either way (False / ""), never a
    # fabricated claim.
    reimburses: bool = False
    contact: str = ""

    @property
    def is_verified(self) -> bool:
        """True when the badge is set AND still belongs to the current payer."""
        return bool(self.verified_wallet) and self.verified_wallet == self.payer

    def projection_tags(self) -> list[str]:
        """Every by-tag partition this listing has a row in: its real tags plus the reserved category tag.

        The ONE place the write and delete sides of both stores take the
        projection's key set from, so the category row can never be written
        without later being deleted, or the other way round.
        """
        return [*self.tags, category_tag(self.category)]


@dataclass
class StoredProbe:
    """The newest unpaid probe of one listed endpoint (x402_probe_latest, migration 097)."""

    url_hash: str
    url: str
    probed_at_epoch: int
    reachable: bool
    http_status: int
    latency_ms: int
    served_valid_402: bool
    payto_seen: str
    error: str


@dataclass
class ProbeLeaderboardEntry:
    """One ranked row of the probe-MEASURED reliability leaderboard (roadmap item 7).

    Built by ListingService.probe_leaderboard() from a listing's own recent
    probe_history sample -- never from paid opinion (x402_grading) or spend.
    `avg_latency_ms` is None when the listing has zero "healthy" (reachable
    AND served_valid_402) samples in its window: an honest missing value,
    never a fabricated 0 that would misread as a great latency.
    """

    url: str
    verified_wallet: str
    sample_count: int
    uptime_pct: float
    avg_latency_ms: float | None
    last_probed_at_epoch: int
