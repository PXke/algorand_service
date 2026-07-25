"""Fetch a spot price tick from CoinGecko."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from app.modules.metrics import coingecko_cache as cache
from app.modules.metrics.price_metrics_models import PriceTick

COINGECKO_API = "https://api.coingecko.com/api/v3"


class PriceMetricsCollectorError(Exception):
    """Raised when a price tick can't be collected."""
    pass


def _tick_to_dict(t: PriceTick) -> dict:
    return {
        "asset_id": t.asset_id,
        "asset_name": t.asset_name,
        "currency": t.currency,
        "price_usd": t.price_usd,
        "change_24h_pct": t.change_24h_pct,
        "market_cap_usd": t.market_cap_usd,
        "volume_24h_usd": t.volume_24h_usd,
        "collected_at": t.collected_at.isoformat(),
        "source": t.source,
    }


def _tick_from_dict(d: dict) -> PriceTick:
    return PriceTick(
        asset_id=d["asset_id"],
        asset_name=d["asset_name"],
        currency=d["currency"],
        price_usd=d["price_usd"],
        change_24h_pct=d.get("change_24h_pct"),
        market_cap_usd=d.get("market_cap_usd"),
        volume_24h_usd=d.get("volume_24h_usd"),
        collected_at=datetime.fromisoformat(d["collected_at"]),
        source=d.get("source", "coingecko"),
    )


def _resolve_asset_name(client: httpx.Client, asset_id: str) -> str:
    """Asset name is static — cache it for a week so we skip the heavy /coins call."""
    cached = cache.get_name(asset_id)
    if cached:
        return cached
    meta = client.get(f"{COINGECKO_API}/coins/{asset_id}")
    meta.raise_for_status()
    name = str(meta.json().get("name") or asset_id)
    cache.set_name(asset_id, name)
    return name


def fetch_spot_tick(
    asset_id: str = "algorand",
    *,
    currency: str = "usd",
    timeout: float = 20.0,
    use_cache: bool = True,
) -> PriceTick:
    """Current spot price and 24h market stats from CoinGecko simple/price.

    Cached in Redis for COINGECKO_CACHE_TTL so concurrent tasks share one call;
    on a CoinGecko error a last-good copy (COINGECKO_STALE_TTL) is served if any.
    """
    from app.core.config import COINGECKO_CACHE_TTL, COINGECKO_STALE_TTL

    asset_id = asset_id.strip().lower()
    fresh_key = f"coingecko:spot:{asset_id}:{currency}"
    stale_key = f"coingecko:spot:last:{asset_id}:{currency}"

    if use_cache:
        hit = cache.get_json(fresh_key)
        if hit is not None:
            return _tick_from_dict(hit)

    params = {
        "ids": asset_id,
        "vs_currencies": currency,
        "include_24hr_change": "true",
        "include_market_cap": "true",
        "include_24hr_vol": "true",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            asset_name = _resolve_asset_name(client, asset_id)
            price_resp = client.get(f"{COINGECKO_API}/simple/price", params=params)
            price_resp.raise_for_status()
            payload = price_resp.json().get(asset_id)
            if not payload:
                raise PriceMetricsCollectorError(f"no price data for {asset_id}")

        price_usd = float(payload.get(currency) or 0)
        if price_usd <= 0:
            raise PriceMetricsCollectorError(f"invalid price for {asset_id}")

        change = payload.get(f"{currency}_24h_change")
        market_cap = payload.get(f"{currency}_market_cap")
        volume = payload.get(f"{currency}_24h_vol")

        tick = PriceTick(
            asset_id=asset_id,
            asset_name=asset_name,
            currency=currency.upper(),
            price_usd=price_usd,
            change_24h_pct=float(change) if change is not None else None,
            market_cap_usd=float(market_cap) if market_cap is not None else None,
            volume_24h_usd=float(volume) if volume is not None else None,
            collected_at=datetime.now(tz=UTC),
        )
    except (httpx.HTTPError, PriceMetricsCollectorError):
        # Serve last-good on a transient CoinGecko failure rather than breaking.
        stale = cache.get_json(stale_key)
        if stale is not None:
            return _tick_from_dict(stale)
        raise

    cache.set_json(fresh_key, _tick_to_dict(tick), COINGECKO_CACHE_TTL)
    cache.set_json(stale_key, _tick_to_dict(tick), COINGECKO_STALE_TTL)
    return tick
