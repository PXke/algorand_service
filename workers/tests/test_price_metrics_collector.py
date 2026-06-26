from __future__ import annotations

from app.modules.metrics.price_metrics_collector import fetch_spot_tick


def test_fetch_spot_tick_mocked(monkeypatch) -> None:
    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get(self, url: str, params=None):
            if url.endswith("/coins/algorand"):
                return FakeResponse({"name": "Algorand"})
            return FakeResponse(
                {
                    "algorand": {
                        "usd": 0.25,
                        "usd_24h_change": 3.5,
                        "usd_market_cap": 2_000_000_000,
                        "usd_24h_vol": 100_000_000,
                    }
                }
            )

    monkeypatch.setattr(
        "app.modules.metrics.price_metrics_collector.httpx.Client",
        FakeClient,
    )
    tick = fetch_spot_tick("algorand")
    assert tick.asset_name == "Algorand"
    assert tick.price_usd == 0.25
    assert tick.change_24h_pct == 3.5
