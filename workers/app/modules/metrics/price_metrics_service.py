from __future__ import annotations

import httpx

from app.core import config
from app.core.config import PRICE_METRICS_ASSET_ID
from app.modules.metrics.price_metrics_collector import (
    PriceMetricsCollectorError,
    fetch_spot_tick,
)
from app.modules.metrics.price_metrics_prepare import build_brief, fetch_weekly_reference
from app.modules.metrics.price_metrics_store import (
    insert_sample,
    list_recent_samples,
    save_brief,
)


def run_collect_and_prepare_price_metrics(
    *,
    asset_id: str = PRICE_METRICS_ASSET_ID,
) -> dict[str, str]:
    """Poll CoinGecko, append a sample, rebuild the Mistral-ready brief."""
    if not config.PRICE_METRICS_ENABLED:
        return {"status": "skipped", "reason": "PRICE_METRICS_ENABLED=0"}

    try:
        tick = fetch_spot_tick(asset_id)
    except (PriceMetricsCollectorError, httpx.HTTPError) as exc:
        return {"status": "error", "detail": str(exc)}

    insert_sample(tick)
    samples = list_recent_samples(tick.asset_id, lookback_days=7)
    weekly = fetch_weekly_reference(tick.asset_id)
    brief = build_brief(tick, samples, weekly=weekly)
    save_brief(brief)

    return {
        "status": "ok",
        "asset_id": tick.asset_id,
        "price_usd": f"{tick.price_usd:.6f}",
        "samples_7d": str(brief.sample_count_7d),
        "samples_24h": str(brief.sample_count_24h),
        "context_chars": str(len(brief.mistral_context)),
    }
