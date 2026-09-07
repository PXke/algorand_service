"""In-memory x402 board store for dev and tests."""

from __future__ import annotations

import threading

from app.modules.x402_board.models.domain import StoredClickEvent, StoredPlacement


class InMemoryPlacementStore:
    """In-memory x402 visibility-board placement storage."""

    def __init__(self) -> None:
        """Start with an empty board, no clicks and no click-event history."""
        self._items: dict[str, StoredPlacement] = {}
        self._clicks: dict[str, int] = {}
        self._click_events: dict[str, list[StoredClickEvent]] = {}
        # Guards the click total: `+= 1` is an interruptible read-modify-write,
        # and the Cassandra backend's counter column is atomic, so the memory
        # backend keeps the same promise with a lock (same reasoning as the
        # feature board's vote total). Also guards `_click_events`, appended
        # from the same call site.
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

    def list_by_category(self, category: str, *, limit: int) -> list[StoredPlacement]:
        """Return placements in this category, newest-first, at most `limit`."""
        matching = [item for item in self._items.values() if item.category == category]
        ordered = sorted(matching, key=lambda i: (-i.created_at_epoch, i.entry_id))
        return ordered[: max(0, limit)]

    def increment_clicks(self, entry_id: str) -> None:
        """Add one to a placement's click-through total, atomically."""
        with self._lock:
            self._clicks[entry_id] = self._clicks.get(entry_id, 0) + 1

    def get_click_counts(self, entry_ids: list[str]) -> dict[str, int]:
        """Return click totals for many placements at once, keyed by entry id."""
        with self._lock:
            return {eid: self._clicks[eid] for eid in entry_ids if eid in self._clicks}

    def delete(self, entry_id: str) -> bool:
        """Remove one placement. False if it did not exist. Clicks and click events are kept, as in Cassandra."""
        return self._items.pop(entry_id, None) is not None

    def record_click_event(self, entry_id: str, *, clicked_at_epoch: int, referrer: str) -> None:
        """Append one click event, newest last (list_click_events sorts on read)."""
        with self._lock:
            self._click_events.setdefault(entry_id, []).append(
                StoredClickEvent(
                    entry_id=entry_id, clicked_at_epoch=clicked_at_epoch, referrer=referrer
                )
            )

    def list_click_events(
        self, entry_id: str, *, since_epoch: int, limit: int
    ) -> list[StoredClickEvent]:
        """Up to `limit` click events for this entry_id with clicked_at >= since_epoch, newest first."""
        with self._lock:
            events = list(self._click_events.get(entry_id, ()))
        in_window = [e for e in events if e.clicked_at_epoch >= since_epoch]
        in_window.sort(key=lambda e: e.clicked_at_epoch, reverse=True)
        return in_window[: max(0, limit)]
