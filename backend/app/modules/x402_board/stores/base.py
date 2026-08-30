"""Storage interface for board placements."""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_board.models.domain import StoredPlacement


class PlacementStore(Protocol):
    """Storage interface for x402 visibility-board placements."""

    def upsert(self, item: StoredPlacement) -> None:
        """Create or replace one placement, recency projection included."""
        ...

    def get(self, entry_id: str) -> StoredPlacement | None:
        """Return the placement for an entry id, or None if there is none."""
        ...

    def list_recent(self, *, limit: int) -> list[StoredPlacement]:
        """Return placements newest-first, at most `limit` of them.

        Returns them regardless of whether their term has ended -- expiry is a
        product rule the service layer applies, so that it applies identically
        to every backend rather than once per backend.
        """
        ...
