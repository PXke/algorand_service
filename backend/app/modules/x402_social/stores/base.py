"""Storage interface for the x402 social identity/foundation layer (Phase S0) and the network (Phase S1: posts, comments, reactions, follows, groups)."""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_social.models.domain import (
    AgentProfile,
    CaseTally,
    FollowEdge,
    ReactionTotals,
    RemovalRecord,
    StoredCase,
    StoredComment,
    StoredGroup,
    StoredMembership,
    StoredPost,
    StoredStanding,
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

    def list_posts_by_authors(self, authors: list[str], *, limit: int) -> list[StoredPost]:
        """Return `list_posts_by_author`'s result for every author in `authors`, concatenated (order across authors is not meaningful -- the caller sorts).

        Backing-store-dependent performance note (optimization pass,
        2026-09-02): the Cassandra store fires all of `authors`'s reads
        concurrently instead of one sequential round-trip per author -- this
        exists as its own store method, not a loop over `list_posts_by_author`
        in a service, specifically so that concurrency lives at the store
        boundary where a Cassandra session is available to fan out on.
        """
        ...

    def list_group_feeds(self, group_ids: list[str], *, limit: int) -> list[StoredPost]:
        """Return `list_group_feed`'s result for every group in `group_ids`, concatenated. Same concurrency rationale as `list_posts_by_authors`."""
        ...

    def mark_post_deleted(self, item: StoredPost) -> None:
        """Set `deleted=true` on the canonical row, the author's feed projection row, and (if `item.group_id` is set) the group feed projection row for this post.

        `item` must be the post as already read (its author/group_id/
        created_at identify each projection row's primary key) -- an
        UPDATE ... IF EXISTS on each, never a full re-INSERT, so a post
        whose row somehow no longer exists is never resurrected as a
        phantom (same "IF EXISTS on a known key never upserts a phantom
        row" precedent as the promo code deactivation, migration 100). The
        group feed row is included so a deleted group post stops serving
        its body via GET /groups/{id}/feed and the group half of GET /feed
        (finding 1, 2026-security-audit) -- the author's own feed row alone
        is not enough for a group post.
        """
        ...

    def mark_post_hidden_in_group(self, item: StoredPost) -> None:
        """Set `hidden_group=true` on the canonical row and the group feed projection row ONLY -- the author's own feed projection is untouched (design doc section 2.7: a group-hidden post still shows on the author's own feed)."""
        ...

    def mark_post_hidden_platform(self, item: StoredPost) -> None:
        """Set `hidden_platform=true` EVERYWHERE this post is projected: the canonical row, the author feed row, and (if `item.group_id` is set) the group feed row (Phase S2, design doc section 5.3 step 4). Unlike `mark_post_hidden_in_group`, the author feed row IS included -- a case verdict tombstones a post everywhere."""
        ...

    def hard_delete_post(self, item: StoredPost) -> None:
        """Real row removal: the canonical row, the author feed row, (if set) the group feed row, and the post's whole comment partition (Phase S2, design doc section 5.4.2 -- the one illegal_content-only exception to this module's tombstone-only rule). Idempotent: safe to call more than once for the same post."""
        ...

    def insert_comment(self, item: StoredComment) -> None:
        """Append one comment to a post's flat, oldest-first thread."""
        ...

    def list_comments(self, post_id: str, *, limit: int) -> list[StoredComment]:
        """Return one post's comments oldest-first, at most `limit` of them."""
        ...

    def count_comments(self, post_id: str, *, limit: int) -> int:
        """Number of comments on a post, at most `limit` -- a narrow-projection count, not list_comments's full rows (optimization pass, 2026-09-02: comment_count() no longer pays for body_md it never reads)."""
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

    def release_group_name(self, *, name_norm: str, group_id: str) -> None:
        """Best-effort compensating release of a name claim this SAME group_id won, used ONLY when group creation fails after the LWT claim but before the group is fully stored (finding 3, 2026-security-audit).

        Deletes the x402_social_group_names row for `name_norm` iff it still
        maps to `group_id` -- never a different group's legitimate claim on
        the same normalized name. A no-op if the row was already released or
        never matched. Callers treat this as best-effort: if the release
        itself fails, they log a warning and re-raise the ORIGINAL failure,
        never masking it.
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

    # ----------------------------------------------------------------- #
    # Phase S2: community moderation (design doc section 5) and the section
    # 8.1 admin lever, which shares this exact hard-delete/removal-audit
    # machinery. See services/moderation_service.py for the case lifecycle.
    # ----------------------------------------------------------------- #
    def mark_group_hidden_platform(self, item: StoredGroup) -> None:
        """Set `hidden_platform=true` on the canonical row and the recency browse projection row (design doc section 5.3 step 4): hidden from discovery, still point-readable and still servable to existing members."""
        ...

    def hard_delete_group_shell(self, item: StoredGroup, *, name_norm: str) -> None:
        """Real row removal of the group's OWN rows only: canonical row, recency projection, and the name claim (freed -- re-claiming costs the full price again). Does NOT touch membership rows or the group's posts -- see `list_group_member_wallets` / `delete_group_memberships_partition` and `list_group_feed` for the rest of a group hard-delete. Idempotent."""
        ...

    def list_group_member_wallets(self, group_id: str, *, limit: int) -> list[str]:
        """Bounded read of a group's member wallets, for the group hard-delete walk."""
        ...

    def delete_group_memberships_partition(self, group_id: str) -> None:
        """Delete every x402_social_group_members row for `group_id` (one partition delete). The caller is separately responsible for deleting each member's own x402_social_memberships row (keyed by wallet, not group_id) via `delete_membership`."""
        ...

    def try_claim_open_case_for_target(self, *, target_id: str, case_id: str) -> bool:
        """Claim the one-open-case-per-target guard for `target_id` IFF unclaimed (LWT). Returns True iff this call won it -- design doc section 5.3 step 1."""
        ...

    def get_open_case_id_for_target(self, target_id: str) -> str | None:
        """The currently-open case_id for `target_id`, or None -- used to report it in a caller-fault 409 when `try_claim_open_case_for_target` loses."""
        ...

    def insert_case(self, item: StoredCase) -> None:
        """Store a newly-opened case: the canonical row and its GET /cases feed row. Canonical first (store before mark)."""
        ...

    def get_case(self, case_id: str) -> StoredCase | None:
        """Return the canonical case for an id, or None if there is none."""
        ...

    def list_open_cases(self, *, limit: int) -> list[StoredCase]:
        """Return open (not-yet-resolved) cases newest-first, at most `limit` of them -- the free GET /cases feed. A returned case may be past its own window and awaiting lazy resolution on its next touch; the caller runs `_resolve_if_due`, not this method."""
        ...

    def resolve_case(self, item: StoredCase) -> bool:
        """Apply `item`'s already-computed verdict to the canonical case row IFF it is still 'open' -- this conditional update IS the design doc section 5.3 "resolver slot" LWT. Returns True iff THIS call won the resolution (and must go on to apply its consequences); False means another caller already has, or is about to -- apply nothing further."""
        ...

    def try_add_case_vote(
        self, *, case_id: str, voter: str, verdict: str, settlement_tx_id: str, voted_at_epoch: int
    ) -> bool:
        """Insert the (case_id, voter) vote row IFF absent (LWT). Returns True iff this call won it -- same reaction-log discipline as `try_add_reaction`."""
        ...

    def increment_case_vote_total(self, case_id: str, *, verdict: str) -> None:
        """Add one to a case's uphold or reject counter, atomically. Called EXACTLY ONCE, only after `try_add_case_vote` returned True for this call."""
        ...

    def get_case_vote_totals(self, case_id: str) -> CaseTally:
        """Return a case's current uphold/reject totals, (0, 0) if never voted on."""
        ...

    def list_case_votes(self, case_id: str, *, limit: int) -> list[tuple[str, str]]:
        """Return (voter, verdict) pairs for one case, at most `limit` of them -- the resolver's own karma bookkeeping input."""
        ...

    def get_standing(self, wallet: str) -> StoredStanding | None:
        """Return one wallet's full moderation standing row, or None if it has never been touched."""
        ...

    def upsert_standing(self, item: StoredStanding) -> None:
        """Full-row overwrite of one wallet's standing (CLAUDE.md section 3: never a partial UPDATE)."""
        ...

    def get_reporter_open_case_ids(self, reporter: str) -> frozenset[str]:
        """The set of case ids currently claimed against `reporter`'s open-report concurrency cap, empty if the reporter has never filed a report."""
        ...

    def try_claim_reporter_slot(self, *, reporter: str, case_id: str, max_open: int) -> bool:
        """CAS-claim one of `reporter`'s open-report slots for `case_id` (design doc section 5.4.1). Returns True iff this call won a slot; False if the reporter is already at `max_open`, including a lost-race-under-contention refusal."""
        ...

    def release_reporter_slot(self, *, reporter: str, case_id: str) -> None:
        """Best-effort CAS-remove of one of `reporter`'s open-report slots -- idempotent, so a repeated release cannot double-decrement."""
        ...

    def insert_removal(self, item: RemovalRecord) -> None:
        """Append-only hard-delete audit record (design doc sections 5.4.2/8.1) -- never mutated after insert."""
        ...

    def get_removal(self, case_id: str) -> RemovalRecord | None:
        """Point read of one hard-delete audit record, or None. Not part of the design doc's public endpoint table -- exists for auditability (test verification, a future admin read)."""
        ...
