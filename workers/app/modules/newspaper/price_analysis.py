"""Fetch and cache a weekly ALGO price snapshot for the digest."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

COINGECKO_API = "https://api.coingecko.com/api/v3"


@dataclass(frozen=True)
class WeeklyPriceSnapshot:
    """One week's ALGO price snapshot for the digest."""
    asset_id: str
    asset_name: str
    currency: str
    price_usd: float
    week_open_usd: float
    week_high_usd: float
    week_low_usd: float
    week_change_pct: float
    as_of: datetime


class PriceAnalysisError(Exception):
    """Raised when the weekly price snapshot can't be fetched."""
    pass


def _snapshot_to_dict(s: WeeklyPriceSnapshot) -> dict:
    return {
        "asset_id": s.asset_id,
        "asset_name": s.asset_name,
        "currency": s.currency,
        "price_usd": s.price_usd,
        "week_open_usd": s.week_open_usd,
        "week_high_usd": s.week_high_usd,
        "week_low_usd": s.week_low_usd,
        "week_change_pct": s.week_change_pct,
        "as_of": s.as_of.isoformat(),
    }


def _snapshot_from_dict(d: dict) -> WeeklyPriceSnapshot:
    return WeeklyPriceSnapshot(
        asset_id=d["asset_id"],
        asset_name=d["asset_name"],
        currency=d["currency"],
        price_usd=d["price_usd"],
        week_open_usd=d["week_open_usd"],
        week_high_usd=d["week_high_usd"],
        week_low_usd=d["week_low_usd"],
        week_change_pct=d["week_change_pct"],
        as_of=datetime.fromisoformat(d["as_of"]),
    )


def fetch_weekly_price(
    asset_id: str = "algorand",
    *,
    currency: str = "usd",
    days: int = 7,
    timeout: float = 20.0,
    use_cache: bool = True,
) -> WeeklyPriceSnapshot:
    """7-day market chart from CoinGecko (public API, no key required).

    Cached in Redis for COINGECKO_WEEKLY_CACHE_TTL (the 7d chart barely moves
    within an hour); a last-good copy is served if CoinGecko errors.
    """
    from app.core.config import COINGECKO_STALE_TTL, COINGECKO_WEEKLY_CACHE_TTL
    from app.modules.metrics import coingecko_cache as cache

    asset_id = asset_id.strip().lower()
    fresh_key = f"coingecko:weekly:{asset_id}:{currency}:{days}"
    stale_key = f"coingecko:weekly:last:{asset_id}:{currency}:{days}"

    if use_cache:
        hit = cache.get_json(fresh_key)
        if hit is not None:
            return _snapshot_from_dict(hit)

    try:
        with httpx.Client(timeout=timeout) as client:
            asset_name = cache.get_name(asset_id)
            if not asset_name:
                meta = client.get(f"{COINGECKO_API}/coins/{asset_id}")
                meta.raise_for_status()
                asset_name = str(meta.json().get("name") or asset_id)
                cache.set_name(asset_id, asset_name)

            chart = client.get(
                f"{COINGECKO_API}/coins/{asset_id}/market_chart",
                params={"vs_currency": currency, "days": str(days)},
            )
            chart.raise_for_status()
            prices = chart.json().get("prices") or []

        if len(prices) < 2:
            msg = f"insufficient price points for {asset_id}"
            raise PriceAnalysisError(msg)

        values = [float(p[1]) for p in prices if len(p) >= 2]
        if not values:
            raise PriceAnalysisError(f"no prices for {asset_id}")

        week_open = values[0]
        current = values[-1]
        change_pct = 0.0 if week_open == 0 else (current - week_open) / week_open * 100.0

        snapshot = WeeklyPriceSnapshot(
            asset_id=asset_id,
            asset_name=asset_name,
            currency=currency.upper(),
            price_usd=current,
            week_open_usd=week_open,
            week_high_usd=max(values),
            week_low_usd=min(values),
            week_change_pct=change_pct,
            as_of=datetime.now(tz=UTC),
        )
    except (httpx.HTTPError, PriceAnalysisError):
        stale = cache.get_json(stale_key)
        if stale is not None:
            return _snapshot_from_dict(stale)
        raise

    cache.set_json(fresh_key, _snapshot_to_dict(snapshot), COINGECKO_WEEKLY_CACHE_TTL)
    cache.set_json(stale_key, _snapshot_to_dict(snapshot), COINGECKO_STALE_TTL)
    return snapshot
