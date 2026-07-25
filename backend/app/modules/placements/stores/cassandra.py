"""Cassandra-backed placements storage."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.modules.placements.models import FeedPlacementItem


class PlacementsStore:
    """Cassandra-backed sponsored-placement storage."""
    def list_active(self, *, slot: str, limit: int = 10) -> list[FeedPlacementItem]:
        """List active, currently-scheduled placements for a slot, priority order."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import PlacementStmts

        session = get_cassandra_session()
        now = datetime.now(tz=UTC)
        rows = session.execute(PlacementStmts.LIST_BY_SLOT, (slot, limit))
        items: list[FeedPlacementItem] = []
        for row in rows:
            if row.enabled is False:
                continue
            if row.active_from and row.active_from > now:
                continue
            if row.active_until and row.active_until < now:
                continue
            items.append(
                FeedPlacementItem(
                    placement_id=str(row.placement_id),
                    slot=row.slot or slot,
                    sponsor_name=row.sponsor_name or "",
                    headline=row.headline or "",
                    body=row.body or "",
                    image_url=row.image_url,
                    target_url=row.target_url,
                    priority=int(row.priority) if row.priority is not None else 0,
                )
            )
        return items

    def upsert(
        self,
        *,
        placement_id: UUID,
        slot: str,
        sponsor_name: str,
        headline: str,
        body: str,
        image_url: str | None,
        target_url: str | None,
        priority: int,
        enabled: bool = True,
        active_from: datetime | None = None,
        active_until: datetime | None = None,
    ) -> None:
        """Insert or update a sponsored placement."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import PlacementStmts

        session = get_cassandra_session()
        now = datetime.now(tz=UTC)
        session.execute(
            PlacementStmts.INSERT,
            (
                placement_id,
                slot,
                sponsor_name,
                headline,
                body,
                image_url,
                target_url,
                priority,
                enabled,
                active_from,
                active_until,
                now,
            ),
        )
        session.execute(
            PlacementStmts.INSERT_BY_SLOT,
            (
                slot,
                priority,
                placement_id,
                sponsor_name,
                headline,
                body,
                image_url,
                target_url,
                enabled,
                active_from,
                active_until,
            ),
        )
