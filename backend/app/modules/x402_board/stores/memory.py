"""In-memory x402 board store for dev and tests."""

from __future__ import annotations

from app.modules.x402_board.models.domain import StoredPlacement


class InMemoryPlacementStore:
    """In-memory x402 visibility-board placement storage."""

    def __init__(self) -> None:
        """Start with an empty board."""
        self._items: dict[str, StoredPlacement] = {}

    def upsert(self, item: StoredPlacement) -> None:
        """Create or replace one placement."""
        self._items[item.entry_id] = item

    def get(self, entry_id: str) -> StoredPlacement | None:
        """Return the placement for an entry id, or None if there is none."""
        return self._items.get(entry_id)

    def list_recent(self, *, limit: int) -> list[StoredPlacement]:
        """Return placements newest-first, at most `limit` of them.

        Ties on created_at break by entry_id ascending, matching the Cassandra
        table's (created_at DESC, entry_id ASC) clustering order so tests see
        the same ordering as production.
        """
        ordered = sorted(self._items.values(), key=lambda i: (-i.created_at_epoch, i.entry_id))
        return ordered[: max(0, limit)]
