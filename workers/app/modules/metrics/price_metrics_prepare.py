"""Build the price-metrics brief and Mistral context from stored samples."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.config import PRICE_METRICS_BRIEF_MAX_CHARS
from app.modules.metrics.price_metrics_models import (
    PriceMetricsBrief,
    PriceSampleRow,
    PriceTick,
    WindowStats,
)
from app.modules.newspaper.price_analysis import (
    PriceAnalysisError,
    WeeklyPriceSnapshot,
    fetch_weekly_price,
)


def _window_stats(label: str, prices: list[float]) -> WindowStats | None:
    if len(prices) < 1:
        return None
    first = prices[0]
    last = prices[-1]
    low = min(prices)
    high = max(prices)
    avg = sum(prices) / len(prices)
    change = 0.0 if first == 0 else (last - first) / first * 100.0
    return WindowStats(
        label=label,
        sample_count=len(prices),
        price_min=low,
        price_max=high,
        price_avg=avg,
        change_pct=change,
        first_price=first,
        last_price=last,
    )


def _samples_in_window(
    samples: list[PriceSampleRow],
    *,
    hours: int,
    now: datetime,
) -> list[PriceSampleRow]:
    cutoff = now - timedelta(hours=hours)

    # Cassandra returns naive UTC timestamps; the cutoff is aware.
    def _aware(collected_at: datetime) -> datetime:
        return collected_at.replace(tzinfo=UTC) if collected_at.tzinfo is None else collected_at

    return [row for row in samples if _aware(row.collected_at) >= cutoff]


def _format_window(stats: WindowStats | None) -> list[str]:
    if stats is None:
        return ["(insufficient stored samples)"]
    return [
        f"- samples: {stats.sample_count}",
        f"- first → last: ${stats.first_price:,.4f} → ${stats.last_price:,.4f} "
        f"({stats.change_pct:+.2f}%)",
        f"- range: ${stats.price_min:,.4f} - ${stats.price_max:,.4f}",
        f"- average: ${stats.price_avg:,.4f}",
    ]


def build_mistral_context(
    tick: PriceTick,
    samples: list[PriceSampleRow],
    *,
    weekly: WeeklyPriceSnapshot | None = None,
    max_chars: int = PRICE_METRICS_BRIEF_MAX_CHARS,
) -> str:
    """Structured facts for Mistral (stored and injected into article prompts)."""
    now = tick.collected_at
    samples_24h = _samples_in_window(samples, hours=24, now=now)
    samples_7d = _samples_in_window(samples, hours=24 * 7, now=now)
    prices_24h = [row.price_usd for row in samples_24h]
    prices_7d = [row.price_usd for row in samples_7d]

    stats_24h = _window_stats("24h (stored polls)", prices_24h)
    stats_7d = _window_stats("7d (stored polls)", prices_7d)

    lines = [
        f"# Price metrics brief — {tick.asset_name} ({tick.asset_id})",
        f"As of: {now.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Source: {tick.source}",
        "",
        "## Spot (latest poll)",
        f"- price: ${tick.price_usd:,.4f} {tick.currency}",
    ]
    if tick.change_24h_pct is not None:
        lines.append(f"- CoinGecko 24h change: {tick.change_24h_pct:+.2f}%")
    if tick.market_cap_usd is not None:
        lines.append(f"- market cap: ${tick.market_cap_usd:,.0f}")
    if tick.volume_24h_usd is not None:
        lines.append(f"- 24h volume: ${tick.volume_24h_usd:,.0f}")
    lines.extend(["", "## Stored sample windows", "", "### Last 24 hours"])
    lines.extend(_format_window(stats_24h))
    lines.extend(["", "### Last 7 days"])
    lines.extend(_format_window(stats_7d))

    if weekly is not None:
        lines.extend(
            [
                "",
                "## CoinGecko 7-day chart (reference)",
                f"- chart 7d open: ${weekly.week_open_usd:,.4f}",
                f"- chart 7d high: ${weekly.week_high_usd:,.4f}",
                f"- chart 7d low: ${weekly.week_low_usd:,.4f}",
                f"- chart 7d change: {weekly.week_change_pct:+.2f}%",
            ]
        )

    lines.extend(
        [
            "",
            "Instructions for the model: use only the numbers above; "
            "mention sample counts when discussing trends; do not invent prices.",
        ]
    )
    text = "\n".join(lines)
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def build_brief(
    tick: PriceTick,
    samples: list[PriceSampleRow],
    *,
    weekly: WeeklyPriceSnapshot | None = None,
) -> PriceMetricsBrief:
    """Summarize a price tick plus its recent samples into a brief for the writer."""
    now = tick.collected_at
    samples_24h = _samples_in_window(samples, hours=24, now=now)
    samples_7d = _samples_in_window(samples, hours=24 * 7, now=now)
    context = build_mistral_context(tick, samples, weekly=weekly)
    return PriceMetricsBrief(
        asset_id=tick.asset_id,
        asset_name=tick.asset_name,
        currency=tick.currency,
        prepared_at=now,
        current_price_usd=tick.price_usd,
        change_24h_pct=tick.change_24h_pct,
        sample_count_24h=len(samples_24h),
        sample_count_7d=len(samples_7d),
        mistral_context=context,
    )


def fetch_weekly_reference(asset_id: str) -> WeeklyPriceSnapshot | None:
    """Fetch the weekly price snapshot for an asset, swallowing errors as None."""
    try:
        return fetch_weekly_price(asset_id)
    except (PriceAnalysisError, Exception):
        return None
