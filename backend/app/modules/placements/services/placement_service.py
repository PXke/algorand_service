"""Merge sponsored placements into the reader feed."""

from __future__ import annotations

from app.core.config import settings
from app.modules.placements.models import FeedPlacementItem
from app.modules.placements.stores.cassandra import PlacementsStore


class PlacementService:
    """Merge sponsored placements into the reader feed."""
    def __init__(self, store: PlacementsStore | None = None) -> None:
        """Wire the placements store, defaulting to a fresh instance."""
        self._store = store or PlacementsStore()

    def list_feed_placements(
        self,
        *,
        slot: str | None = None,
        limit: int | None = None,
    ) -> list[FeedPlacementItem]:
        """List active sponsored placements for a feed slot."""
        resolved_slot = slot or settings.news_placement_slot
        cap = limit if limit is not None else settings.news_placement_limit
        return self._store.list_active(slot=resolved_slot, limit=cap)
