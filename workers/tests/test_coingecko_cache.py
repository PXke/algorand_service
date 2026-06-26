"""CoinGecko caching: a cache hit short-circuits the HTTP calls, and the tick
serialization round-trips. Keeps us from hammering CoinGecko."""

from datetime import UTC, datetime

from app.modules.metrics import coingecko_cache as cache
from app.modules.metrics import price_metrics_collector as collector
from app.modules.metrics.price_metrics_models import PriceTick


def _tick() -> PriceTick:
    return PriceTick(
        asset_id="algorand",
        asset_name="Algorand",
        currency="USD",
        price_usd=0.18,
        change_24h_pct=-1.5,
        market_cap_usd=1.5e9,
        volume_24h_usd=2.5e7,
        collected_at=datetime(2026, 6, 22, 12, 0, tzinfo=UTC),
    )


def test_tick_dict_round_trip():
    t = _tick()
    back = collector._tick_from_dict(collector._tick_to_dict(t))
    assert back == t


def test_cache_hit_skips_http(monkeypatch):
    # A fresh cache hit must return without touching httpx at all.
    monkeypatch.setattr(cache, "get_json", lambda key: collector._tick_to_dict(_tick()))

    def _boom(*a, **k):
        raise AssertionError("HTTP should not be called on a cache hit")

    monkeypatch.setattr(collector.httpx, "Client", _boom)
    out = collector.fetch_spot_tick("algorand")
    assert out.price_usd == 0.18
    assert out.asset_name == "Algorand"


def test_stale_served_on_error(monkeypatch):
    # No fresh hit, HTTP raises -> serve last-good stale copy.
    state = {"fresh": None, "stale": collector._tick_to_dict(_tick())}

    def _get_json(key):
        return state["stale"] if "last" in key else state["fresh"]

    monkeypatch.setattr(cache, "get_json", _get_json)
    monkeypatch.setattr(cache, "get_name", lambda a: "Algorand")

    def _boom(*a, **k):
        raise collector.httpx.ConnectError("down")

    monkeypatch.setattr(collector.httpx, "Client", _boom)
    out = collector.fetch_spot_tick("algorand")
    assert out.price_usd == 0.18  # stale served, no raise
