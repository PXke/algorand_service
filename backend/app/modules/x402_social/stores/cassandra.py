"""Cassandra-backed x402 social storage: Phase S0 agent profiles, Phase S1 posts/comments/reactions/follows/groups."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.cassandra import get_cassandra_session
from app.core.statements import X402SocialStmts
from app.modules.x402_social.models.domain import (
    AGENTS_PARTITION,
    GRAPH_SCAN_LIMIT,
    GROUPS_PARTITION,
    AgentProfile,
    FollowEdge,
    ReactionTotals,
    StoredComment,
    StoredGroup,
    StoredMembership,
    StoredPost,
)


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    return int(value.timestamp()) if value else 0


def _uuid(value: str) -> uuid.UUID:
    """Parse a domain-layer id string into the UUID object the driver's timeuuid codec needs to bind.

    post_id/comment_id are generated as `str(uuid.uuid1())` in
    services/post_service.py (a real version-1 UUID -- Cassandra validates
    the version nibble server-side on a write to a `timeuuid` column, so a
    version-4 id would be rejected there, not just here) and carried as
    plain strings everywhere above the store layer (the same "ids are text"
    convention every other id in this module follows, so the memory store
    needs no parallel type).
    """
    return uuid.UUID(value)


def _row_to_agent(row: object) -> AgentProfile:
    return AgentProfile(
        wallet=row.wallet,
        name=row.name or "",
        bio=row.bio or "",
        mission=row.mission or "",
        location=row.location or "",
        interests=list(row.interests or []),
        emoji=row.emoji or "",
        created_at_epoch=_epoch(row.created_at),
        updated_at_epoch=_epoch(row.updated_at),
        settlement_tx_id=row.settlement_tx_id or "",
    )


def _row_to_agent_from_recency(row: object) -> AgentProfile:
    """Thin projection row -> AgentProfile with bio/location/interests/updated_at/settlement_tx_id at their dataclass defaults -- see AgentProfile's own docstring."""
    return AgentProfile(
        wallet=row.wallet,
        name=row.name or "",
        mission=row.mission or "",
        emoji=row.emoji or "",
        created_at_epoch=_epoch(row.created_at),
    )


def _row_to_post(row: object) -> StoredPost:
    return StoredPost(
        post_id=str(row.post_id),
        author=row.author or "",
        group_id=row.group_id or "",
        body_md=row.body_md or "",
        tags=list(row.tags or []),
        created_at_epoch=_epoch(row.created_at),
        settlement_tx_id=row.settlement_tx_id or "",
        deleted=bool(row.deleted),
        hidden_group=bool(row.hidden_group),
    )


def _row_to_post_from_author(row: object) -> StoredPost:
    """x402_social_posts_by_author has no hidden_group column -- see the migration's own comment; it reads back at the dataclass default (False)."""
    return StoredPost(
        post_id=str(row.post_id),
        author=row.author or "",
        group_id=row.group_id or "",
        body_md=row.body_md or "",
        tags=list(row.tags or []),
        created_at_epoch=_epoch(row.created_at),
        deleted=bool(row.deleted),
    )


def _row_to_post_from_group_feed(row: object) -> StoredPost:
    return StoredPost(
        post_id=str(row.post_id),
        author=row.author or "",
        group_id=row.group_id or "",
        body_md=row.body_md or "",
        tags=list(row.tags or []),
        created_at_epoch=_epoch(row.created_at),
        deleted=bool(row.deleted),
        hidden_group=bool(row.hidden_group),
    )


def _row_to_comment(row: object) -> StoredComment:
    return StoredComment(
        post_id=str(row.post_id),
        comment_id=str(row.comment_id),
        author=row.author or "",
        body_md=row.body_md or "",
        created_at_epoch=_epoch(row.created_at),
        settlement_tx_id=row.settlement_tx_id or "",
        deleted=bool(row.deleted),
    )


def _row_to_group(row: object) -> StoredGroup:
    return StoredGroup(
        group_id=row.group_id,
        name=row.name or "",
        description=row.description or "",
        owner=row.owner or "",
        created_at_epoch=_epoch(row.created_at),
        settlement_tx_id=row.settlement_tx_id or "",
    )


def _row_to_group_from_recency(row: object) -> StoredGroup:
    return StoredGroup(
        group_id=row.group_id,
        name=row.name or "",
        description=row.description or "",
        owner=row.owner or "",
        created_at_epoch=_epoch(row.created_at),
    )


def _row_to_membership(row: object) -> StoredMembership:
    return StoredMembership(
        group_id=row.group_id,
        wallet=row.wallet,
        role=row.role or "",
        joined_at_epoch=_epoch(row.joined_at),
        settlement_tx_id=row.settlement_tx_id or "",
    )


