from __future__ import annotations

from pydantic import BaseModel


class PriceMetricsResponse(BaseModel):
    asset_id: str
    asset_name: str
    currency: str
    price_usd: float
    change_24h_pct: float | None
    market_cap_usd: float | None = None
    volume_24h_usd: float | None = None
    prepared_at_epoch: int | None
    sample_count_24h: int
    available: bool
