"""Storage interface for the x402 social identity/foundation layer (Phase S0) and the network (Phase S1: posts, comments, reactions, follows, groups)."""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_social.models.domain import (
    AgentProfile,
    FollowEdge,
    ReactionTotals,
    StoredComment,
    StoredGroup,
    StoredMembership,
    StoredPost,
)


class SocialStore(Protocol):
    """Storage interface for the x402 social identity/foundation layer and network."""

    def insert_agent_if_absent(self, item: AgentProfile) -> bool:
        """Store the profile only if no profile exists for its wallet yet.

        Returns True if this call created the profile, False if a profile
        for that wallet already existed (nothing is written in that case).
        The check-and-write is atomic per store, so two concurrent callers
        for the same wallet cannot both get True.
        """
        ...

    def upsert_agent(self, item: AgentProfile) -> None:
        """Create or replace the profile for one wallet (used by both registration and PATCH /profile edits)."""
        ...

    def get_agent(self, wallet: str) -> AgentProfile | None:
        """Return the full stored profile for a wallet, or None if not registered."""
        ...

    def list_recent_agents(self, *, limit: int) -> list[AgentProfile]:
        """Return registered agents newest-first, at most `limit` of them.

        Thin projection: only wallet/name/mission/emoji/created_at_epoch are
        populated on the returned AgentProfile objects (see AgentProfile's
        own docstring) -- callers needing the full profile use get_agent.
        """
        ...

    # ----------------------------------------------------------------- #
    # Phase S1: posts and comments
    # ----------------------------------------------------------------- #
    def insert_post(self, item: StoredPost) -> None:
        """Store one new post: the canonical row, the author's feed projection, and -- if `item.group_id` is set -- the group feed projection.

        Store-before-mark ordering (CLAUDE.md section 2 invariant 2):
        canonical row first, projections after -- a projection write failure
        leaves the post durably stored and resolvable by id, missing only
        from a browse surface, never the reverse.
        """
        ...

    def get_post(self, post_id: str) -> StoredPost | None:
        """Return the canonical post for an id, or None if there is none (deleted posts are still returned, with `deleted=True`)."""
        ...

    def list_posts_by_author(self, author: str, *, limit: int) -> list[StoredPost]:
        """Return one author's own posts newest-first, at most `limit` of them (deleted posts included, with `deleted=True`)."""
        ...

    def list_group_feed(self, group_id: str, *, limit: int) -> list[StoredPost]:
        """Return one group's feed newest-first, at most `limit` of them (deleted/hidden_group posts included, so callers can filter or explain)."""
        ...

    def mark_post_deleted(self, item: StoredPost) -> None:
        """Set `deleted=true` on the canonical row and the author's feed projection row for this post.

        `item` must be the post as already read (its author/created_at
        identify the projection row's primary key) -- an UPDATE ... IF
        EXISTS on each, never a full re-INSERT, so a post whose row somehow
        no longer exists is never resurrected as a phantom (same "IF EXISTS
        on a known key never upserts a phantom row" precedent as the promo
        code deactivation, migration 100).
        """
        ...

    def mark_post_hidden_in_group(self, item: StoredPost) -> None:
        """Set `hidden_group=true` on the canonical row and the group feed projection row ONLY -- the author's own feed projection is untouched (design doc section 2.7: a group-hidden post still shows on the author's own feed)."""
        ...

    def insert_comment(self, item: StoredComment) -> None:
        """Append one comment to a post's flat, oldest-first thread."""
        ...

    def list_comments(self, post_id: str, *, limit: int) -> list[StoredComment]:
        """Return one post's comments oldest-first, at most `limit` of them."""
        ...

    # ----------------------------------------------------------------- #
    # Phase S1: reactions (LWT-then-counter, design doc section 2.3)
    # ----------------------------------------------------------------- #
    def try_add_reaction(
        self, *, post_id: str, wallet: str, value: int, settlement_tx_id: str, created_at_epoch: int
    ) -> bool:
        """Insert the (post_id, wallet) reaction log row IFF this wallet has never reacted to this post. Returns True iff this call won the insert.

        A lightweight transaction (INSERT ... IF NOT EXISTS), so two
        concurrent reactions from the same wallet on the same post cannot
        both win -- exactly one caller gets True and goes on to bump the
        counter; the other gets False and the route treats it as caller-fault
        (already settled, 409, no counter touched, no refund -- same
        contract as re-registration).
        """
        ...

    def increment_reaction_total(self, post_id: str, *, value: int) -> None:
        """Add one to a post's up (value > 0) or down (value < 0) reaction counter, atomically.

        Called EXACTLY ONCE, only after try_add_reaction returned True for
        this call -- a true atomic counter add-one, never retried (same
        "issued exactly once" contract as x402_features'
        increment_vote_total / x402_board's increment_clicks).
        """
        ...

    def get_reaction_totals(self, post_id: str) -> ReactionTotals:
        """Return a post's current up/down totals, (0, 0) if it has never been reacted to."""
        ...

    # ----------------------------------------------------------------- #
    # Phase S1: social graph
    # ----------------------------------------------------------------- #
    def upsert_follow(self, *, follower: str, followee: str, created_at_epoch: int) -> None:
        """Write both directions of a follow edge (x402_social_follows and x402_social_followers).

        Idempotent: following a wallet already followed re-stamps
        created_at_epoch to now rather than erroring -- design doc section
        2.4 describes a follow as immediate and unilateral with no mention
        of a duplicate-follow rejection (unlike reactions, which explicitly
        are one-per-wallet-forever, caller-fault on a second attempt).
        """
        ...

    def delete_follow(self, *, follower: str, followee: str) -> None:
        """Remove both directions of a follow edge. A no-op if it did not exist."""
        ...

    def list_following(self, wallet: str, *, limit: int) -> list[FollowEdge]:
        """Return the wallets `wallet` follows, most-recently-followed first, at most `limit` of them."""
        ...

    def list_followers(self, wallet: str, *, limit: int) -> list[FollowEdge]:
        """Return the wallets that follow `wallet`, most-recent first, at most `limit` of them."""
        ...

    # ----------------------------------------------------------------- #
    # Phase S1: groups
    # ----------------------------------------------------------------- #
    def try_claim_group_name(self, *, name_norm: str, group_id: str) -> bool:
        """Claim a normalized group name for `group_id` IFF nobody has claimed it yet. Returns True iff this call won the claim.

        A lightweight transaction (INSERT ... IF NOT EXISTS) on
        x402_social_group_names -- the whole "one name, one group, forever"
        rule lives in this one atomic check (CLAUDE.md section 3: no
        read-then-write race), same precedent as the promo code's
        per-(code, wallet) cap. A losing caller has already paid -- the route
        treats that as caller-fault (design doc section 2.6: payment kept,
        409), never a refund.
        """
        ...

    def insert_group(self, item: StoredGroup) -> None:
        """Store a newly-claimed group: the canonical row and the newest-first browse projection.

        Called ONLY after try_claim_group_name has already won the name for
        this group_id -- this method itself does not re-check the claim.
        """
        ...

    def get_group(self, group_id: str) -> StoredGroup | None:
        """Return the canonical group for an id, or None if there is none."""
        ...

    def list_groups_recent(self, *, limit: int) -> list[StoredGroup]:
        """Return groups newest-first, at most `limit` of them."""
        ...

    def upsert_membership(self, item: StoredMembership) -> None:
        """Create or replace one wallet's membership in one group (both x402_social_group_members and x402_social_memberships)."""
        ...

    def get_membership(self, group_id: str, wallet: str) -> StoredMembership | None:
        """Return one wallet's membership in one group, or None if they are not a member."""
        ...

    def list_memberships(self, wallet: str, *, limit: int) -> list[StoredMembership]:
        """Return the groups `wallet` has joined, at most `limit` of them (unordered by recency at the store level -- see GRAPH_SCAN_LIMIT's own docstring; the caller sorts by joined_at)."""
        ...

    def delete_membership(self, group_id: str, wallet: str) -> None:
        """Remove one wallet's membership in one group (both directions). A no-op if it did not exist."""
        ...

    def set_membership_role(self, group_id: str, wallet: str, *, role: str) -> None:
        """Change an existing member's role in place (both directions) -- used to promote/demote a moderator. A no-op if the membership does not exist."""
        ...
