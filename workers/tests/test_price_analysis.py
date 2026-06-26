from __future__ import annotations

import pytest

from app.modules.newspaper.price_analysis import (
    WeeklyPriceSnapshot,
    compose_weekly_price_article,
    fetch_weekly_price,
)


def test_compose_weekly_price_article() -> None:
    snap = WeeklyPriceSnapshot(
        asset_id="algorand",
        asset_name="Algorand",
        currency="USD",
        price_usd=0.25,
        week_open_usd=0.20,
        week_high_usd=0.26,
        week_low_usd=0.19,
        week_change_pct=25.0,
        as_of=__import__("datetime").datetime(2026, 6, 2, 12, 0, tzinfo=__import__("datetime").UTC),
    )
    title, summary, body = compose_weekly_price_article(snap)
    assert "Algorand" in title
    assert "weekly" in title.lower() or "price" in title.lower()
    assert "+25.00%" in body
    assert summary


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
