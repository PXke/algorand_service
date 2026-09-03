"""In-memory x402 social storage for dev and tests: Phase S0 agent profiles, Phase S1 posts/comments/reactions/follows/groups."""

from __future__ import annotations

import threading
from dataclasses import replace

from app.modules.x402_social.models.domain import (
    CASE_STATE_OPEN,
    VERDICT_UPHOLD,
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


def _recency_projection(item: AgentProfile) -> AgentProfile:
    """The thin (wallet/name/mission/emoji/created_at) shape the real by-recency table carries -- see AgentProfile's own docstring."""
    return AgentProfile(
        wallet=item.wallet,
        name=item.name,
        mission=item.mission,
        emoji=item.emoji,
        created_at_epoch=item.created_at_epoch,
    )


class InMemorySocialStore:
    """In-memory x402 social storage.

    Keeps an explicit, deliberately-thinned `_recency` projection (rather
    than sorting/slicing `_agents` on read) so this store mirrors the
    Cassandra store's real two-table shape -- see x402_directory's
    InMemoryListingStore for the same precedent -- and a test exercising
    list_recent_agents sees the same bio/location/interests-dropped
    behaviour production does.
    """

    def __init__(self) -> None:
        """Start with empty S0 (agents) and S1 (posts/comments/reactions/follows/groups) tables."""
        self._agents: dict[str, AgentProfile] = {}
        self._recency: dict[str, AgentProfile] = {}

        # Phase S1.
        self._posts: dict[str, StoredPost] = {}
        self._posts_by_author: dict[str, list[StoredPost]] = {}
        self._group_feed: dict[str, list[StoredPost]] = {}
        self._comments: dict[str, list[StoredComment]] = {}
        self._reaction_log: dict[tuple[str, str], tuple[int, str, int]] = {}
        self._reaction_totals: dict[str, ReactionTotals] = {}
        self._follows: dict[str, dict[str, int]] = {}  # follower -> {followee: created_at}
        self._followers: dict[str, dict[str, int]] = {}  # followee -> {follower: created_at}
        self._groups: dict[str, StoredGroup] = {}
        self._group_names: dict[str, str] = {}  # name_norm -> group_id
        self._groups_recency: dict[str, StoredGroup] = {}
        self._memberships: dict[tuple[str, str], StoredMembership] = {}  # (group_id, wallet)

        # Phase S2 (design doc section 5).
        self._cases: dict[str, StoredCase] = {}
        self._open_case_by_target: dict[str, str] = {}  # target_id -> case_id
        self._case_votes: dict[
            str, dict[str, tuple[str, str, int]]
        ] = {}  # case_id -> voter -> (verdict, tx, at)
        self._case_vote_totals: dict[str, CaseTally] = {}
        self._standing: dict[str, StoredStanding] = {}
        self._reporter_slots: dict[str, set[str]] = {}  # reporter -> {case_id, ...}
        self._removals: dict[str, RemovalRecord] = {}  # case_id -> record

        # Guards every read-modify-write below (reaction/follow/membership
        # dict mutation is not atomic under CPython's bytecode the same way
        # a bare `+=` on a counter is not -- see InMemoryFeatureStore's own
        # lock for the identical reasoning).
        self._lock = threading.Lock()

    def insert_agent_if_absent(self, item: AgentProfile) -> bool:
        """Store the profile only if its wallet is not yet present. True if it was stored."""
        if item.wallet in self._agents:
            return False
        self._put(item)
        return True

    def upsert_agent(self, item: AgentProfile) -> None:
        """Create or replace the profile for one wallet, recency projection included."""
        self._put(item)

    def _put(self, item: AgentProfile) -> None:
        self._agents[item.wallet] = replace(item)
        self._recency[item.wallet] = _recency_projection(item)

    def get_agent(self, wallet: str) -> AgentProfile | None:
        """Return the full stored profile for a wallet, or None if not registered."""
        found = self._agents.get(wallet)
        return None if found is None else replace(found)

    def list_recent_agents(self, *, limit: int) -> list[AgentProfile]:
        """Return registered agents newest-first (thin projection), at most `limit` of them.

        Ties on created_at break by wallet ascending, matching the
        Cassandra projection's (created_at DESC, wallet ASC) clustering
        order so tests see the same ordering as production.
        """
        ordered = sorted(self._recency.values(), key=lambda a: (-a.created_at_epoch, a.wallet))
        return [replace(a) for a in ordered[: max(0, limit)]]

    # ----------------------------------------------------------------- #
    # Phase S1: posts and comments
    # ----------------------------------------------------------------- #
    def insert_post(self, item: StoredPost) -> None:
        """Store one new post: canonical row, author feed, and (if set) group feed."""
        with self._lock:
            self._posts[item.post_id] = replace(item)
            self._posts_by_author.setdefault(item.author, []).insert(0, replace(item))
            if item.group_id:
                self._group_feed.setdefault(item.group_id, []).insert(0, replace(item))

    def get_post(self, post_id: str) -> StoredPost | None:
        """Return the canonical post for an id, or None if there is none."""
        with self._lock:
            found = self._posts.get(post_id)
            return None if found is None else replace(found)

    def list_posts_by_author(self, author: str, *, limit: int) -> list[StoredPost]:
        """Return one author's own posts newest-first, at most `limit` of them."""
        with self._lock:
            ordered = sorted(
                self._posts_by_author.get(author, []),
                key=lambda p: (-p.created_at_epoch, p.post_id),
            )
            return [replace(p) for p in ordered[: max(0, limit)]]

    def list_group_feed(self, group_id: str, *, limit: int) -> list[StoredPost]:
        """Return one group's feed newest-first, at most `limit` of them."""
        with self._lock:
            ordered = sorted(
                self._group_feed.get(group_id, []), key=lambda p: (-p.created_at_epoch, p.post_id)
            )
            return [replace(p) for p in ordered[: max(0, limit)]]

    def list_posts_by_authors(self, authors: list[str], *, limit: int) -> list[StoredPost]:
        """Sequential loop -- an in-process dict lookup has no round-trip cost to batch away, unlike the Cassandra store's version."""
        collected: list[StoredPost] = []
        for author in authors:
            collected.extend(self.list_posts_by_author(author, limit=limit))
        return collected

    def list_group_feeds(self, group_ids: list[str], *, limit: int) -> list[StoredPost]:
        """Sequential loop -- see list_posts_by_authors's identical rationale."""
        collected: list[StoredPost] = []
        for group_id in group_ids:
            collected.extend(self.list_group_feed(group_id, limit=limit))
        return collected

    def mark_post_deleted(self, item: StoredPost) -> None:
        """Set deleted=True on the canonical row, the author feed row, and (if set) the group feed row for this post.

        Mirrors the Cassandra store's group feed fix (finding 1,
        2026-security-audit): a deleted group post must stop serving its
        body via the group feed projection too, not just the canonical row
        and the author's own feed.
        """
        with self._lock:
            canonical = self._posts.get(item.post_id)
            if canonical is not None:
                canonical.deleted = True
            for row in self._posts_by_author.get(item.author, []):
                if row.post_id == item.post_id:
                    row.deleted = True
            if item.group_id:
                for row in self._group_feed.get(item.group_id, []):
                    if row.post_id == item.post_id:
                        row.deleted = True

    def mark_post_hidden_in_group(self, item: StoredPost) -> None:
        """Set hidden_group=True on the canonical row and the group feed row ONLY."""
        with self._lock:
            canonical = self._posts.get(item.post_id)
            if canonical is not None:
                canonical.hidden_group = True
            for row in self._group_feed.get(item.group_id, []):
                if row.post_id == item.post_id:
                    row.hidden_group = True

    def mark_post_hidden_platform(self, item: StoredPost) -> None:
        """Set hidden_platform=True EVERYWHERE this post is projected: canonical row, author feed row, and (if set) the group feed row (Phase S2). Unlike mark_post_hidden_in_group, the author feed row IS included."""
        with self._lock:
            canonical = self._posts.get(item.post_id)
            if canonical is not None:
                canonical.hidden_platform = True
            for row in self._posts_by_author.get(item.author, []):
                if row.post_id == item.post_id:
                    row.hidden_platform = True
            if item.group_id:
                for row in self._group_feed.get(item.group_id, []):
                    if row.post_id == item.post_id:
                        row.hidden_platform = True

    def hard_delete_post(self, item: StoredPost) -> None:
        """Real row removal: canonical row, author feed row, (if set) group feed row, and the post's whole comment list (Phase S2, design doc section 5.4.2). Idempotent: removing an absent entry is a no-op."""
        with self._lock:
            self._posts.pop(item.post_id, None)
            self._posts_by_author[item.author] = [
                row
                for row in self._posts_by_author.get(item.author, [])
                if row.post_id != item.post_id
            ]
            if item.group_id:
                self._group_feed[item.group_id] = [
                    row
                    for row in self._group_feed.get(item.group_id, [])
                    if row.post_id != item.post_id
                ]
            self._comments.pop(item.post_id, None)

    def insert_comment(self, item: StoredComment) -> None:
        """Append one comment to a post's thread."""
        with self._lock:
            self._comments.setdefault(item.post_id, []).append(replace(item))

    def list_comments(self, post_id: str, *, limit: int) -> list[StoredComment]:
        """Return one post's comments oldest-first, at most `limit` of them."""
        with self._lock:
            ordered = sorted(
                self._comments.get(post_id, []),
                key=lambda c: (c.created_at_epoch, c.comment_id),
            )
            return [replace(c) for c in ordered[: max(0, limit)]]

    def count_comments(self, post_id: str, *, limit: int) -> int:
        """In-process dict -- no separate narrow-projection path needed, unlike the Cassandra store's version."""
        with self._lock:
            return min(len(self._comments.get(post_id, [])), max(0, limit))

    # ----------------------------------------------------------------- #
    # Phase S1: reactions
    # ----------------------------------------------------------------- #
    def try_add_reaction(
        self, *, post_id: str, wallet: str, value: int, settlement_tx_id: str, created_at_epoch: int
    ) -> bool:
        """Insert the (post_id, wallet) reaction log row IFF absent. Returns True iff this call won it."""
        key = (post_id, wallet)
        with self._lock:
            if key in self._reaction_log:
                return False
            self._reaction_log[key] = (value, settlement_tx_id, created_at_epoch)
            return True

    def increment_reaction_total(self, post_id: str, *, value: int) -> None:
        """Add one to a post's up (value > 0) or down (value < 0) counter, atomically."""
        with self._lock:
            current = self._reaction_totals.get(post_id, ReactionTotals())
            if value > 0:
                self._reaction_totals[post_id] = replace(current, up=current.up + 1)
            else:
                self._reaction_totals[post_id] = replace(current, down=current.down + 1)

    def get_reaction_totals(self, post_id: str) -> ReactionTotals:
        """Return a post's current up/down totals, (0, 0) if never reacted to."""
        with self._lock:
            return self._reaction_totals.get(post_id, ReactionTotals())

    # ----------------------------------------------------------------- #
    # Phase S1: social graph
    # ----------------------------------------------------------------- #
    def upsert_follow(self, *, follower: str, followee: str, created_at_epoch: int) -> None:
        """Write both directions of a follow edge, idempotent."""
        with self._lock:
            self._follows.setdefault(follower, {})[followee] = created_at_epoch
            self._followers.setdefault(followee, {})[follower] = created_at_epoch

    def delete_follow(self, *, follower: str, followee: str) -> None:
        """Remove both directions of a follow edge. A no-op if it did not exist."""
        with self._lock:
            self._follows.get(follower, {}).pop(followee, None)
            self._followers.get(followee, {}).pop(follower, None)

    def list_following(self, wallet: str, *, limit: int) -> list[FollowEdge]:
        """Return the wallets `wallet` follows, most-recently-followed first."""
        with self._lock:
            items = list(self._follows.get(wallet, {}).items())
        ordered = sorted(items, key=lambda kv: (-kv[1], kv[0]))
        return [FollowEdge(wallet=w, created_at_epoch=t) for w, t in ordered[: max(0, limit)]]

    def list_followers(self, wallet: str, *, limit: int) -> list[FollowEdge]:
        """Return the wallets that follow `wallet`, most-recently-followed first."""
        with self._lock:
            items = list(self._followers.get(wallet, {}).items())
        ordered = sorted(items, key=lambda kv: (-kv[1], kv[0]))
        return [FollowEdge(wallet=w, created_at_epoch=t) for w, t in ordered[: max(0, limit)]]

    # ----------------------------------------------------------------- #
    # Phase S1: groups
    # ----------------------------------------------------------------- #
    def try_claim_group_name(self, *, name_norm: str, group_id: str) -> bool:
        """Claim a normalized group name for `group_id` IFF unclaimed. Returns True iff this call won it."""
        with self._lock:
            if name_norm in self._group_names:
                return False
            self._group_names[name_norm] = group_id
            return True

    def release_group_name(self, *, name_norm: str, group_id: str) -> None:
        """Best-effort compensating release of a name claim THIS group_id won (finding 3, 2026-security-audit). See base.SocialStore.release_group_name's own docstring."""
        with self._lock:
            if self._group_names.get(name_norm) == group_id:
                del self._group_names[name_norm]

    def insert_group(self, item: StoredGroup) -> None:
        """Store a newly-claimed group: canonical row and recency projection."""
        with self._lock:
            self._groups[item.group_id] = replace(item)
            self._groups_recency[item.group_id] = replace(item)

    def get_group(self, group_id: str) -> StoredGroup | None:
        """Return the canonical group for an id, or None if there is none."""
        with self._lock:
            found = self._groups.get(group_id)
            return None if found is None else replace(found)

    def list_groups_recent(self, *, limit: int) -> list[StoredGroup]:
        """Return groups newest-first, at most `limit` of them."""
        with self._lock:
            ordered = sorted(
                self._groups_recency.values(), key=lambda g: (-g.created_at_epoch, g.group_id)
            )
            return [replace(g) for g in ordered[: max(0, limit)]]

    def mark_group_hidden_platform(self, item: StoredGroup) -> None:
        """Set hidden_platform=True on the canonical row and the recency projection row (Phase S2)."""
        with self._lock:
            canonical = self._groups.get(item.group_id)
            if canonical is not None:
                canonical.hidden_platform = True
            recency = self._groups_recency.get(item.group_id)
            if recency is not None:
                recency.hidden_platform = True

    def hard_delete_group_shell(self, item: StoredGroup, *, name_norm: str) -> None:
        """Real row removal of the group's own rows only: canonical row, recency projection, and the name claim (freed). Idempotent."""
        with self._lock:
            self._groups.pop(item.group_id, None)
            self._groups_recency.pop(item.group_id, None)
            if self._group_names.get(name_norm) == item.group_id:
                del self._group_names[name_norm]

    def list_group_member_wallets(self, group_id: str, *, limit: int) -> list[str]:
        """Bounded read of a group's member wallets, for the group hard-delete walk."""
        with self._lock:
            wallets = [w for (gid, w) in self._memberships if gid == group_id]
            return wallets[: max(0, limit)]

    def delete_group_memberships_partition(self, group_id: str) -> None:
        """Remove every membership row for `group_id` (both directions -- the in-memory store has no separate per-group partition to bulk-delete, unlike Cassandra's x402_social_group_members, so this also removes the wallet-keyed x402_social_memberships-equivalent rows directly)."""
        with self._lock:
            for key in [k for k in self._memberships if k[0] == group_id]:
                del self._memberships[key]

    def upsert_membership(self, item: StoredMembership) -> None:
        """Create or replace one wallet's membership in one group."""
        with self._lock:
            self._memberships[(item.group_id, item.wallet)] = item

    def get_membership(self, group_id: str, wallet: str) -> StoredMembership | None:
        """Return one wallet's membership in one group, or None if not a member."""
        with self._lock:
            return self._memberships.get((group_id, wallet))

    def list_memberships(self, wallet: str, *, limit: int) -> list[StoredMembership]:
        """Return the groups `wallet` has joined, at most `limit` of them."""
        with self._lock:
            items = [m for (_gid, w), m in self._memberships.items() if w == wallet]
            return items[: max(0, limit)]

    def delete_membership(self, group_id: str, wallet: str) -> None:
        """Remove one wallet's membership in one group. A no-op if it did not exist."""
        with self._lock:
            self._memberships.pop((group_id, wallet), None)

    def set_membership_role(self, group_id: str, wallet: str, *, role: str) -> None:
        """Change an existing member's role in place. A no-op if the membership does not exist."""
        with self._lock:
            existing = self._memberships.get((group_id, wallet))
            if existing is not None:
                self._memberships[(group_id, wallet)] = replace(existing, role=role)

    # ----------------------------------------------------------------- #
    # Phase S2: community moderation (design doc section 5) and the section
    # 8.1 admin lever, which shares this exact hard-delete/removal-audit
    # machinery.
    # ----------------------------------------------------------------- #
    def try_claim_open_case_for_target(self, *, target_id: str, case_id: str) -> bool:
        """Claim the one-open-case-per-target guard for `target_id` IFF unclaimed. Returns True iff this call won it."""
        with self._lock:
            if target_id in self._open_case_by_target:
                return False
            self._open_case_by_target[target_id] = case_id
            return True

    def get_open_case_id_for_target(self, target_id: str) -> str | None:
        """The currently-open case_id for `target_id`, or None."""
        with self._lock:
            return self._open_case_by_target.get(target_id)

    def insert_case(self, item: StoredCase) -> None:
        """Store a newly-opened case's canonical row (the in-memory store has no separate GET /cases feed row -- list_open_cases reads self._cases directly)."""
        with self._lock:
            self._cases[item.case_id] = replace(item)

    def get_case(self, case_id: str) -> StoredCase | None:
        """Return the canonical case for an id, or None if there is none."""
        with self._lock:
            found = self._cases.get(case_id)
            return None if found is None else replace(found)

    def list_open_cases(self, *, limit: int) -> list[StoredCase]:
        """Return cases whose state is still 'open' (not-yet-resolved), newest-first, at most `limit` of them -- mirrors the Cassandra store's feed-row-deleted-on-resolution behavior by filtering on state here instead."""
        with self._lock:
            open_cases = [c for c in self._cases.values() if c.state == CASE_STATE_OPEN]
            ordered = sorted(open_cases, key=lambda c: (-c.opened_at_epoch, c.case_id))
            return [replace(c) for c in ordered[: max(0, limit)]]

    def resolve_case(self, item: StoredCase) -> bool:
        """Apply `item`'s verdict to the canonical case row IFF it is still 'open' -- this check-then-set IS the in-memory equivalent of the Cassandra store's conditional-update "resolver slot" (guarded by the store-wide lock, so it is exactly as exclusive as the real LWT). Returns True iff THIS call won the resolution; also releases the one-open-case-per-target guard on a win."""
        with self._lock:
            current = self._cases.get(item.case_id)
            if current is None or current.state != CASE_STATE_OPEN:
                return False
            self._cases[item.case_id] = replace(item)
            if self._open_case_by_target.get(item.target_id) == item.case_id:
                del self._open_case_by_target[item.target_id]
            return True

    def try_add_case_vote(
        self, *, case_id: str, voter: str, verdict: str, settlement_tx_id: str, voted_at_epoch: int
    ) -> bool:
        """Insert the (case_id, voter) vote row IFF absent. Returns True iff this call won it."""
        with self._lock:
            voters = self._case_votes.setdefault(case_id, {})
            if voter in voters:
                return False
            voters[voter] = (verdict, settlement_tx_id, voted_at_epoch)
            return True

    def increment_case_vote_total(self, case_id: str, *, verdict: str) -> None:
        """Add one to a case's uphold or reject counter, atomically."""
        with self._lock:
            current = self._case_vote_totals.get(case_id, CaseTally())
            if verdict == VERDICT_UPHOLD:
                self._case_vote_totals[case_id] = replace(current, uphold=current.uphold + 1)
            else:
                self._case_vote_totals[case_id] = replace(current, reject=current.reject + 1)

    def get_case_vote_totals(self, case_id: str) -> CaseTally:
        """Return a case's current uphold/reject totals, (0, 0) if never voted on."""
        with self._lock:
            return self._case_vote_totals.get(case_id, CaseTally())

    def list_case_votes(self, case_id: str, *, limit: int) -> list[tuple[str, str]]:
        """Return (voter, verdict) pairs for one case, at most `limit` of them."""
        with self._lock:
            items = list(self._case_votes.get(case_id, {}).items())
            return [(voter, verdict) for voter, (verdict, _tx, _at) in items[: max(0, limit)]]

    def get_standing(self, wallet: str) -> StoredStanding | None:
        """Return one wallet's full moderation standing row, or None if it has never been touched."""
        with self._lock:
            found = self._standing.get(wallet)
            return None if found is None else replace(found, offenses=list(found.offenses))

    def upsert_standing(self, item: StoredStanding) -> None:
        """Full-row overwrite of one wallet's standing."""
        with self._lock:
            self._standing[item.wallet] = replace(item, offenses=list(item.offenses))

    def get_reporter_open_case_ids(self, reporter: str) -> frozenset[str]:
        """The set of case ids currently claimed against `reporter`'s open-report concurrency cap, empty if never filed."""
        with self._lock:
            return frozenset(self._reporter_slots.get(reporter, set()))

    def try_claim_reporter_slot(self, *, reporter: str, case_id: str, max_open: int) -> bool:
        """Claim one of `reporter`'s open-report slots for `case_id`. Returns True iff this call won a slot; False if the reporter is already at `max_open`."""
        with self._lock:
            current = self._reporter_slots.setdefault(reporter, set())
            if case_id in current:
                return True
            if len(current) >= max_open:
                return False
            current.add(case_id)
            return True

    def release_reporter_slot(self, *, reporter: str, case_id: str) -> None:
        """Remove one of `reporter`'s open-report slots. Idempotent -- a no-op if the slot was already released."""
        with self._lock:
            self._reporter_slots.get(reporter, set()).discard(case_id)

    def insert_removal(self, item: RemovalRecord) -> None:
        """Append-only hard-delete audit record -- never mutated after insert."""
        with self._lock:
            self._removals[item.case_id] = item

    def get_removal(self, case_id: str) -> RemovalRecord | None:
        """Point read of one hard-delete audit record, or None."""
        with self._lock:
            return self._removals.get(case_id)
