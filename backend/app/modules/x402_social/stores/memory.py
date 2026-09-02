"""In-memory x402 social agent-profile store for dev and tests."""

from __future__ import annotations

from dataclasses import replace

from app.modules.x402_social.models.domain import AgentProfile


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
        """Start with an empty agents table and an empty recency projection."""
        self._agents: dict[str, AgentProfile] = {}
        self._recency: dict[str, AgentProfile] = {}

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
