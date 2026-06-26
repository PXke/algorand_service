from __future__ import annotations

from typing import Any


def collect_market_context(asset_id: str = "algorand") -> dict[str, Any]:
    """Live ALGO market snapshot for the writer bundle: price, 24h change,
    market cap, volume, plus the prepared trend narrative. Empty when metrics
    haven't been collected yet."""
    out: dict[str, Any] = {"available": False}
    try:
        from app.modules.metrics.price_metrics_store import list_recent_samples, load_brief

        brief = load_brief(asset_id)
        samples = list_recent_samples(asset_id=asset_id, limit=1)
        latest = samples[0] if samples else None
        if latest is None and brief is None:
            return out
        out["available"] = True
        if latest is not None:
            out["price_usd"] = round(float(latest.price_usd), 6)
            out["change_24h_pct"] = (
                round(float(latest.change_24h_pct), 2) if latest.change_24h_pct is not None else None
            )
            out["market_cap_usd"] = latest.market_cap_usd
            out["volume_24h_usd"] = latest.volume_24h_usd
            out["as_of"] = latest.collected_at.isoformat()
        if brief is not None:
            out["samples_7d"] = brief.sample_count_7d
            if brief.mistral_context.strip():
                out["trend_narrative"] = brief.mistral_context.strip()[:1500]
    except Exception:
        return {"available": False}
    return out
