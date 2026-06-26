from __future__ import annotations

from datetime import UTC, datetime

from app.modules.metrics import price_metrics_service
from app.modules.metrics.price_metrics_models import PriceTick


def test_run_skips_when_disabled(monkeypatch) -> None:
    import app.core.config as config

    monkeypatch.setattr(config, "PRICE_METRICS_ENABLED", False)
    result = price_metrics_service.run_collect_and_prepare_price_metrics()
    assert result["status"] == "skipped"


def test_run_collects_and_saves(monkeypatch) -> None:
    import app.core.config as config

    monkeypatch.setattr(config, "PRICE_METRICS_ENABLED", True)
    tick = PriceTick(
        asset_id="algorand",
        asset_name="Algorand",
        currency="USD",
        price_usd=0.25,
        change_24h_pct=1.0,
        market_cap_usd=None,
        volume_24h_usd=None,
        collected_at=datetime(2026, 6, 2, tzinfo=UTC),
    )
    monkeypatch.setattr(price_metrics_service, "fetch_spot_tick", lambda asset_id: tick)
    monkeypatch.setattr(price_metrics_service, "insert_sample", lambda t: None)
    monkeypatch.setattr(price_metrics_service, "list_recent_samples", lambda asset_id, **kw: [])
    monkeypatch.setattr(price_metrics_service, "fetch_weekly_reference", lambda asset_id: None)
    saved: list = []
    monkeypatch.setattr(price_metrics_service, "save_brief", lambda b: saved.append(b))

    result = price_metrics_service.run_collect_and_prepare_price_metrics()
    assert result["status"] == "ok"
    assert saved
    assert "Algorand" in saved[0].mistral_context
