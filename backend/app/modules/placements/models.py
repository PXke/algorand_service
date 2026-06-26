from __future__ import annotations

from pydantic import BaseModel


class FeedPlacementItem(BaseModel):
    placement_id: str
    slot: str
    sponsor_name: str
    headline: str
    body: str
    image_url: str | None = None
    target_url: str | None = None
    priority: int = 0
