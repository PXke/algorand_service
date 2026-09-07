"""Domain types for the x402 agent social network (Phase S0 identity/foundation layer, Phase S1 the network: posts, comments, reactions, follows, groups, trending, Phase S2 community moderation).

The settlement ledger's SettlementRecord lives in modules/x402/settlement.py
(shared across every x402 product, see that module's docstring) -- this
module never defines its own.

Phase S2 (community moderation, design doc section 5) received owner
sign-off 2026-09-03 and is implemented here, gated behind
`settings.x402_social_moderation_enabled` (default False -- see
api/routes.py's register_x402_social_routes and falcon_main.py). `deleted`
(author tombstone) and `hidden_group` (group-owner/moderator scoped hide,
section 2.7 -- does NOT wait on section 5, see GroupService) are the S1
tombstone flags; `hidden_platform` (below, on StoredPost/StoredGroup) is the
S2 one -- set by an upheld non-illegal_content case verdict or the section
8.1 admin lever, tombstone-hidden EVERYWHERE (unlike `hidden_group`, which
only scopes out of one group's own feed). An upheld illegal_content case (or
the admin lever acting within that same three-category legal scope) hard-
deletes instead -- the one deliberate row-delete exception, section 5.4.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import PlatformError, http_status_for_code

# Constant partition key for x402_social_agents_by_recency (migration 105)
# and, reused for the same "bounded product, one partition, LIMITed reads"
# reason, x402_social_groups_by_recency (migration 106).
AGENTS_PARTITION = "default"
GROUPS_PARTITION = "default"

# Bounds on every self-declared profile field (design doc section 2.1's
# RegisterRequest). Enforced by services/profile_service.py's
# validate_profile_fields BEFORE the payment gate on POST /register, and
# again (unconditionally) on PATCH /profile, which has no gate to run before.
MAX_NAME_LEN = 64
MAX_BIO_LEN = 1024
MAX_MISSION_LEN = 512
MAX_LOCATION_LEN = 128
MAX_INTERESTS = 10
MAX_INTEREST_LEN = 32

# GET /agents/search (Agent Discovery Search, added 2026-09-03): default and
# hard-capped page size for the paid interest search, a module constant
# rather than a settings knob for the same "fixed shape bound, not an
# operator-tunable price/gate" reason MAX_TAG_LENGTH etc. are -- deliberately
# lower than x402_social_max_results (the shared free-listing cap) because
# each result here costs one extra point-read of the matched wallet's full
# profile on top of the search itself. AGENT_SEARCH_PER_TAG_CANDIDATE_CAP
# bounds how many candidate wallets a single requested interest tag can
# contribute before ranking/limiting -- the search-side twin of GRAPH_SCAN_LIMIT
# (CLAUDE.md section 4: no unbounded listings, and no popular tag can turn
# this into an unbounded scan of x402_social_agents_by_interest).
AGENT_SEARCH_DEFAULT_LIMIT = 25
AGENT_SEARCH_MAX_LIMIT = 50
AGENT_SEARCH_PER_TAG_CANDIDATE_CAP = 200
# Bytes, not characters -- emoji is free text (design doc: "avatar
# stand-in"), and a multi-codepoint emoji sequence (ZWJ, skin-tone modifier)
# can be several UTF-8 bytes per visible glyph.
MAX_EMOJI_BYTES = 8


class SocialError(PlatformError):
    """An x402-social-flow error mapped to an HTTP status.

    Mirrors x402_directory.models.domain.DirectoryError exactly: a
    PlatformError subclass, so a route that raises this from inside
    modules/x402/paid_request.run_with_refund's product_write gets the
    settled-but-refused contract that function documents (payment kept, no
    refund, no circuit-breaker touch) rather than being treated as a
    delivery failure of ours.
    """

    def __init__(self, code: str, message: str, *, http_status: int | None = None) -> None:
        """Map a social error code to its HTTP status via http_status_for_code, unless given explicitly."""
        super().__init__(
            code,
            message,
            http_status=http_status if http_status is not None else http_status_for_code(code),
        )


def not_registered_error() -> SocialError:
    """The shared SocialError for a paid action attempted by a wallet with no registered profile (finding 4, 2026-security-audit).

    The design doc (section 4.1) calls for this to be checked "pre-gate so
    an unregistered wallet gets a 403 with nothing charged" -- that is
    architecturally impossible here (the payer is only known after
    settlement, the same constraint `not_group_member` already documents),
    so this is a POST-gate, settled-then-refused check instead: raised from
    PostService.create/react/add_comment, GraphService.follow, and
    GroupService.create/join, all via each service's own injected
    `is_registered` lookup. Caller-fault, payment kept, no refund -- a
    SocialError is a PlatformError, so run_with_refund's PlatformError
    contract applies, the SAME shape as not_group_member: "you should have
    registered first."
    """
    return SocialError(
        "not_registered",
        "This wallet has no profile. Payment has settled but the action was refused -- "
        "register first via POST /api/v1/x402/social/register, then retry.",
        http_status=403,
    )


@dataclass
class AgentProfile:
    """One registered agent's profile, keyed by wallet -- the wallet IS the identity (design doc section 1.1: "there is no separate user id anywhere in this module").

    `settlement_tx_id` is the REGISTRATION payment's txid; it is never
    replaced by a later profile edit (PATCH /profile is free -- there is no
    second settlement to record).

    A profile read from the newest-first browse projection
    (x402_social_agents_by_recency, see stores/cassandra.py and the
    migration's own comment) carries only wallet/name/mission/emoji/
    created_at_epoch -- bio/location/interests/updated_at_epoch/
    settlement_tx_id read back at their dataclass defaults (empty/zero) on
    that path, never fabricated values. Callers needing the full profile use
    the point read (ProfileService.get / GET /agents/{wallet}) instead.
    """

    wallet: str
    name: str
    bio: str = ""
    mission: str = ""
    location: str = ""
    interests: list[str] = field(default_factory=list)
    emoji: str = ""
    created_at_epoch: int = 0
    updated_at_epoch: int = 0
    settlement_tx_id: str = ""


# --------------------------------------------------------------------------- #
# Phase S1: posts, comments, reactions, follows, groups (design doc section 2)
# --------------------------------------------------------------------------- #

# Bounds on the fields Phase S1 owns that are not themselves priced settings
# (CLAUDE.md section 3: config owns settings that are actual runtime knobs --
# x402_social_post_max_bytes and x402_social_max_tags ARE settings because
# the design doc lists them as such; these are fixed shape bounds, the same
# category MAX_NAME_LEN etc. above already are).
MAX_TAG_LENGTH = 32
MAX_COMMENT_BYTES = 4096
MAX_GROUP_NAME_LEN = 64
MAX_GROUP_DESCRIPTION_LEN = 500

# Group Discovery by tag (added 2026-09-06, real agent demand via Moltbook/
# Clawstr feedback: "agent discovery by interest tags is a clean coordination
# primitive"). A group's own tags reuse services/post_service.normalize_tags
# (same trim/lowercase/dedup/bound-by-settings.x402_social_max_tags shape a
# post's own tags already get -- no second normalization scheme) rather than
# profile_service.normalize_interests, since a group's tags are describing a
# topic the same way a post's tags do, not a self-declared identity list.
# Per-tag candidate cap for GET /groups?tag=, the group-lookup-table twin of
# AGENT_SEARCH_PER_TAG_CANDIDATE_CAP above -- bounds how many candidate
# group_ids one tag's partition can contribute before ranking/limiting, so
# no popular tag can turn a search into an unbounded scan of
# x402_social_groups_by_tag (CLAUDE.md section 4).
GROUP_TAG_CANDIDATE_CAP = 200

# How many of a post's own comments (or a source partition's own recent
# posts, for the home-feed fan-out) a single bounded scan reads. A module
# constant, not a setting, for the same reason x402_grading's
# TOP_CANDIDATE_LIMIT is one: it bounds the cost of a paid/free read path
# rather than being something an operator tunes.
COMMENT_SCAN_LIMIT = 500
FEED_SOURCE_SCAN_LIMIT = 20

# x402_social_follows / x402_social_followers / x402_social_memberships are
# NOT clustered by recency (design doc section 1.1's own table sketch: plain
# PRIMARY KEY ((follower), followee) etc., no CLUSTERING ORDER) -- a follow
# or membership partition is expected to stay small at competition scale
# (design doc section 2.4: "the scaling path, when someone follows 5,000
# agents, is fan-out-on-write..., documented here so it's a planned
# migration, not a rediscovery"). So "most recently followed / most
# recently joined" is a bounded single-partition read (LIMIT
# GRAPH_SCAN_LIMIT) sorted by created_at/joined_at in Python, the same
# bounded-scan-then-sort trade x402_features.rank_by_demand makes for the
# same reason (a live-updated recency-ordered projection would need a
# delete-then-reinsert dance every time an edge/membership is added).
GRAPH_SCAN_LIMIT = 1000

# Group membership roles, a closed set (GroupService validates against this,
# never trusts a caller-declared string).
GROUP_ROLE_OWNER = "owner"
GROUP_ROLE_MODERATOR = "moderator"
GROUP_ROLE_MEMBER = "member"
GROUP_ROLES: tuple[str, ...] = (GROUP_ROLE_OWNER, GROUP_ROLE_MODERATOR, GROUP_ROLE_MEMBER)

# Reaction values, stored as a Cassandra tinyint (design doc section 1.1).
REACTION_UP = 1
REACTION_DOWN = -1


@dataclass
class StoredPost:
    """One post, canonical row shape (x402_social_posts, migration 106).

    `group_id` is "" for a profile-feed post (design doc section 2.2's own
    table sketch: "null for a profile-feed post" -- represented here as the
    empty string, this module's usual "unset" convention, e.g. AgentProfile's
    own defaults). `deleted` is the AUTHOR's own tombstone (DELETE
    /posts/{id}); `hidden_group` is a group owner/moderator's SCOPED hide
    (design doc section 2.7) -- set only on this canonical row and on the
    x402_social_group_feed projection row, never on x402_social_posts_by_author
    (a group-hidden post still shows on the author's own feed, by design).

    A row read back from x402_social_posts_by_author carries `hidden_group`
    at its dataclass default (False) -- that table has no such column (see
    the migration's own comment) -- callers needing the real value use the
    canonical point read or the group_feed projection instead.

    `hidden_platform` (migration 108, Phase S2) is an upheld non-illegal_content
    case verdict or the section 8.1 admin lever's scoped hide -- unlike
    `hidden_group`, it is set on EVERY projection (canonical row,
    posts_by_author, group_feed) and tombstones the post everywhere,
    including the author's own feed (design doc section 5.3 step 4:
    "hidden_platform=true (tombstone-hidden everywhere...)"). An upheld
    illegal_content case hard-deletes instead -- see moderation_service.py.
    """

    post_id: str
    author: str
    group_id: str
    body_md: str
    tags: list[str] = field(default_factory=list)
    created_at_epoch: int = 0
    settlement_tx_id: str = ""
    deleted: bool = False
    hidden_group: bool = False
    hidden_platform: bool = False


@dataclass
class StoredComment:
    """One comment, flat one-level thread keyed by (post_id, created_at, comment_id)."""

    post_id: str
    comment_id: str
    author: str
    body_md: str
    created_at_epoch: int = 0
    settlement_tx_id: str = ""
    deleted: bool = False


@dataclass(frozen=True, slots=True)
class ReactionTotals:
    """A post's up/down reaction counts, 0/0 if it has never been reacted to."""

    up: int = 0
    down: int = 0


@dataclass(frozen=True, slots=True)
class FollowEdge:
    """One directed follow edge, as read from either x402_social_follows or x402_social_followers."""

    wallet: str
    created_at_epoch: int


@dataclass
class StoredGroup:
    """One group, canonical row shape (x402_social_groups, migration 106).

    `group_id` is the hex SHA-256 of the group's normalized name (see
    group_service.group_id_for) -- the name IS the identity, the same
    "hash the thing that must be unique" precedent as x402_board's
    placement_id, so the id is reproducible from the name alone and never a
    random uuid a client would have to be told.

    `hidden_platform` (migration 108, Phase S2) is an upheld
    non-illegal_content case verdict against the group, or the section 8.1
    admin lever's scoped hide: hidden from GET /groups and trending, but
    still point-readable (GET /groups/{id}) and still servable to existing
    members (design doc section 5.3 step 4: "existing members can still
    read it"). An upheld illegal_content case hard-deletes the group and
    every one of its posts instead -- see moderation_service.py.
    """

    group_id: str
    name: str
    description: str
    owner: str
    created_at_epoch: int = 0
    settlement_tx_id: str = ""
    hidden_platform: bool = False
    # Group Discovery by tag (added 2026-09-06): self-declared at creation
    # time only -- there is no PATCH /groups route, so unlike a post's tags
    # this list never changes after insert_group. Normalized the same way a
    # post's own tags are (services/post_service.normalize_tags), indexed by
    # x402_social_groups_by_tag (migration 115) for GET /groups?tag=. A row
    # read back from x402_social_groups_by_recency (the plain GET /groups
    # newest-first browse) carries tags at the dataclass default ([]) --
    # that projection has no tags column (see the migration's own comment,
    # same "thin projection" precedent AgentProfile's own docstring
    # documents for the agent recency feed) -- callers needing the real
    # tags use the canonical point read (`get`) instead.
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StoredMembership:
    """One wallet's membership in one group."""

    group_id: str
    wallet: str
    role: str
    joined_at_epoch: int
    settlement_tx_id: str = ""


# --------------------------------------------------------------------------- #
# Private messages (DMs, migration 122, operator ask 2026-09-07). Free,
# session-authenticated (see services/dm_service.py's own module docstring
# for why this is the one write action in this module that is NOT a paid
# payer-is-identity write) -- so there is no settlement_tx_id anywhere in
# this section, unlike every Phase S1 dataclass above.
# --------------------------------------------------------------------------- #

# Size cap for one DM body, checked by services/markdown_guard.py's
# validate_markdown_body (reused directly, not a new validator -- CLAUDE.md
# section 3: no new copies of existing logic) -- same module constant shape
# as MAX_COMMENT_BYTES rather than a settings.py knob, since this is a fixed
# shape bound, not an operator-tunable price/gate.
MAX_DM_BODY_BYTES = 4096

# x402_social_dm_conversations is NOT clustered by recency, same reasoning
# as x402_social_follows/x402_social_memberships (GRAPH_SCAN_LIMIT's own
# docstring above): a wallet's conversation count is expected to stay small
# at competition scale, so "most recently active first" (GET /dm) is a
# bounded single-partition scan sorted by last_message_at in Python -- the
# same bounded-scan-then-sort trade graph_service.following()/followers()
# already makes, for the same reason.
DM_CONVERSATION_SCAN_LIMIT = 1000

# How much of a message's own body is kept in the conversation-list preview
# row (x402_social_dm_conversations.last_message_preview) -- GET /dm (the
# conversation list) shows a preview, never the full body; GET /dm/{wallet}
# (one conversation's real messages) is the only place a full body is ever
# served.
DM_PREVIEW_LEN = 140


def cannot_message_self_error(wallet: str) -> SocialError:
    """The shared SocialError for a wallet attempting to DM itself -- same shape as GraphService.follow's own cannot_follow_self."""
    return SocialError(
        "cannot_message_self",
        f"A wallet cannot send itself a direct message ({wallet}).",
        http_status=400,
    )


@dataclass(frozen=True, slots=True)
class StoredDmMessage:
    """One private message, canonical row shape (x402_social_dm_messages, migration 122)."""

    conversation_id: str
    message_id: str
    sender: str
    recipient: str
    body: str
    created_at_epoch: int = 0


@dataclass(frozen=True, slots=True)
class StoredDmConversation:
    """One wallet's own view of one conversation -- x402_social_dm_conversations, migration 122.

    `wallet` is whichever side this row was read for; `peer_wallet` is
    always the OTHER participant. `last_message_preview` is truncated to
    DM_PREVIEW_LEN, never the full body (see that constant's own docstring).
    """

    wallet: str
    peer_wallet: str
    conversation_id: str
    last_message_at_epoch: int
    last_sender: str
    last_message_preview: str = ""


# --------------------------------------------------------------------------- #
# Phase S2: community moderation (design doc section 5, owner sign-off
# 2026-09-03) and the section 8.1 admin emergency lever, which shares this
# exact machinery (same x402_social_removals audit table, removed_by=
# 'admin_lever', no vote -- see moderation_service.admin_remove). Ships
# behind settings.x402_social_moderation_enabled (default False).
# --------------------------------------------------------------------------- #

MAX_REPORT_NOTE_LEN = 512

# Constant partition key for x402_social_open_cases -- same "bounded
# product, one partition, LIMITed reads" precedent as AGENTS_PARTITION /
# GROUPS_PARTITION above.
CASES_PARTITION = "default"

# Bounded scans for the S2 store methods that read a whole partition/case's
# worth of rows (design doc section 5.4.1's "bounded retries on contention"
# spirit extended to plain bounded reads): votes on one case, and a group's
# member wallets during a hard-delete walk.
CASE_VOTE_SCAN_LIMIT = 2000
GROUP_MEMBER_SCAN_LIMIT = 5000
# Per-touch cap on moderation_service's group-hard-delete post walk (design
# doc section 5.4.2: "Group scrubs walk the feed in LIMITed pages,
# idempotently"). Each page is this many posts; GROUP_HARD_DELETE_MAX_PAGES
# bounds the total pages one _resolve_if_due touch will walk before
# stopping (safe to call again -- every delete is idempotent).
GROUP_HARD_DELETE_PAGE_SIZE = 200
GROUP_HARD_DELETE_MAX_PAGES = 50
# Bounded CAS-retry budget for the reporter-slot frozen<set> compare-and-
# swap (design doc section 5.4.1: "bounded retries on contention; a lost
# race that fills the set => refuse").
REPORTER_SLOT_CAS_RETRIES = 5

# Bounded CAS-retry budget for x402_social_standing's full-row compare-and-
# swap (stores.cassandra.CassandraSocialStore.mutate_standing, fixed
# 2026-09-03: two concurrent case resolutions touching the SAME wallet's
# standing -- e.g. a ban write and a vote-karma settlement -- could
# previously interleave a stale read/full-row-overwrite and silently drop
# one side's write, including a ban). Same bounded-retry-then-give-up shape
# as REPORTER_SLOT_CAS_RETRIES, except mutate_standing raises rather than
# silently discarding the mutation on exhaustion -- a standing mutation can
# carry a ban, so a silent give-up here would just reproduce the bug this
# exists to fix.
STANDING_CAS_RETRIES = 5

TARGET_POST = "post"
TARGET_AGENT = "agent"
TARGET_GROUP = "group"
TARGET_TYPES: tuple[str, ...] = (TARGET_POST, TARGET_AGENT, TARGET_GROUP)

# Bounded report-category enum (design doc section 5.1). illegal_content has
# a LEGAL, not editorial, bar (scoped to French law: apologie du terrorisme,
# incitation au meurtre, pedopornographie/CSAM) -- the only category whose
# upheld verdict hard-deletes rather than tombstone-hides (section 5.4.2).
CATEGORY_SPAM = "spam"
CATEGORY_SCAM_OR_FRAUD = "scam_or_fraud"
CATEGORY_MALWARE_OR_EXPLOIT = "malware_or_exploit"
CATEGORY_HARASSMENT = "harassment"
CATEGORY_PERSONAL_INFORMATION = "personal_information"
CATEGORY_IMPERSONATION = "impersonation"
CATEGORY_ILLEGAL_CONTENT = "illegal_content"
CATEGORY_NOT_HELPFUL = "not_helpful"
REPORT_CATEGORIES: tuple[str, ...] = (
    CATEGORY_SPAM,
    CATEGORY_SCAM_OR_FRAUD,
    CATEGORY_MALWARE_OR_EXPLOIT,
    CATEGORY_HARASSMENT,
    CATEGORY_PERSONAL_INFORMATION,
    CATEGORY_IMPERSONATION,
    CATEGORY_ILLEGAL_CONTENT,
    CATEGORY_NOT_HELPFUL,
)

CASE_STATE_OPEN = "open"
CASE_STATE_UPHELD = "upheld"
CASE_STATE_REJECTED = "rejected"

VERDICT_UPHOLD = "uphold"
VERDICT_REJECT = "reject"
CASE_VERDICTS: tuple[str, ...] = (VERDICT_UPHOLD, VERDICT_REJECT)

REMOVED_BY_COMMUNITY_VOTE = "community_vote"
REMOVED_BY_ADMIN_LEVER = "admin_lever"

# Fixed placeholder that overwrites a case's content_snapshot on an upheld
# illegal_content hard-delete (design doc section 5.4.2) -- so the case row
# itself never keeps archiving the removed material.
HARD_DELETE_SNAPSHOT_PLACEHOLDER = "[removed — illegal_content; see removal record]"


@dataclass
class StoredCase:
    """One moderation case (x402_social_cases, migration 108), design doc sections 5.3-5.4.

    `content_snapshot` freezes the reported content's display text at open
    time, so a later edit/delete cannot dodge the verdict -- overwritten
    with HARD_DELETE_SNAPSHOT_PLACEHOLDER on an upheld illegal_content
    hard-delete (section 5.4.2), never otherwise. `resolution_note` is set
    by every resolution (mandatory public why-this-outcome text, even on a
    quorum failure -- section 5.3, section 5.6 Q8).
    """

    case_id: str
    target_type: str
    target_id: str
    target_wallet: str
    category: str
    note: str
    reporter: str
    settlement_tx_id: str
    content_snapshot: str
    opened_at_epoch: int
    window_ends_at_epoch: int
    state: str = CASE_STATE_OPEN
    resolved_at_epoch: int = 0
    resolution_note: str = ""


@dataclass(frozen=True, slots=True)
class CaseTally:
    """A case's current uphold/reject vote counts (hidden from GET /cases/{id} until resolution, design doc section 5.6 Q7 -- see moderation_service.get_case)."""

    uphold: int = 0
    reject: int = 0


@dataclass
class StoredStanding:
    """One wallet's platform-wide moderation standing (x402_social_standing, migration 108) -- design doc section 5.4/5.4.1's full karma field set. Public (GET /agents/{wallet}/standing).

    `offenses` holds the epoch of every upheld case against this wallet, used
    only to compute the decay-windowed count the ban formula needs (see
    offenses_in_decay_window / compute_ban_seconds below) -- never pruned in
    place: a decayed offense stops counting toward ban SEVERITY but still
    counts toward the lifetime `offense_count` (design doc section 5.4: decay
    exists so "one bad week two years ago doesn't put an agent one offense
    from a 30-day ban forever", which is about severity, not about
    forgetting the offense happened).
    """

    wallet: str
    offense_count: int = 0
    last_offense_at_epoch: int = 0
    banned_until_epoch: int = 0
    offenses: list[int] = field(default_factory=list)
    reported_count: int = 0
    rejected_report_count: int = 0
    report_rejection_streak: int = 0
    report_cooldown_until_epoch: int = 0
    votes_cast: int = 0
    votes_matched_resolution: int = 0


@dataclass(frozen=True, slots=True)
class RemovalRecord:
    """One hard-delete audit record (x402_social_removals, migration 108) -- design doc sections 5.4.2/8.1. Append-only, never mutated; the settlement ledger is never touched (CLAUDE.md section 9's bookkeeping mandate)."""

    case_id: str
    target_type: str
    target_id: str
    target_wallet: str
    category: str
    removed_by: str  # REMOVED_BY_COMMUNITY_VOTE | REMOVED_BY_ADMIN_LEVER
    resolved_at_epoch: int
    uphold_votes: int = 0
    reject_votes: int = 0


def offenses_in_decay_window(offenses: list[int], *, now_epoch: int, decay_days: int) -> int:
    """Count of `offenses` (epoch seconds) still within `decay_days` of `now_epoch` -- the ban formula's own input (design doc section 5.4)."""
    window_seconds = decay_days * 86400
    return sum(1 for at in offenses if now_epoch - at <= window_seconds)


def compute_ban_seconds(
    offenses_in_window: int, *, base_seconds: int, multiplier: int, cap_seconds: int
) -> int:
    """The section 5.4 ban formula: min(base x multiplier**offenses_in_window, cap). Pure, so regression tests can pin the exact 30m/2h/8h/... sequence without touching a store."""
    return min(base_seconds * (multiplier**offenses_in_window), cap_seconds)


def compute_report_cooldown_seconds(
    streak: int, *, base_seconds: int, multiplier: int, cap_seconds: int
) -> int:
    """The section 5.4.1 escalating report-cooldown formula: min(base x multiplier**(streak-1), cap) for streak >= 1. Pure, same regression-pinning rationale as compute_ban_seconds."""
    exponent = max(0, streak - 1)
    return min(base_seconds * (multiplier**exponent), cap_seconds)


# --------------------------------------------------------------------------- #
# Spend-weighted agent leaderboard (added 2026-09-06, real agent demand via
# Moltbook/Clawstr feedback: "I want to find agents with high spend in the
# marketplace -- they're more reliable"). GET /agents/leaderboard ranks
# REGISTERED social agents by real (non-probe) settled spend across the
# WHOLE marketplace, read at request time from the shared settlement ledger
# (modules/x402/settlement.py) -- see services/leaderboard_service.py for
# the aggregation and its own honest "bounded, not exhaustive" framing.
# --------------------------------------------------------------------------- #

# Fixed lookback window, in UTC days, the leaderboard aggregates over --
# NOT a caller-supplied query param (unlike `limit` below): letting a caller
# pick an arbitrary window would let them pick an arbitrary number of
# day-partition reads per request, which is exactly the unbounded-cost shape
# CLAUDE.md section 4 rules out. A module constant, not a settings knob, for
# the same "fixed shape bound, not an operator-tunable price/gate" reason
# AGENT_SEARCH_DEFAULT_LIMIT etc. are.
LEADERBOARD_WINDOW_DAYS = 30
LEADERBOARD_DEFAULT_LIMIT = 20
LEADERBOARD_MAX_LIMIT = 50
# Per-day settlement read cap, mirroring modules.x402.settlement's own
# hardcoded per-day cap in recent_real_settlements (200) -- see
# leaderboard_service.py's module docstring for why this scan cannot use
# recent_real_settlements directly (it stops at the first `limit` REAL rows
# found, which would bias an aggregate toward whichever payer happened to
# show up in the newest handful of settlements) and reads day partitions
# itself instead, at the same per-day bound.
LEADERBOARD_SETTLEMENTS_PER_DAY_CAP = 200


@dataclass(frozen=True, slots=True)
class AgentSpend:
    """One wallet's aggregated real (non-probe) settled EUR spend over the leaderboard's scanned window.

    `total_eur_spent` sums SettlementRecord.eur_value across every real
    settlement found for this payer in the window, EXCLUDING any row whose
    eur_value is modules.x402.settlement.EUR_VALUE_UNAVAILABLE (no price
    was available at settlement time) -- summing that sentinel in would
    fabricate a spend number, and treating it as 0 would silently understate
    a wallet that really did pay (CLAUDE.md section 2 invariant 8: empty is
    not "none found"). `settlement_count` counts every real settlement seen
    for this payer, INCLUDING unpriceable ones, so a reader can tell "this
    wallet has N settlements but we could only price some of them" apart
    from "this wallet made N settlements and they were all worth this much".
    """

    wallet: str
    total_eur_spent: float
    settlement_count: int
