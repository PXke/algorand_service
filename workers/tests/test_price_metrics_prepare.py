"""Building the Mistral context and brief from stored price samples."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.modules.metrics.price_metrics_models import PriceSampleRow, PriceTick
from app.modules.metrics.price_metrics_prepare import build_brief, build_mistral_context
from app.modules.newspaper.price_analysis import WeeklyPriceSnapshot


def _tick(price: float, when: datetime) -> PriceTick:
    return PriceTick(
        asset_id="algorand",
        asset_name="Algorand",
        currency="USD",
        price_usd=price,
        change_24h_pct=2.5,
        market_cap_usd=1_000_000_000.0,
        volume_24h_usd=50_000_000.0,
        collected_at=when,
    )


def test_build_mistral_context_includes_windows() -> None:
    """build_mistral_context includes both 24h and 7d windows plus the CoinGecko chart note."""
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    samples = [
        PriceSampleRow(
            asset_id="algorand",
            collected_at=now - timedelta(hours=6),
            price_usd=0.20,
            currency="USD",
            change_24h_pct=None,
            market_cap_usd=None,
            volume_24h_usd=None,
            source="coingecko",
        ),
        PriceSampleRow(
            asset_id="algorand",
            collected_at=now - timedelta(hours=1),
            price_usd=0.25,
            currency="USD",
            change_24h_pct=None,
            market_cap_usd=None,
            volume_24h_usd=None,
            source="coingecko",
        ),
    ]
    weekly = WeeklyPriceSnapshot(
        asset_id="algorand",
        asset_name="Algorand",
        currency="USD",
        price_usd=0.25,
        week_open_usd=0.20,
        week_high_usd=0.26,
        week_low_usd=0.19,
        week_change_pct=25.0,
        as_of=now,
    )
    text = build_mistral_context(_tick(0.25, now), samples, weekly=weekly)
    assert "Algorand" in text
    assert "24h" in text
    assert "7d" in text
    assert "CoinGecko 7-day chart" in text
    assert "0.25" in text


def test_build_brief_counts_samples() -> None:
    """build_brief counts the 24h samples it was given."""
    now = datetime(2026, 6, 2, 12, 0, tzinfo=UTC)
    samples = [
        PriceSampleRow(
            asset_id="algorand",
            collected_at=now - timedelta(hours=2),
            price_usd=0.22,
            currency="USD",
            change_24h_pct=None,
            market_cap_usd=None,
            volume_24h_usd=None,
            source="coingecko",
        ),
    ]
    brief = build_brief(_tick(0.24, now), samples)
    assert brief.sample_count_24h == 1
    assert brief.mistral_context
