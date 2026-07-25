"""Fetching the weekly price snapshot."""

from __future__ import annotations

from typing import Self

import pytest

from app.modules.newspaper.price_analysis import fetch_weekly_price


def test_fetch_weekly_price_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Builds a weekly price snapshot with the latest price and week-over-week change from mocked HTTP responses."""
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
