from __future__ import annotations

import pytest

from app.modules.newspaper.price_analysis import fetch_weekly_price


def test_fetch_weekly_price_mocked(monkeypatch) -> None:
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
                    "prices": [
                        [1, 0.20],
                        [2, 0.22],
                        [3, 0.25],
                    ]
                }
            )

    monkeypatch.setattr("app.modules.newspaper.price_analysis.httpx.Client", FakeClient)
    snap = fetch_weekly_price("algorand")
    assert snap.asset_name == "Algorand"
    assert snap.price_usd == 0.25
    assert snap.week_change_pct == pytest.approx(25.0)
