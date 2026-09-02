"""In-memory x402 social storage for dev and tests: Phase S0 agent profiles, Phase S1 posts/comments/reactions/follows/groups."""

from __future__ import annotations

import threading
from dataclasses import replace

from app.modules.x402_social.models.domain import (
    AgentProfile,
    FollowEdge,
    ReactionTotals,
    StoredComment,
    StoredGroup,
    StoredMembership,
    StoredPost,
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
