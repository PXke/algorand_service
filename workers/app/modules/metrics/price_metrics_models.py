"""Data models for price ticks, samples, window stats, and briefs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class PriceTick:
    """One fetched spot-price reading."""

    asset_id: str
    asset_name: str
    currency: str
    price_usd: float
    change_24h_pct: float | None
    market_cap_usd: float | None
    volume_24h_usd: float | None
    collected_at: datetime
    source: str = "coingecko"


@dataclass(frozen=True)
class PriceSampleRow:
    """One stored price sample row."""

    asset_id: str
    collected_at: datetime
    price_usd: float
    currency: str
    change_24h_pct: float | None
    market_cap_usd: float | None
    volume_24h_usd: float | None
    source: str


@dataclass(frozen=True)
class WindowStats:
    """Aggregated price stats over one time window."""

    label: str
    sample_count: int
    price_min: float
    price_max: float
    price_avg: float
    change_pct: float
    first_price: float
    last_price: float


@dataclass(frozen=True)
class PriceMetricsBrief:
    """A windowed price-stats snapshot ready for storage/display."""

    asset_id: str
    asset_name: str
    currency: str
    prepared_at: datetime
    current_price_usd: float
    change_24h_pct: float | None
    sample_count_24h: int
    sample_count_7d: int
    mistral_context: str
