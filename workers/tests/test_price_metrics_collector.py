"""Fetching a spot price tick."""

from __future__ import annotations

from typing import Self

import pytest

from app.modules.metrics.price_metrics_collector import fetch_spot_tick


def test_fetch_spot_tick_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Parses a spot price tick (price/change/market-cap/volume) from a mocked CoinGecko response."""
    class FakeResponse:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str, params: tuple | None = None) -> FakeResponse:  # noqa: ARG002 -- name must match the real callee's keyword arg
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
