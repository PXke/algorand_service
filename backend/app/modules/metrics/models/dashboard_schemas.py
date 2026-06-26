from __future__ import annotations

from pydantic import BaseModel


class MetricTile(BaseModel):
    id: str
    label: str
    value: str
    hint: str | None = None
    available: bool = True


class MetricsDashboardResponse(BaseModel):
    tiles: list[MetricTile]
