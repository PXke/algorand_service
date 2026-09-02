"""Domain types for the x402 agent social network (Phase S0 identity/foundation layer, Phase S1 the network: posts, comments, reactions, follows, groups, trending).

The settlement ledger's SettlementRecord lives in modules/x402/settlement.py
(shared across every x402 product, see that module's docstring) -- this
module never defines its own.

Phase S2 (community moderation, design doc section 5) is explicitly NOT
approved for implementation -- see that section's own owner-sign-off block.
Nothing in this module defines a `hidden_platform` field or any case/vote/
standing type: the design doc's own S1 table sketch ties `hidden_platform`
to section 5 (a case verdict) and section 8.1 (an admin emergency lever),
neither of which is in scope here, and CLAUDE.md's Phase-S1 task brief is
explicit that S2 must not even be scaffolded. `deleted` (author tombstone)
and `hidden_group` (group-owner/moderator scoped hide, section 2.7 -- this
one does NOT wait on section 5, see GroupService) are the only tombstone
flags Phase S1 needs.
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
    """

    group_id: str
    name: str
    description: str
    owner: str
    created_at_epoch: int = 0
    settlement_tx_id: str = ""


@dataclass(frozen=True, slots=True)
class StoredMembership:
    """One wallet's membership in one group."""

    group_id: str
    wallet: str
    role: str
    joined_at_epoch: int
    settlement_tx_id: str = ""
