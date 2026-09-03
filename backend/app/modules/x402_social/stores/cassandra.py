"""Cassandra-backed x402 social storage: Phase S0 agent profiles, Phase S1 posts/comments/reactions/follows/groups."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from app.core.cassandra import execute_parallel_with_args, get_cassandra_session
from app.core.statements import X402SocialStmts
from app.modules.x402_social.models.domain import (
    AGENTS_PARTITION,
    CASES_PARTITION,
    GRAPH_SCAN_LIMIT,
    GROUPS_PARTITION,
    REPORTER_SLOT_CAS_RETRIES,
    STANDING_CAS_RETRIES,
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


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    """UTC epoch seconds from a stored timestamp.

    The Cassandra driver returns timezone-NAIVE datetimes that are already UTC wall-clock values;
    calling .timestamp() directly makes Python assume the server's LOCAL zone and silently shift
    the result (root-caused 2026-09-03 on this exact module: a report-rejection cooldown read
    back 2 hours earlier than it was written, matching this server's UTC+2 local zone -- same bug
    class already fixed once in news/stores/cassandra.py, whose own docstring notes it broke
    "Xh ago" displays on a non-UTC host; that fix never got propagated here).
    """
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


logger = logging.getLogger(__name__)


def _uuid(value: str) -> uuid.UUID:
    """Parse a domain-layer id string into the UUID object the driver's timeuuid codec needs to bind.

    post_id/comment_id are generated as `str(uuid.uuid1())` in
    services/post_service.py (a real version-1 UUID -- Cassandra validates
    the version nibble server-side on a write to a `timeuuid` column, so a
    version-4 id would be rejected there, not just here) and carried as
    plain strings everywhere above the store layer (the same "ids are text"
    convention every other id in this module follows, so the memory store
    needs no parallel type).

    Raises ValueError on a malformed id -- callers reading by external id
    (a post_id off a URL, never one this process itself minted) use
    `_try_uuid` instead so a malformed id becomes a clean "not found"
    rather than an unhandled 500 (finding 5, 2026-security-audit). This
    raising form stays the one write paths use (`insert_post`,
    `insert_comment`, ...), where the id was always minted by
    `post_service.create`/`add_comment` moments earlier and a ValueError
    here would mean OUR bug, not malformed external input.
    """
    return uuid.UUID(value)


def _try_uuid(value: str) -> uuid.UUID | None:
    """Like `_uuid`, but returns None instead of raising on a malformed id.

    Used by every READ path that takes a post/comment id straight from
    external input (a URL path param, ultimately) -- `get_post`,
    `list_comments`, `get_reaction_totals` -- so a request like
    `GET /posts/not-a-uuid` resolves to the same "not found" a well-formed
    but unknown id gets, instead of an unhandled 500 (finding 5,
    2026-security-audit: the in-memory store backing the test suite is a
    plain dict lookup and never hit this, since a malformed string just
    misses the dict -- only the Cassandra-backed production path could 500).
    """
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


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
        hidden_platform=bool(row.hidden_platform),
    )


def _row_to_post_from_author(row: object) -> StoredPost:
    """x402_social_posts_by_author has no hidden_group column -- see the migration's own comment; it reads back at the dataclass default (False). hidden_platform IS a real column here (migration 108) -- see that migration's own note on why it differs from hidden_group."""
    return StoredPost(
        post_id=str(row.post_id),
        author=row.author or "",
        group_id=row.group_id or "",
        body_md=row.body_md or "",
        tags=list(row.tags or []),
        created_at_epoch=_epoch(row.created_at),
        deleted=bool(row.deleted),
        hidden_platform=bool(row.hidden_platform),
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
        hidden_platform=bool(row.hidden_platform),
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
        hidden_platform=bool(row.hidden_platform),
    )


def _row_to_group_from_recency(row: object) -> StoredGroup:
    return StoredGroup(
        group_id=row.group_id,
        name=row.name or "",
        description=row.description or "",
        owner=row.owner or "",
        created_at_epoch=_epoch(row.created_at),
        hidden_platform=bool(row.hidden_platform),
    )


def _row_to_case(row: object) -> StoredCase:
    return StoredCase(
        case_id=str(row.case_id),
        target_type=row.target_type or "",
        target_id=row.target_id or "",
        target_wallet=row.target_wallet or "",
        category=row.category or "",
        note=row.note or "",
        reporter=row.reporter or "",
        settlement_tx_id=row.settlement_tx_id or "",
        content_snapshot=row.content_snapshot or "",
        opened_at_epoch=_epoch(row.opened_at),
        window_ends_at_epoch=_epoch(row.window_ends_at),
        state=row.state or "",
        resolved_at_epoch=_epoch(row.resolved_at),
        resolution_note=row.resolution_note or "",
    )


def _row_to_standing(row: object) -> StoredStanding:
    return StoredStanding(
        wallet=row.wallet,
        offense_count=int(row.offense_count or 0),
        last_offense_at_epoch=_epoch(row.last_offense_at),
        banned_until_epoch=_epoch(row.banned_until),
        offenses=[_epoch(ts) for ts in (row.offenses or [])],
        reported_count=int(row.reported_count or 0),
        rejected_report_count=int(row.rejected_report_count or 0),
        report_rejection_streak=int(row.report_rejection_streak or 0),
        report_cooldown_until_epoch=_epoch(row.report_cooldown_until),
        votes_cast=int(row.votes_cast or 0),
        votes_matched_resolution=int(row.votes_matched_resolution or 0),
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


def _standing_values(item: StoredStanding) -> tuple:
    """The 10 non-wallet column values for x402_social_standing, in column order -- shared by INSERT_STANDING_IF_ABSENT (via _standing_params), UPSERT_STANDING, and UPDATE_STANDING_IF_MATCH's SET and IF clauses (mutate_standing) so all three never drift out of sync on column order."""
    return (
        item.offense_count,
        _dt(item.last_offense_at_epoch) if item.last_offense_at_epoch else None,
        _dt(item.banned_until_epoch) if item.banned_until_epoch else None,
        [_dt(e) for e in item.offenses],
        item.reported_count,
        item.rejected_report_count,
        item.report_rejection_streak,
        _dt(item.report_cooldown_until_epoch) if item.report_cooldown_until_epoch else None,
        item.votes_cast,
        item.votes_matched_resolution,
    )


