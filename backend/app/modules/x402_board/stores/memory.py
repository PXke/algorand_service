"""In-memory x402 board store for dev and tests."""

from __future__ import annotations

import threading

from app.modules.x402_board.models.domain import StoredPlacement


class InMemoryPlacementStore:
    """In-memory x402 visibility-board placement storage."""

    def __init__(self) -> None:
        """Start with an empty board and no clicks."""
        self._items: dict[str, StoredPlacement] = {}
        self._clicks: dict[str, int] = {}
        # Guards the click total: `+= 1` is an interruptible read-modify-write,
        # and the Cassandra backend's counter column is atomic, so the memory
        # backend keeps the same promise with a lock (same reasoning as the
        # feature board's vote total).
        self._lock = threading.Lock()

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

    def increment_clicks(self, entry_id: str) -> None:
        """Add one to a placement's click-through total, atomically."""
        with self._lock:
            self._clicks[entry_id] = self._clicks.get(entry_id, 0) + 1

    def get_click_counts(self, entry_ids: list[str]) -> dict[str, int]:
        """Return click totals for many placements at once, keyed by entry id."""
        with self._lock:
            return {eid: self._clicks[eid] for eid in entry_ids if eid in self._clicks}
