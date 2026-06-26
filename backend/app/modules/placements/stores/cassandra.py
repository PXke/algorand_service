from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.modules.placements.models import FeedPlacementItem


class PlacementsStore:
    def list_active(self, *, slot: str, limit: int = 10) -> list[FeedPlacementItem]:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()
        now = datetime.now(tz=UTC)
        rows = session.execute(
            """
            SELECT placement_id, slot, sponsor_name, headline, body,
                   image_url, target_url, priority, enabled, active_from, active_until
            FROM feed_placements_by_slot
            WHERE slot = %s
            LIMIT %s
            """,
            (slot, limit),
        )
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
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()
        now = datetime.now(tz=UTC)
        session.execute(
            """
            INSERT INTO feed_placements (
              placement_id, slot, sponsor_name, headline, body, image_url, target_url,
              priority, enabled, active_from, active_until, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
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
            """
            INSERT INTO feed_placements_by_slot (
              slot, priority, placement_id, sponsor_name, headline, body,
              image_url, target_url, enabled, active_from, active_until
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
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
