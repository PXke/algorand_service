from __future__ import annotations

from datetime import UTC, datetime

from app.modules.metrics.services.price_service import PriceMetricsService
from app.modules.metrics.stores.cassandra import StoredPriceBrief


def test_price_metrics_unavailable_when_no_brief(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.metrics.services.price_service.load_price_brief",
        lambda asset_id: None,
    )
    result = PriceMetricsService().get_spot()
    assert result.available is False
    assert result.price_usd == 0.0


def test_price_metrics_returns_brief(monkeypatch) -> None:
    brief = StoredPriceBrief(
        asset_id="algorand",
        asset_name="Algorand",
        currency="USD",
        current_price_usd=0.25,
        change_24h_pct=1.5,
        market_cap_usd=2_000_000_000.0,
        prepared_at=datetime(2026, 1, 1, tzinfo=UTC),
        sample_count_24h=12,
    )
    monkeypatch.setattr(
        "app.modules.metrics.services.price_service.load_price_brief",
        lambda asset_id: brief,
    )
    result = PriceMetricsService().get_spot()
    assert result.available is True
    assert result.price_usd == 0.25
    assert result.change_24h_pct == 1.5
    assert result.prepared_at_epoch == int(brief.prepared_at.timestamp())
