"""lookup_asset_market_data: live price/volume/TVL for specific Algorand ASAs, via Vestige's aggregator API. Added 2026-08-18 after a rug.ninja recompose invented a whole liquidity dataset with zero grounding — there was no tool that could answer a per-token liquidity/volume/TVL question at all (get_defi_tvl only covers protocol-level TVL, not individual coins)."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai import research_tools
from app.modules.ai.research_tools import _tool_lookup_asset_market_data
from app.modules.ai.research_tools import research_tools as research_tools_fn


def _json_response(url: str, status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


_TINY_RESULT = {
    "rank": 16,
    "id": 2200000000,
    "name": "TINY",
    "ticker": "TINY",
    "price": 0.010024,
    "price1d": 0.010016,
    "volume1d": 10925.147292,
    "volume7d": 152139.085862,
    "swaps1d": 267,
    "market_cap": 9907130.105515989,
    "tvl": 1180145.1124642517,
}


def test_requires_asset_ids() -> None:
    """Empty asset_ids is a usage error, not an API call."""
    result = _tool_lookup_asset_market_data("")
    assert "error" in result


def test_rejects_too_many_ids() -> None:
    """More than 25 ids is rejected before any request is made."""
    result = _tool_lookup_asset_market_data(",".join(str(i) for i in range(30)))
    assert "error" in result


def test_happy_path_single_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    """A known asset id returns price/volume/tvl."""

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        return _json_response(url, 200, {"count": 1, "results": [_TINY_RESULT]})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_lookup_asset_market_data("2200000000")
    assert "error" not in result
    assert len(result["assets"]) == 1
    asset = result["assets"][0]
    assert asset["asset_id"] == 2200000000
    assert asset["ticker"] == "TINY"
    assert asset["tvl_usd"] == 1180145.1124642517
    assert asset["volume_1d_usd"] == 10925.147292


def test_forwards_comma_separated_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple ids are joined and sent as one request, not one call per id."""
    seen_params: list[dict] = []

    def fake_get(url: str, **kw: object) -> httpx.Response:
        seen_params.append(kw.get("params") or {})
        return _json_response(url, 200, {"count": 1, "results": [_TINY_RESULT]})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    _tool_lookup_asset_market_data("2200000000, 31566704")
    assert len(seen_params) == 1
    assert seen_params[0]["asset_ids"] == "2200000000,31566704"


def test_untracked_asset_reports_error_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """An id Vestige has no data for (never traded on a tracked DEX) is a clean miss, not a crash."""

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        return _json_response(url, 200, {"count": 0, "results": []})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_lookup_asset_market_data("999999999")
    assert result["assets"] == []
    assert "error" in result


def test_partial_miss_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """When only some of the requested ids come back, the missing ones are surfaced rather than silently dropped."""

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        return _json_response(url, 200, {"count": 1, "results": [_TINY_RESULT]})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_lookup_asset_market_data("2200000000,999999999")
    assert len(result["assets"]) == 1
    assert result["missing_asset_ids"] == ["999999999"]


def test_network_failure_reports_error_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network/HTTP failure degrades to an error dict, never an unhandled exception."""

    def fake_get(_url: str, **_kw: object) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_lookup_asset_market_data("2200000000")
    assert "error" in result


def test_tool_registered() -> None:
    """Registers lookup_asset_market_data in both the tool schemas and handlers."""
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "lookup_asset_market_data" in names
    assert "lookup_asset_market_data" in handlers