def _agent_params(item: AgentProfile) -> tuple:
    """Bind params shared by UPSERT_AGENT and INSERT_AGENT_IF_ABSENT."""
    return (
        item.wallet,
        item.name,
        item.bio,
        item.mission,
        item.location,
        list(item.interests),
        item.emoji,
        _dt(item.created_at_epoch),
        _dt(item.updated_at_epoch),
        item.settlement_tx_id,
    )


def _recency_params(item: AgentProfile) -> tuple:
    """Bind params for INSERT_RECENCY.

    Always the profile's OWN created_at_epoch, which a profile edit never
    changes (see AgentProfile's docstring and the migration's own comment),
    so this INSERT is always an in-place overwrite of the same recency row
    -- no delete-then-insert dance is ever needed here, unlike the
    directory's tag/category projections.
    """
    return (
        AGENTS_PARTITION,
        _dt(item.created_at_epoch),
        item.wallet,
        item.name,
        item.mission,
        item.emoji,
    )


class CassandraSocialStore:
    """Cassandra-backed x402 social agent-profile storage.

    Two tables: the canonical x402_social_agents row and the newest-first
    recency projection (105) for the free agent directory. Every write path
    goes canonical row first, projection second (store before mark): if the
    projection write then fails, the profile exists and is missing only from
    the browse feed, which the next PATCH /profile repairs. The reverse order
    could leave a feed entry pointing at a profile that was never durably
    stored.
    """

    def insert_agent_if_absent(self, item: AgentProfile) -> bool:
        """Store the profile only if no x402_social_agents row exists for its wallet.

        A lightweight transaction (INSERT ... IF NOT EXISTS), so two
        concurrent first-time registrations of the same wallet cannot both
        succeed: exactly one INSERT is applied and the other returns False
        having written nothing, including no recency row.
        """
        session = get_cassandra_session()
        result = session.execute(X402SocialStmts.INSERT_AGENT_IF_ABSENT, _agent_params(item))
        if not result.was_applied:
            return False
        session.execute(X402SocialStmts.INSERT_RECENCY, _recency_params(item))
        return True

    def upsert_agent(self, item: AgentProfile) -> None:
        """Create or replace the profile for one wallet, recency projection included.

        No previous-row lookup is needed here (unlike x402_directory's
        upsert): the recency projection's key never moves after
        registration, so this is always a same-key overwrite in place on
        both tables -- see _recency_params's own docstring.
        """
        session = get_cassandra_session()
        session.execute(X402SocialStmts.UPSERT_AGENT, _agent_params(item))
        session.execute(X402SocialStmts.INSERT_RECENCY, _recency_params(item))

    def get_agent(self, wallet: str) -> AgentProfile | None:
        """Return the full stored profile for a wallet, or None if not registered."""
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_AGENT, (wallet,)).one()
        return None if row is None else _row_to_agent(row)

    def list_recent_agents(self, *, limit: int) -> list[AgentProfile]:
        """Return registered agents newest-first (thin projection), at most `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_RECENT_AGENTS, (AGENTS_PARTITION, limit))
        return [_row_to_agent_from_recency(row) for row in rows]

    # ----------------------------------------------------------------- #
    # Phase S1: posts and comments
    # ----------------------------------------------------------------- #
    def insert_post(self, item: StoredPost) -> None:
        """Store one new post: canonical row, author feed, and (if set) group feed. Canonical first (store before mark)."""
        session = get_cassandra_session()
        created = _dt(item.created_at_epoch)
        post_id = _uuid(item.post_id)
        session.execute(
            X402SocialStmts.INSERT_POST,
            (
                post_id,
                item.author,
                item.group_id,
                item.body_md,
                list(item.tags),
                created,
                item.settlement_tx_id,
                item.deleted,
                item.hidden_group,
            ),
        )
        session.execute(
            X402SocialStmts.INSERT_POST_BY_AUTHOR,
            (
                item.author,
                created,
                post_id,
                item.group_id,
                item.body_md,
                list(item.tags),
                item.deleted,
            ),
        )
        if item.group_id:
            session.execute(
                X402SocialStmts.INSERT_GROUP_FEED_POST,
                (
                    item.group_id,
                    created,
                    post_id,
                    item.author,
                    item.body_md,
                    list(item.tags),
                    item.deleted,
                    item.hidden_group,
                ),
            )

    def get_post(self, post_id: str) -> StoredPost | None:
        """Return the canonical post for an id, or None if there is none."""
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_POST, (_uuid(post_id),)).one()
        return None if row is None else _row_to_post(row)

    def list_posts_by_author(self, author: str, *, limit: int) -> list[StoredPost]:
        """Return one author's own posts newest-first, at most `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_POSTS_BY_AUTHOR, (author, limit))
        return [_row_to_post_from_author(row) for row in rows]

    def list_group_feed(self, group_id: str, *, limit: int) -> list[StoredPost]:
        """Return one group's feed newest-first, at most `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_GROUP_FEED, (group_id, limit))
        return [_row_to_post_from_group_feed(row) for row in rows]

    def mark_post_deleted(self, item: StoredPost) -> None:
        """Set deleted=true on the canonical row and the author feed row for this post, both via UPDATE ... IF EXISTS."""
        session = get_cassandra_session()
        post_id = _uuid(item.post_id)
        session.execute(X402SocialStmts.MARK_POST_DELETED, (post_id,))
        session.execute(
            X402SocialStmts.MARK_POST_BY_AUTHOR_DELETED,
            (item.author, _dt(item.created_at_epoch), post_id),
        )

    def mark_post_hidden_in_group(self, item: StoredPost) -> None:
        """Set hidden_group=true on the canonical row and the group feed row ONLY, both via UPDATE ... IF EXISTS."""
        session = get_cassandra_session()
        post_id = _uuid(item.post_id)
        session.execute(X402SocialStmts.MARK_POST_HIDDEN_GROUP, (post_id,))
        session.execute(
            X402SocialStmts.MARK_GROUP_FEED_POST_HIDDEN,
            (item.group_id, _dt(item.created_at_epoch), post_id),
        )

    def insert_comment(self, item: StoredComment) -> None:
        """Append one comment to a post's thread."""
        session = get_cassandra_session()
        session.execute(
            X402SocialStmts.INSERT_COMMENT,
            (
                _uuid(item.post_id),
                _dt(item.created_at_epoch),
                _uuid(item.comment_id),
                item.author,
                item.body_md,
                item.settlement_tx_id,
                item.deleted,
            ),
        )

    def list_comments(self, post_id: str, *, limit: int) -> list[StoredComment]:
        """Return one post's comments oldest-first, at most `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_COMMENTS, (_uuid(post_id), limit))
        return [_row_to_comment(row) for row in rows]

    # ----------------------------------------------------------------- #
    # Phase S1: reactions
    # ----------------------------------------------------------------- #
    def try_add_reaction(
        self, *, post_id: str, wallet: str, value: int, settlement_tx_id: str, created_at_epoch: int
    ) -> bool:
        """Insert the (post_id, wallet) reaction log row IFF absent (LWT). Returns True iff this call won it."""
        session = get_cassandra_session()
        result = session.execute(
            X402SocialStmts.INSERT_REACTION_IF_ABSENT,
            (_uuid(post_id), wallet, value, settlement_tx_id, _dt(created_at_epoch)),
        )
        return bool(result.was_applied)

    def increment_reaction_total(self, post_id: str, *, value: int) -> None:
        """Add one to a post's up (value > 0) or down (value < 0) counter, atomically, exactly once."""
        session = get_cassandra_session()
        stmt = (
            X402SocialStmts.INCREMENT_REACTION_UP
            if value > 0
            else X402SocialStmts.INCREMENT_REACTION_DOWN
        )
        session.execute(stmt, (_uuid(post_id),))

    def get_reaction_totals(self, post_id: str) -> ReactionTotals:
        """Return a post's current up/down totals, (0, 0) if never reacted to."""
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_REACTION_TOTALS, (_uuid(post_id),)).one()
        if row is None:
            return ReactionTotals()
        return ReactionTotals(up=int(row.up or 0), down=int(row.down or 0))

    # ----------------------------------------------------------------- #
    # Phase S1: social graph
    # ----------------------------------------------------------------- #
    def upsert_follow(self, *, follower: str, followee: str, created_at_epoch: int) -> None:
        """Write both directions of a follow edge, idempotent."""
        session = get_cassandra_session()
        created = _dt(created_at_epoch)
        session.execute(X402SocialStmts.INSERT_FOLLOW, (follower, followee, created))
        session.execute(X402SocialStmts.INSERT_FOLLOWER, (followee, follower, created))

    def delete_follow(self, *, follower: str, followee: str) -> None:
        """Remove both directions of a follow edge. A no-op if it did not exist."""
        session = get_cassandra_session()
        session.execute(X402SocialStmts.DELETE_FOLLOW, (follower, followee))
        session.execute(X402SocialStmts.DELETE_FOLLOWER, (followee, follower))

    def list_following(self, wallet: str, *, limit: int) -> list[FollowEdge]:
        """Return the wallets `wallet` follows, most-recently-followed first.

        Not clustered by recency (see the migration's own comment): a
        bounded partition scan (GRAPH_SCAN_LIMIT rows), sorted by
        created_at descending in Python, then sliced to `limit`.
        """
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_FOLLOWING, (wallet, GRAPH_SCAN_LIMIT))
        edges = [
            FollowEdge(wallet=row.followee, created_at_epoch=_epoch(row.created_at)) for row in rows
        ]
        edges.sort(key=lambda e: (-e.created_at_epoch, e.wallet))
        return edges[: max(0, limit)]

    def list_followers(self, wallet: str, *, limit: int) -> list[FollowEdge]:
        """Return the wallets that follow `wallet`, most-recently-followed first (bounded scan, sorted in Python)."""
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_FOLLOWERS, (wallet, GRAPH_SCAN_LIMIT))
        edges = [
            FollowEdge(wallet=row.follower, created_at_epoch=_epoch(row.created_at)) for row in rows
        ]
        edges.sort(key=lambda e: (-e.created_at_epoch, e.wallet))
        return edges[: max(0, limit)]

    # ----------------------------------------------------------------- #
    # Phase S1: groups
    # ----------------------------------------------------------------- #
    def try_claim_group_name(self, *, name_norm: str, group_id: str) -> bool:
        """Claim a normalized group name for `group_id` IFF unclaimed (LWT). Returns True iff this call won it."""
        session = get_cassandra_session()
        result = session.execute(X402SocialStmts.INSERT_GROUP_NAME_IF_ABSENT, (name_norm, group_id))
        return bool(result.was_applied)

    def insert_group(self, item: StoredGroup) -> None:
        """Store a newly-claimed group: canonical row and recency projection. Canonical first (store before mark)."""
        session = get_cassandra_session()
        created = _dt(item.created_at_epoch)
        session.execute(
            X402SocialStmts.INSERT_GROUP,
            (
                item.group_id,
                item.name,
                item.description,
                item.owner,
                created,
                item.settlement_tx_id,
            ),
        )
        session.execute(
            X402SocialStmts.INSERT_GROUP_RECENCY,
            (GROUPS_PARTITION, created, item.group_id, item.name, item.description, item.owner),
        )

    def get_group(self, group_id: str) -> StoredGroup | None:
        """Return the canonical group for an id, or None if there is none."""
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_GROUP, (group_id,)).one()
        return None if row is None else _row_to_group(row)

    def list_groups_recent(self, *, limit: int) -> list[StoredGroup]:
        """Return groups newest-first, at most `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_GROUPS_RECENT, (GROUPS_PARTITION, limit))
        return [_row_to_group_from_recency(row) for row in rows]

    def upsert_membership(self, item: StoredMembership) -> None:
        """Create or replace one wallet's membership in one group (both directions)."""
        session = get_cassandra_session()
        joined = _dt(item.joined_at_epoch)
        session.execute(
            X402SocialStmts.UPSERT_GROUP_MEMBER,
            (item.group_id, item.wallet, item.role, joined, item.settlement_tx_id),
        )
        session.execute(
            X402SocialStmts.UPSERT_MEMBERSHIP,
            (item.wallet, item.group_id, item.role, joined, item.settlement_tx_id),
        )

    def get_membership(self, group_id: str, wallet: str) -> StoredMembership | None:
        """Return one wallet's membership in one group, or None if not a member."""
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_GROUP_MEMBER, (group_id, wallet)).one()
        return None if row is None else _row_to_membership(row)

    def list_memberships(self, wallet: str, *, limit: int) -> list[StoredMembership]:
        """Return the groups `wallet` has joined, at most GRAPH_SCAN_LIMIT-bounded `limit` of them."""
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_MEMBERSHIPS, (wallet, limit))
        return [_row_to_membership(row) for row in rows]

    def delete_membership(self, group_id: str, wallet: str) -> None:
        """Remove one wallet's membership in one group (both directions). A no-op if it did not exist."""
        session = get_cassandra_session()
        session.execute(X402SocialStmts.DELETE_GROUP_MEMBER, (group_id, wallet))
        session.execute(X402SocialStmts.DELETE_MEMBERSHIP, (wallet, group_id))

    def set_membership_role(self, group_id: str, wallet: str, *, role: str) -> None:
        """Change an existing member's role in place (both directions), via UPDATE ... IF EXISTS. A no-op if the membership does not exist."""
        session = get_cassandra_session()
        session.execute(X402SocialStmts.UPDATE_GROUP_MEMBER_ROLE, (role, group_id, wallet))
        session.execute(X402SocialStmts.UPDATE_MEMBERSHIP_ROLE, (role, wallet, group_id))
