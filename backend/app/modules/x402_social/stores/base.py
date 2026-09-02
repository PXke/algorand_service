"""Storage interface for x402 social agent profiles (Phase S0)."""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_social.models.domain import AgentProfile


class SocialStore(Protocol):
    """Storage interface for the x402 social identity/foundation layer."""

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
