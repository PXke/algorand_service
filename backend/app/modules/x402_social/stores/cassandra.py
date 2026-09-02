"""Cassandra-backed x402 social agent-profile storage (Phase S0)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.cassandra import get_cassandra_session
from app.core.statements import X402SocialStmts
from app.modules.x402_social.models.domain import AGENTS_PARTITION, AgentProfile


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    return int(value.timestamp()) if value else 0


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
