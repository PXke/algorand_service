"""Storage interface for board placements."""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_board.models.domain import StoredClickEvent, StoredPlacement


class PlacementStore(Protocol):
    """Storage interface for x402 visibility-board placements."""

    def upsert(self, item: StoredPlacement) -> None:
        """Create or replace one placement, recency and category projections included."""
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

    def list_by_category(self, category: str, *, limit: int) -> list[StoredPlacement]:
        """Return placements in this (already-validated) category, newest-first, at most `limit`.

        Same expiry contract as list_recent: expired placements are returned
        too, filtered by BoardService, not here.
        """
        ...

    def increment_clicks(self, entry_id: str) -> None:
        """Add one to a placement's click-through total, atomically.

        Only ever called from the free redirect route, never from the feed
        read: the feed is the hot path and must stay a pure read. Same
        atomic-add-one contract as the feature board's vote counter -- two
        clicks landing at once are two clicks.
        """
        ...

    def get_click_counts(self, entry_ids: list[str]) -> dict[str, int]:
        """Return click totals for many placements at once, keyed by entry id.

        One method rather than a loop over point reads at the call site, so
        the feed's latency does not scale with the page size. Ids with no
        clicks may be omitted; the caller treats a missing id as 0.
        """
        ...

    def delete(self, entry_id: str) -> bool:
        """Remove one placement, recency and category projections included. False if it did not exist.

        Admin-only. The click counter AND the click-event log are left in
        place: both are keyed on the entry id, so they are unreachable once
        the placement is gone, and a counter row cannot be safely deleted
        then re-incremented in Cassandra.
        """
        ...

    def record_click_event(self, entry_id: str, *, clicked_at_epoch: int, referrer: str) -> None:
        """Append one click event for the owner-only click-analytics read.

        Called from the free redirect route, alongside (never instead of)
        increment_clicks -- this is a SECOND write, not a replacement for the
        lifetime counter. A failure here is the caller's (BoardService.click's)
        problem to log and swallow, same as the counter bump.
        """
        ...

    def list_click_events(
        self, entry_id: str, *, since_epoch: int, limit: int
    ) -> list[StoredClickEvent]:
        """Up to `limit` click events for this entry_id with clicked_at >= since_epoch, newest first."""
        ...
