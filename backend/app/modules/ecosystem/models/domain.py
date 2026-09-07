"""Domain types for the Algorand Open Registry (roadmap item 26).

A NEW, separate concern from the paid `x402_directory` (see
`docs/awesome-algorand-directory-design.md` section 6 for why): free,
anonymous, human-reviewed ecosystem-project listings, glossary-shaped.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import PlatformError, http_status_for_code

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUSES: tuple[str, ...] = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED)

SOURCE_SEEDED = "seeded"
SOURCE_SUBMITTED = "submitted"
SOURCE_CLAIMED = "claimed"
SOURCES: tuple[str, ...] = (SOURCE_SEEDED, SOURCE_SUBMITTED, SOURCE_CLAIMED)

STAGE_LIVE = "live"
STAGE_BETA = "beta"
STAGE_SUNSET = "sunset"
STAGES: tuple[str, ...] = (STAGE_LIVE, STAGE_BETA, STAGE_SUNSET)
DEFAULT_STAGE = STAGE_LIVE

# Closed category enum (design doc section 4), one per entry (single-
# partition facet); free tags cover everything else. Ordered as they'd
# appear in the nav.
CATEGORIES: tuple[str, ...] = (
    "wallets",
    "defi-exchange",
    "defi-lending",
    "nfts",
    "gaming",
    "identity",
    "rwa",
    "payments",
    "infrastructure",
    "devtools",
    "analytics",
    "governance",
    "interop",
    "security",
    "agents",
    "enterprise",
    "media",
    "other",
)
DEFAULT_CATEGORY = "other"

# Rejection reasons shown to the submitter on the status page (design doc
# section 3.3), a short closed list -- not free text, so the same reason
# always reads the same way to a resubmitting builder.
REJECT_REASONS: tuple[str, ...] = (
    "not_algorand",
    "unreachable",
    "duplicate",
    "low_quality",
    "spam_or_scam",
    "other",
)

MAX_TAGS = 5
MAX_TAG_LENGTH = 40


class EcosystemError(PlatformError):
    """A registry-flow error mapped to an HTTP status."""

    def __init__(self, code: str, message: str, *, http_status: int | None = None) -> None:
        """Map a registry error code to its HTTP status via http_status_for_code, unless given explicitly."""
        super().__init__(
            code,
            message,
            http_status=http_status if http_status is not None else http_status_for_code(code),
        )


@dataclass
class StoredProject:
    """One registry entry, as stored and as served.

    `contact` is private -- admin-only, never rendered on a public route
    (same posture as the contact-form inbox, design doc section 3.1).
    `draft_description` is the admin-only, never-auto-published DeepSeek
    blurb suggestion (design doc section 6.3) -- distinct from
    `description`, which is the only field a public read ever serves.
    """

    slug: str
    name: str
    domain: str
    url: str
    description: str
    category: str = DEFAULT_CATEGORY
    tags: list[str] = field(default_factory=list)
    repo_url: str = ""
    x402_url: str = ""
    stage: str = DEFAULT_STAGE
    open_source: bool = False
    source: str = SOURCE_SUBMITTED
    status: str = STATUS_PENDING
    editor_pick: bool = False
    contact: str = ""
    service_id: str = ""
    draft_description: str = ""
    submitted_at_epoch: int = 0
    reviewed_at_epoch: int = 0
    reviewed_by: str = ""
    reject_reason: str = ""
    last_probed_at_epoch: int = 0
    reachable: bool | None = None
    last_http_status: int = 0

    @property
    def x402_enabled(self) -> bool:
        """Derived, never persisted separately (design doc section 4's orthogonal facet)."""
        return bool(self.x402_url)

    def projection_categories(self) -> list[str]:
        """Every by-category partition this entry has a row in when approved: its real category plus the reserved 'all' pseudo-category."""
        from algorand_shared.ecosystem_statements import ALL_CATEGORY_PARTITION

        return [self.category, ALL_CATEGORY_PARTITION]

    def projection_tags(self) -> list[str]:
        """Every by-tag partition this entry has a row in when approved."""
        return list(self.tags)


@dataclass(frozen=True)
class SubmissionQueueItem:
    """One row of the admin review queue (ecosystem_submissions_by_status)."""

    slug: str
    name: str
    domain: str
    url: str
    category: str
    source: str
    submitted_at_epoch: int
    status: str


__all__ = [
    "CATEGORIES",
    "DEFAULT_CATEGORY",
    "DEFAULT_STAGE",
    "MAX_TAGS",
    "MAX_TAG_LENGTH",
    "REJECT_REASONS",
    "SOURCES",
    "SOURCE_CLAIMED",
    "SOURCE_SEEDED",
    "SOURCE_SUBMITTED",
    "STAGES",
    "STAGE_BETA",
    "STAGE_LIVE",
    "STAGE_SUNSET",
    "STATUSES",
    "STATUS_APPROVED",
    "STATUS_PENDING",
    "STATUS_REJECTED",
    "EcosystemError",
    "StoredProject",
    "SubmissionQueueItem",
]
