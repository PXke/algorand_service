"""Domain types for the Algorand Open Registry (roadmap item 26).

Free, anonymous, human-reviewed ecosystem-project listings,
glossary-shaped -- deliberately not a paid x402 product (see
`docs/awesome-algorand-directory-design.md` section 6 for why).
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

# Human names for the closed category enum (design doc section 4), mirrors
# frontend/src/lib/api/ecosystem.ts's ECOSYSTEM_CATEGORY_LABELS 1:1 -- own
# copy, no cross-service import, same convention that file's own docstring
# already uses for CATEGORIES itself. Backend-only use: the registry SSR
# pages (seo/render.py's render_registry_index/render_registry_entry).
CATEGORY_LABELS: dict[str, str] = {
    "wallets": "Wallets & key management",
    "defi-exchange": "Exchanges & AMMs",
    "defi-lending": "Lending, stablecoins & yield",
    "nfts": "NFTs & collectibles",
    "gaming": "Gaming & metaverse",
    "identity": "Identity, names & credentials",
    "rwa": "Real-world assets",
    "payments": "Payments & commerce",
    "infrastructure": "Infrastructure & nodes",
    "devtools": "Developer tools & SDKs",
    "analytics": "Explorers & analytics",
    "governance": "Governance & DAOs",
    "interop": "Oracles & bridges",
    "security": "Security & auditing",
    "agents": "AI & agents",
    "enterprise": "Enterprise & impact",
    "media": "Education & media",
    "other": "Other",
}


def category_label(slug: str) -> str:
    """Human label for a category slug; falls back to the slug itself for an unrecognized value."""
    return CATEGORY_LABELS.get(slug, slug)


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

# "Suggest a change" (owner ask, 2026-09-08: "Yes we need a suggest a
# change") -- a free, anonymous request against an ALREADY-APPROVED entry,
# distinct from a fresh submission: no domain dedupe, no liveness check, no
# category, just a human-reviewed note. `change` asks the reviewer to fix
# something on the live entry; `removal` asks for it to be taken down
# (reviewer can act via update_entry's stage=sunset, or delete_entry -- see
# request_service's own docstring for why this never auto-applies either).
REQUEST_KIND_CHANGE = "change"
REQUEST_KIND_REMOVAL = "removal"
REQUEST_KINDS: tuple[str, ...] = (REQUEST_KIND_CHANGE, REQUEST_KIND_REMOVAL)

REQUEST_STATUS_PENDING = "pending"
REQUEST_STATUS_RESOLVED = "resolved"
REQUEST_STATUS_DISMISSED = "dismissed"
REQUEST_STATUSES: tuple[str, ...] = (
    REQUEST_STATUS_PENDING,
    REQUEST_STATUS_RESOLVED,
    REQUEST_STATUS_DISMISSED,
)

MAX_REQUEST_MESSAGE_LENGTH = 1000
MIN_REQUEST_MESSAGE_LENGTH = 10


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
    # Free-text, submitter-suggested category name -- shown only to an admin
    # reviewer (design doc's closed-enum category stays closed; this is not
    # auto-applied to `category`, which always stays one of CATEGORIES). Set
    # only when the submitter picked "other" and had something more specific
    # in mind (2026-09-08, owner ask: "allow people to suggest new
    # categories").
    category_suggestion: str = ""
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


@dataclass
class EntryRequest:
    """One "suggest a change" / removal request against an already-approved entry.

    Anonymous by default (`contact` is optional, private, admin-only --
    same posture as `StoredProject.contact`). Never mutates the target
    entry itself; a human reviewer reads the message and acts through the
    ordinary admin edit/delete path.
    """

    request_id: str
    slug: str
    kind: str
    message: str
    contact: str = ""
    status: str = REQUEST_STATUS_PENDING
    created_at_epoch: int = 0
    resolved_at_epoch: int = 0
    resolved_by: str = ""


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
    "CATEGORY_LABELS",
    "DEFAULT_CATEGORY",
    "DEFAULT_STAGE",
    "MAX_REQUEST_MESSAGE_LENGTH",
    "MAX_TAGS",
    "MAX_TAG_LENGTH",
    "MIN_REQUEST_MESSAGE_LENGTH",
    "REJECT_REASONS",
    "REQUEST_KINDS",
    "REQUEST_KIND_CHANGE",
    "REQUEST_KIND_REMOVAL",
    "REQUEST_STATUSES",
    "REQUEST_STATUS_DISMISSED",
    "REQUEST_STATUS_PENDING",
    "REQUEST_STATUS_RESOLVED",
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
    "EntryRequest",
    "StoredProject",
    "SubmissionQueueItem",
    "category_label",
]