def _standing_params(item: StoredStanding) -> tuple:
    """Bind params for UPSERT_STANDING / INSERT_STANDING_IF_ABSENT: wallet, then _standing_values."""
    return (item.wallet, *_standing_values(item))


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
        try:
            session.execute(X402SocialStmts.INSERT_RECENCY, _recency_params(item))
        except Exception:
            # The profile LWT already won and is durably stored (finding 3,
            # 2026-security-audit) -- only the newest-first browse
            # projection failed to write. A full compensating rollback is
            # overkill for a denormalized read projection (the wallet IS
            # registered; a caller can always fetch it directly via
            # GET /agents/{wallet}, it is just missing from GET /agents
            # until the next PATCH /profile repairs this row -- see
            # UPSERT_AGENT's own docstring). Documented here, not silently
            # swallowed: this still propagates, so run_with_refund's
            # generic-exception path attempts a refund for a registration
            # that durably exists -- a known, accepted, low-severity drift,
            # not something this method tries to fix.
            logger.warning(
                "x402 social register: profile stored for wallet=%s but the recency "
                "projection write failed -- wallet is registered but will not appear "
                "in GET /agents until the next profile edit repairs this row",
                item.wallet,
                exc_info=True,
            )
            raise
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
    # Agent Discovery Search (added 2026-09-03) -- x402_social_agents_by_interest,
    # migration 110. Kept in sync with AgentProfile.interests by
    # services/profile_service.py.
    # ----------------------------------------------------------------- #
    def upsert_agent_interest(self, *, interest: str, wallet: str, created_at_epoch: int) -> None:
        """Add (or overwrite) one (interest, wallet) row to the interest lookup."""
        session = get_cassandra_session()
        session.execute(
            X402SocialStmts.UPSERT_AGENT_INTEREST, (interest, wallet, _dt(created_at_epoch))
        )

    def delete_agent_interest(self, *, interest: str, wallet: str) -> None:
        """Remove one (interest, wallet) row from the interest lookup. A no-op if it did not exist."""
        session = get_cassandra_session()
        session.execute(X402SocialStmts.DELETE_AGENT_INTEREST, (interest, wallet))

    def list_agents_by_interest(self, interest: str, *, limit: int) -> list[tuple[str, int]]:
        """Up to `limit` (wallet, created_at_epoch) pairs registered under one interest tag."""
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_AGENTS_BY_INTEREST, (interest, limit))
        return [(row.wallet, _epoch(row.created_at)) for row in rows]

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
                item.hidden_platform,
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
                item.hidden_platform,
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
                    item.hidden_platform,
                ),
            )

    def get_post(self, post_id: str) -> StoredPost | None:
        """Return the canonical post for an id, or None if there is none (including a malformed, non-UUID id -- finding 5, 2026-security-audit)."""
        parsed = _try_uuid(post_id)
        if parsed is None:
            return None
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_POST, (parsed,)).one()
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

    def list_posts_by_authors(self, authors: list[str], *, limit: int) -> list[StoredPost]:
        """Fan out one single-partition read per author CONCURRENTLY, not sequentially (optimization pass, 2026-09-02).

        home_feed's fan-out is bounded by x402_social_feed_fanout_limit
        (default 50), but a sequential loop still meant up to 50 fully
        blocking round-trips before GET /feed could start assembling a
        response -- the design doc's own acknowledged scaling risk for this
        route. execute_parallel_with_args (app/core/cassandra.py) is the
        same fan-out-then-collect helper app/modules/admin already uses for
        an identical per-item-partition-read shape. raise_on_error=False: one
        author's read failing must not fail the whole feed -- skipped, not
        raised, same as this store's other best-effort read paths.
        """
        if not authors:
            return []
        results = execute_parallel_with_args(
            X402SocialStmts.LIST_POSTS_BY_AUTHOR,
            [(author, limit) for author in authors],
            raise_on_error=False,
        )
        collected: list[StoredPost] = []
        for ok, rows_or_exc in results:
            if not ok:
                logger.warning(
                    "x402 social home_feed: a followee's post read failed: %s", rows_or_exc
                )
                continue
            collected.extend(_row_to_post_from_author(row) for row in rows_or_exc)
        return collected

    def list_group_feeds(self, group_ids: list[str], *, limit: int) -> list[StoredPost]:
        """Fan out one single-partition read per group CONCURRENTLY. See list_posts_by_authors's identical rationale."""
        if not group_ids:
            return []
        results = execute_parallel_with_args(
            X402SocialStmts.LIST_GROUP_FEED,
            [(group_id, limit) for group_id in group_ids],
            raise_on_error=False,
        )
        collected: list[StoredPost] = []
        for ok, rows_or_exc in results:
            if not ok:
                logger.warning(
                    "x402 social home_feed: a joined group's feed read failed: %s", rows_or_exc
                )
                continue
            collected.extend(_row_to_post_from_group_feed(row) for row in rows_or_exc)
        return collected

    def mark_post_deleted(self, item: StoredPost) -> None:
        """Set deleted=true on the canonical row, the author feed row, and (if set) the group feed row for this post, all via UPDATE ... IF EXISTS.

        The group feed row is included so a deleted group post actually
        stops serving its body via GET /groups/{id}/feed and the group half
        of GET /feed (finding 1, 2026-security-audit) -- previously only the
        canonical row and the author feed row were flipped, leaving the
        group feed projection permanently stale.
        """
        session = get_cassandra_session()
        post_id = _uuid(item.post_id)
        session.execute(X402SocialStmts.MARK_POST_DELETED, (post_id,))
        session.execute(
            X402SocialStmts.MARK_POST_BY_AUTHOR_DELETED,
            (item.author, _dt(item.created_at_epoch), post_id),
        )
        if item.group_id:
            session.execute(
                X402SocialStmts.MARK_GROUP_FEED_POST_DELETED,
                (item.group_id, _dt(item.created_at_epoch), post_id),
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

    def mark_post_hidden_platform(self, item: StoredPost) -> None:
        """Set hidden_platform=true EVERYWHERE this post is projected: the canonical row, the author feed row, and (if set) the group feed row -- all via UPDATE ... IF EXISTS (Phase S2, design doc section 5.3 step 4).

        Unlike mark_post_hidden_in_group, the author feed row IS included:
        an upheld case verdict tombstones a post everywhere, including the
        author's own feed, not just one group's.
        """
        session = get_cassandra_session()
        post_id = _uuid(item.post_id)
        created = _dt(item.created_at_epoch)
        session.execute(X402SocialStmts.MARK_POST_HIDDEN_PLATFORM, (post_id,))
        session.execute(
            X402SocialStmts.MARK_POST_BY_AUTHOR_HIDDEN_PLATFORM, (item.author, created, post_id)
        )
        if item.group_id:
            session.execute(
                X402SocialStmts.MARK_GROUP_FEED_POST_HIDDEN_PLATFORM,
                (item.group_id, created, post_id),
            )

    def hard_delete_post(self, item: StoredPost) -> None:
        """Real row removal: the canonical row, the author feed row, (if set) the group feed row, and the post's whole comment partition (Phase S2, design doc section 5.4.2 -- the one category-scoped, illegal_content-only exception to this module's tombstone-only rule).

        Each DELETE is independently idempotent (deleting an absent row is a
        no-op), so this is safe to call more than once for the same post --
        the property moderation_service's bounded group-hard-delete scrub
        loop relies on.
        """
        session = get_cassandra_session()
        post_id = _uuid(item.post_id)
        created = _dt(item.created_at_epoch)
        session.execute(X402SocialStmts.DELETE_POST, (post_id,))
        session.execute(X402SocialStmts.DELETE_POST_BY_AUTHOR, (item.author, created, post_id))
        if item.group_id:
            session.execute(
                X402SocialStmts.DELETE_GROUP_FEED_POST, (item.group_id, created, post_id)
            )
        session.execute(X402SocialStmts.DELETE_COMMENTS_PARTITION, (post_id,))

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
        """Return one post's comments oldest-first, at most `limit` of them (empty for a malformed, non-UUID id -- finding 5, 2026-security-audit)."""
        parsed = _try_uuid(post_id)
        if parsed is None:
            return []
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_COMMENTS, (parsed, limit))
        return [_row_to_comment(row) for row in rows]

    def count_comments(self, post_id: str, *, limit: int) -> int:
        """Number of comments on a post, at most `limit` (a LIMIT+1 caller detects truncation from the returned value equalling `limit`).

        Reads comment_id only, not the full row (optimization pass,
        2026-09-02): comment_count() previously called list_comments and
        threw away every field but the row count, paying for up to
        MAX_COMMENT_BYTES of body_md per row on every free GET /posts/{id}
        for nothing this function needs.
        """
        parsed = _try_uuid(post_id)
        if parsed is None:
            return 0
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_COMMENT_IDS, (parsed, limit))
        return sum(1 for _ in rows)

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
        """Add one to a post's up (value > 0) or down (value < 0) counter, atomically, exactly once.

        Called only after the reaction LWT (INSERT_REACTION_IF_ABSENT) has
        already won, so on a failure here the reaction IS durably recorded
        in x402_social_reaction_log -- only the denormalized counter is
        under-counted (finding 3, 2026-security-audit: same "core write
        succeeded, only a projection/counter is stale" class as the
        registration recency-projection case above, so likewise just
        documented via a warning log and re-raised, not compensated with a
        rollback -- there is nothing to roll back that would not itself
        under-count differently, since the reaction log entry is correct).
        """
        session = get_cassandra_session()
        stmt = (
            X402SocialStmts.INCREMENT_REACTION_UP
            if value > 0
            else X402SocialStmts.INCREMENT_REACTION_DOWN
        )
        try:
            session.execute(stmt, (_uuid(post_id),))
        except Exception:
            logger.warning(
                "x402 social react: reaction recorded for post_id=%s but the reaction "
                "counter increment failed -- the up/down total for this post is now "
                "under-counted by one",
                post_id,
                exc_info=True,
            )
            raise

    def get_reaction_totals(self, post_id: str) -> ReactionTotals:
        """Return a post's current up/down totals, (0, 0) if never reacted to (including for a malformed, non-UUID id -- finding 5, 2026-security-audit)."""
        parsed = _try_uuid(post_id)
        if parsed is None:
            return ReactionTotals()
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_REACTION_TOTALS, (parsed,)).one()
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

    def release_group_name(self, *, name_norm: str, group_id: str) -> None:
        """Best-effort compensating release of a name claim THIS group_id won (finding 3, 2026-security-audit). See base.SocialStore.release_group_name's own docstring."""
        session = get_cassandra_session()
        session.execute(X402SocialStmts.DELETE_GROUP_NAME_IF_OWNED, (name_norm, group_id))

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
                item.hidden_platform,
            ),
        )
        session.execute(
            X402SocialStmts.INSERT_GROUP_RECENCY,
            (
                GROUPS_PARTITION,
                created,
                item.group_id,
                item.name,
                item.description,
                item.owner,
                item.hidden_platform,
            ),
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

    def mark_group_hidden_platform(self, item: StoredGroup) -> None:
        """Set hidden_platform=true on the canonical row and the recency projection row, both via UPDATE ... IF EXISTS (Phase S2, design doc section 5.3 step 4: hidden from discovery, still point-readable and still servable to existing members)."""
        session = get_cassandra_session()
        session.execute(X402SocialStmts.MARK_GROUP_HIDDEN_PLATFORM, (item.group_id,))
        session.execute(
            X402SocialStmts.MARK_GROUP_RECENCY_HIDDEN_PLATFORM,
            (GROUPS_PARTITION, _dt(item.created_at_epoch), item.group_id),
        )

    def hard_delete_group_shell(self, item: StoredGroup, *, name_norm: str) -> None:
        """Real row removal of the group's own rows ONLY: canonical row, recency projection, and the name claim (freed -- re-claiming costs the full price again) -- Phase S2, design doc section 5.4.2.

        Does NOT touch membership rows or the group's posts -- see
        list_group_member_wallets / delete_group_memberships_partition and
        list_group_feed (already the group's own feed reader) for the rest
        of moderation_service's group hard-delete walk. Each DELETE is
        independently idempotent, same property as hard_delete_post.
        """
        session = get_cassandra_session()
        session.execute(X402SocialStmts.DELETE_GROUP, (item.group_id,))
        session.execute(
            X402SocialStmts.DELETE_GROUP_RECENCY,
            (GROUPS_PARTITION, _dt(item.created_at_epoch), item.group_id),
        )
        session.execute(X402SocialStmts.DELETE_GROUP_NAME_IF_OWNED, (name_norm, item.group_id))

    def list_group_member_wallets(self, group_id: str, *, limit: int) -> list[str]:
        """Bounded read of a group's member wallets, for moderation_service's group hard-delete walk."""
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_GROUP_MEMBER_WALLETS, (group_id, limit))
        return [row.wallet for row in rows]

    def delete_group_memberships_partition(self, group_id: str) -> None:
        """Delete every x402_social_group_members row for `group_id` (one partition delete) AND, per wallet already read via list_group_member_wallets, the caller is responsible for deleting the matching x402_social_memberships row (keyed by wallet, not group_id -- see delete_membership, already defined above)."""
        session = get_cassandra_session()
        session.execute(X402SocialStmts.DELETE_GROUP_MEMBERS_PARTITION, (group_id,))

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

    # ----------------------------------------------------------------- #
    # Phase S2: community moderation (design doc section 5). See
    # services/moderation_service.py for the case lifecycle, resolution,
    # and the hard-delete mechanics the section 8.1 admin lever also calls
    # into.
    # ----------------------------------------------------------------- #
    def try_claim_open_case_for_target(self, *, target_id: str, case_id: str) -> bool:
        """Claim the one-open-case-per-target guard for `target_id` IFF unclaimed (LWT). Returns True iff this call won it."""
        session = get_cassandra_session()
        result = session.execute(
            X402SocialStmts.INSERT_OPEN_CASE_BY_TARGET_IF_ABSENT, (target_id, _uuid(case_id))
        )
        return bool(result.was_applied)

    def release_open_case_for_target(self, *, target_id: str, case_id: str) -> None:
        """Best-effort compensating release of the open-case-per-target claim THIS case_id won (A1, 2026-09-03) -- used ONLY when open_report fails after the claim but before the case is fully stored. Reuses DELETE_OPEN_CASE_BY_TARGET_IF_OWNED, the same statement resolve_case's own win path already uses -- IF case_id = ? so this can only ever release the exact claim THIS case_id won, never a different (later) case's legitimate claim on the same target."""
        session = get_cassandra_session()
        session.execute(
            X402SocialStmts.DELETE_OPEN_CASE_BY_TARGET_IF_OWNED, (target_id, _uuid(case_id))
        )

    def get_open_case_id_for_target(self, target_id: str) -> str | None:
        """The currently-open case_id for `target_id`, or None -- used only to report it in a caller-fault 409's message when try_claim_open_case_for_target loses."""
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_OPEN_CASE_BY_TARGET, (target_id,)).one()
        return None if row is None else str(row.case_id)

    def insert_case(self, item: StoredCase) -> None:
        """Store a newly-opened case: the canonical row and its GET /cases feed row. Canonical first (store before mark)."""
        session = get_cassandra_session()
        case_id = _uuid(item.case_id)
        opened = _dt(item.opened_at_epoch)
        session.execute(
            X402SocialStmts.INSERT_CASE,
            (
                case_id,
                item.target_type,
                item.target_id,
                item.target_wallet,
                item.category,
                item.note,
                item.reporter,
                item.settlement_tx_id,
                item.content_snapshot,
                opened,
                _dt(item.window_ends_at_epoch),
                item.state,
                _dt(item.resolved_at_epoch) if item.resolved_at_epoch else None,
                item.resolution_note,
            ),
        )
        session.execute(
            X402SocialStmts.INSERT_OPEN_CASE_FEED,
            (CASES_PARTITION, opened, case_id, item.target_type, item.target_id, item.category),
        )

    def get_case(self, case_id: str) -> StoredCase | None:
        """Return the canonical case for an id, or None if there is none (including a malformed, non-UUID id)."""
        parsed = _try_uuid(case_id)
        if parsed is None:
            return None
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_CASE, (parsed,)).one()
        return None if row is None else _row_to_case(row)

    def list_open_cases(self, *, limit: int) -> list[StoredCase]:
        """Return open (not-yet-resolved-and-purged) cases newest-first, at most `limit` of them -- the free GET /cases 'jury duty' feed.

        Reads the feed's own (thin) rows for ordering/paging, then point-reads
        each case's full canonical row: the feed row's own columns are
        already a subset of the canonical row's, and a case in this feed can
        legitimately be past its window and awaiting lazy resolution on its
        NEXT touch -- the caller (moderation_service.list_open_cases) is
        responsible for running _resolve_if_due on any that are due, not
        this store method.
        """
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_OPEN_CASES, (CASES_PARTITION, limit))
        cases: list[StoredCase] = []
        for row in rows:
            case = self.get_case(str(row.case_id))
            if case is not None:
                cases.append(case)
        return cases

    def resolve_case(self, item: StoredCase) -> bool:
        """Apply `item`'s already-computed verdict to the canonical case row IFF it is still 'open' -- this conditional UPDATE IS the "resolver slot" LWT (design doc section 5.3): exactly one concurrent caller ever sees it applied=True.

        Returns True iff THIS call won the resolution (and is therefore
        responsible for applying the case's actual consequences -- tombstone/
        hard-delete/ban/karma, all in moderation_service.py). A losing
        caller (False) must apply nothing further -- another process already
        has, or is about to.

        On a win, also removes the case from the open-cases feed and
        releases the one-open-case-per-target guard (so the target becomes
        reportable again), both best-effort cleanup of denormalized state
        that the canonical row's own resolution already made authoritative.
        """
        session = get_cassandra_session()
        case_id = _uuid(item.case_id)
        result = session.execute(
            X402SocialStmts.UPDATE_CASE_RESOLUTION,
            (
                item.state,
                _dt(item.resolved_at_epoch),
                item.resolution_note,
                item.content_snapshot,
                case_id,
            ),
        )
        if not result.was_applied:
            return False
        session.execute(
            X402SocialStmts.DELETE_OPEN_CASE_FEED,
            (CASES_PARTITION, _dt(item.opened_at_epoch), case_id),
        )
        session.execute(
            X402SocialStmts.DELETE_OPEN_CASE_BY_TARGET_IF_OWNED, (item.target_id, case_id)
        )
        return True

    def try_add_case_vote(
        self, *, case_id: str, voter: str, verdict: str, settlement_tx_id: str, voted_at_epoch: int
    ) -> bool:
        """Insert the (case_id, voter) vote row IFF absent (LWT). Returns True iff this call won it -- same reaction-log discipline as try_add_reaction."""
        session = get_cassandra_session()
        result = session.execute(
            X402SocialStmts.INSERT_CASE_VOTE_IF_ABSENT,
            (_uuid(case_id), voter, verdict, settlement_tx_id, _dt(voted_at_epoch)),
        )
        return bool(result.was_applied)

    def increment_case_vote_total(self, case_id: str, *, verdict: str) -> None:
        """Add one to a case's uphold or reject counter, atomically. Called EXACTLY ONCE, only after try_add_case_vote returned True for this call -- same "issued exactly once" contract as increment_reaction_total.

        On a failure here the vote IS durably recorded in
        x402_social_case_votes (try_add_case_vote already won) -- only the
        denormalized tally is under-counted, the same "core write succeeded,
        only a counter is stale" class increment_reaction_total documents.
        Fixed 2026-09-03 (A4): this used to fail silently (no log line, same
        contract as increment_reaction_total's success path but missing that
        method's OWN warning-log-then-re-raise on failure) -- now logs at
        warning with the same detail before re-raising.
        """
        session = get_cassandra_session()
        stmt = (
            X402SocialStmts.INCREMENT_CASE_UPHOLD
            if verdict == VERDICT_UPHOLD
            else X402SocialStmts.INCREMENT_CASE_REJECT
        )
        try:
            session.execute(stmt, (_uuid(case_id),))
        except Exception:
            logger.warning(
                "x402 social moderation: vote recorded for case_id=%s verdict=%s but the vote "
                "tally counter increment failed -- the uphold/reject total for this case is now "
                "under-counted by one",
                case_id,
                verdict,
                exc_info=True,
            )
            raise

    def get_case_vote_totals(self, case_id: str) -> CaseTally:
        """Return a case's current uphold/reject totals, (0, 0) if never voted on."""
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_CASE_VOTE_TOTALS, (_uuid(case_id),)).one()
        if row is None:
            return CaseTally()
        return CaseTally(uphold=int(row.uphold or 0), reject=int(row.reject or 0))

    def list_case_votes(self, case_id: str, *, limit: int) -> list[tuple[str, str]]:
        """Return (voter, verdict) pairs for one case, at most `limit` of them -- the resolver's own karma bookkeeping input (votes_cast/votes_matched_resolution on each voter's standing)."""
        session = get_cassandra_session()
        rows = session.execute(X402SocialStmts.LIST_CASE_VOTES, (_uuid(case_id), limit))
        return [(row.voter, row.verdict) for row in rows]

    def get_standing(self, wallet: str) -> StoredStanding | None:
        """Return one wallet's full moderation standing row, or None if it has never been touched (a wallet with no row yet has no offenses/reports/votes -- moderation_service's own default-zero StoredStanding covers that case for callers)."""
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_STANDING, (wallet,)).one()
        return None if row is None else _row_to_standing(row)

    def upsert_standing(self, item: StoredStanding) -> None:
        """Full-row, UNCONDITIONAL overwrite of one wallet's standing (CLAUDE.md section 3: never a PARTIAL UPDATE -- the articles_feed phantom-null-row class of bug).

        This is NOT what a read-modify-write caller should use directly --
        see base.SocialStore.upsert_standing's own docstring: an
        unconditional overwrite from a stale read is exactly what let two
        concurrent read-modify-writes clobber each other (finding-class
        2026-09-03, A2), which is why every moderation_service.py caller now
        goes through mutate_standing (below) instead. This method survives
        for direct seeding (tests) and any future caller that has already
        re-derived the row's true current state some other way.
        """
        session = get_cassandra_session()
        session.execute(X402SocialStmts.UPSERT_STANDING, _standing_params(item))

    def mutate_standing(
        self, wallet: str, mutate: Callable[[StoredStanding], StoredStanding]
    ) -> StoredStanding:
        """Atomic read-modify-write of one wallet's standing row via bounded-retry compare-and-swap (A2, 2026-09-03).

        The OLD pattern every moderation_service.py caller used --
        get_standing() then a plain upsert_standing() of the mutated copy,
        as two separate calls -- let two concurrent mutations of the SAME
        wallet (e.g. one case resolution's ban write racing another case
        resolution's vote-karma settlement) interleave a stale read between
        them: the second writer's unconditional full-row INSERT silently
        overwrote the first writer's change, including a ban. This method
        closes that window: first-ever write for a wallet is an LWT insert
        (INSERT_STANDING_IF_ABSENT, no prior row to CAS against -- same
        precedent as INSERT_AGENT_IF_ABSENT); every write after that is a
        full-row CAS (UPDATE_STANDING_IF_MATCH, mirroring
        UPDATE_REPORTER_SLOTS_IF_MATCH's own "read, mutate, UPDATE ... IF
        every compared column still equals what was just read" shape,
        generalized to a full row since standing mixes plain columns that
        cannot be true Cassandra `counter`s -- see migration 108's own
        note). A losing CAS means a concurrent writer touched the row in
        between; this re-reads and retries, bounded by STANDING_CAS_RETRIES.

        Raises RuntimeError if the mutation still has not converged after
        that many attempts (logged at ERROR first) -- unlike
        release_reporter_slot's own best-effort give-up, a standing
        mutation can carry a ban, so silently discarding it here would just
        reproduce the bug this method exists to fix; a caller several
        layers up (get_case / cast_vote / list_open_cases) surfaces this as
        an unhandled 500 instead of a fabricated success.
        """
        session = get_cassandra_session()
        for _ in range(STANDING_CAS_RETRIES):
            current = self.get_standing(wallet)
            if current is None:
                base = StoredStanding(wallet=wallet)
                updated = mutate(base)
                result = session.execute(
                    X402SocialStmts.INSERT_STANDING_IF_ABSENT, _standing_params(updated)
                )
                if result.was_applied:
                    return updated
                continue  # someone else created the row first -- retry as an update
            # Snapshot the IF-clause values BEFORE calling mutate(): every
            # real caller's mutator (moderation_service.py) mutates the
            # StoredStanding it is handed IN PLACE and returns that same
            # object, so `current` and `updated` below are the identical
            # object -- computing the "old" values from `current` AFTER
            # mutate() would silently capture the POST-mutation values
            # instead, making the IF clause compare the new row against
            # itself and never match a genuinely-unchanged row (a bug
            # caught in this method's own regression test, which mutates
            # a StoredStanding in place exactly like every real call site).
            old_values = _standing_values(current)
            updated = mutate(current)
            result = session.execute(
                X402SocialStmts.UPDATE_STANDING_IF_MATCH,
                (*_standing_values(updated), wallet, *old_values),
            )
            if result.was_applied:
                return updated
        logger.error(
            "x402 social moderation: standing CAS for wallet=%s did not converge after %d "
            "retries under contention -- refusing to apply this mutation rather than silently "
            "drop it (this can include a ban)",
            wallet,
            STANDING_CAS_RETRIES,
        )
        raise RuntimeError(f"x402 social standing CAS did not converge for wallet={wallet}")

    def get_reporter_open_case_ids(self, reporter: str) -> frozenset[str]:
        """The set of case ids currently claimed against `reporter`'s open-report concurrency cap (design doc section 5.4.1), empty if the reporter has never filed a report."""
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_REPORTER_SLOTS, (reporter,)).one()
        if row is None or not row.open_case_ids:
            return frozenset()
        return frozenset(str(cid) for cid in row.open_case_ids)

    def try_claim_reporter_slot(self, *, reporter: str, case_id: str, max_open: int) -> bool:
        """CAS-claim one of `reporter`'s open-report slots for `case_id` (design doc section 5.4.1's frozen<set> concurrency-cap table). Returns True iff this call won a slot; False if the reporter is already at `max_open` open reports, INCLUDING when a lost race under contention exhausts the retry budget -- "a lost race that fills the set => refuse", the design doc's own words, not an error.

        First-ever claim for a reporter is a plain LWT insert (no prior row
        to CAS against); every claim after that is a bounded-retry
        read-then-compare-and-swap on the whole set value, since Cassandra
        cannot CAS-compare or LWT-guard a counter and a plain int would race
        under concurrent opens (see the migration's own note).
        """
        session = get_cassandra_session()
        case_uuid = _uuid(case_id)
        first = session.execute(
            X402SocialStmts.INSERT_REPORTER_SLOTS_IF_ABSENT, (reporter, {case_uuid})
        )
        if first.was_applied:
            return True
        for _ in range(REPORTER_SLOT_CAS_RETRIES):
            row = session.execute(X402SocialStmts.GET_REPORTER_SLOTS, (reporter,)).one()
            current = set(row.open_case_ids) if row and row.open_case_ids else set()
            if case_uuid in current:
                return True  # already holds this exact slot (retry after a prior partial failure)
            if len(current) >= max_open:
                return False
            updated = current | {case_uuid}
            result = session.execute(
                X402SocialStmts.UPDATE_REPORTER_SLOTS_IF_MATCH, (updated, reporter, current)
            )
            if result.was_applied:
                return True
        return False

    def release_reporter_slot(self, *, reporter: str, case_id: str) -> None:
        """Best-effort CAS-remove of one of `reporter`'s open-report slots -- idempotent (membership-based: removing an absent element from the compared set is simply a no-op return), so a repeated release from a retried _resolve_if_due touch cannot double-decrement (design doc section 5.4.1)."""
        session = get_cassandra_session()
        case_uuid = _uuid(case_id)
        for _ in range(REPORTER_SLOT_CAS_RETRIES):
            row = session.execute(X402SocialStmts.GET_REPORTER_SLOTS, (reporter,)).one()
            if row is None or not row.open_case_ids:
                return
            current = set(row.open_case_ids)
            if case_uuid not in current:
                return
            updated = current - {case_uuid}
            result = session.execute(
                X402SocialStmts.UPDATE_REPORTER_SLOTS_IF_MATCH, (updated, reporter, current)
            )
            if result.was_applied:
                return
        logger.warning(
            "x402 social moderation: failed to release reporter slot for reporter=%s "
            "case_id=%s after %d CAS retries -- this reporter's open-report count may stay "
            "stale (overcounted) until a future release attempt succeeds",
            reporter,
            case_id,
            REPORTER_SLOT_CAS_RETRIES,
        )

    def insert_removal(self, item: RemovalRecord) -> None:
        """Append-only hard-delete audit record (design doc sections 5.4.2/8.1) -- never mutated after insert."""
        session = get_cassandra_session()
        session.execute(
            X402SocialStmts.INSERT_REMOVAL,
            (
                _uuid(item.case_id),
                item.target_type,
                item.target_id,
                item.target_wallet,
                item.category,
                item.removed_by,
                _dt(item.resolved_at_epoch),
                item.uphold_votes,
                item.reject_votes,
            ),
        )

    def get_removal(self, case_id: str) -> RemovalRecord | None:
        """Point read of one hard-delete audit record, or None. Not part of the design doc's public endpoint table -- see GET_REMOVAL's own comment."""
        parsed = _try_uuid(case_id)
        if parsed is None:
            return None
        session = get_cassandra_session()
        row = session.execute(X402SocialStmts.GET_REMOVAL, (parsed,)).one()
        if row is None:
            return None
        return RemovalRecord(
            case_id=str(row.case_id),
            target_type=row.target_type or "",
            target_id=row.target_id or "",
            target_wallet=row.target_wallet or "",
            category=row.category or "",
            removed_by=row.removed_by or "",
            resolved_at_epoch=_epoch(row.resolved_at),
            uphold_votes=int(row.uphold_votes or 0),
            reject_votes=int(row.reject_votes or 0),
        )
