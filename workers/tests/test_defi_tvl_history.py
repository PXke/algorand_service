"""get_defi_tvl_history: a tool-gap suggestion from a real compose (Algomint, 2026-08-07 -- "For Algomint's wind-down narrative I could only anchor its decline with Messari's single Q2 2025 TVL figure... A per-protocol TVL history... would have let me chart the actual decline curve instead of inferring it from two points"). get_defi_tvl only ever gives today's number; this adds DeFiLlama's historical series, downsampled to one point per month."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai import research_tools
from app.modules.ai.research_tools import _tool_get_defi_tvl_history
from app.modules.ai.research_tools import research_tools as research_tools_fn


def _json_response(url: str, status_code: int, payload: dict) -> httpx.Response:
    return httpx.Response(status_code, json=payload, request=httpx.Request("GET", url))


def _protocol_payload(daily_points: list[dict]) -> dict:
    return {"chainTvls": {"Algorand": {"tvl": daily_points}}}


def test_downsamples_to_one_point_per_month(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple daily points in the same calendar month collapse to the LAST day seen that month."""
    daily = [
        {"date": 1704067200, "totalLiquidityUSD": 100},  # 2024-01-01
        {"date": 1706745599, "totalLiquidityUSD": 150},  # 2024-01-31 (last Jan point)
        {"date": 1706745600, "totalLiquidityUSD": 200},  # 2024-02-01
    ]
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **_kw: _json_response(url, 200, _protocol_payload(daily)),
    )
    result = _tool_get_defi_tvl_history("algomint")

    assert result["monthly_tvl_usd"] == [
        {"month": "2024-01", "tvl_usd": 150},
        {"month": "2024-02", "tvl_usd": 200},
    ]
    assert result["current_tvl_usd"] == 200
    assert result["peak_tvl_usd"] == 200
    assert result["source"] == "DeFiLlama"


def test_months_param_caps_trailing_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the most recent `months` monthly points are returned, oldest first."""
    daily = [
        {"date": 1698796800, "totalLiquidityUSD": 10},  # 2023-11
        {"date": 1701388800, "totalLiquidityUSD": 20},  # 2023-12
        {"date": 1704067200, "totalLiquidityUSD": 30},  # 2024-01
    ]
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **_kw: _json_response(url, 200, _protocol_payload(daily)),
    )
    result = _tool_get_defi_tvl_history("algomint", months=2)

    assert [m["month"] for m in result["monthly_tvl_usd"]] == ["2023-12", "2024-01"]


def test_falls_back_to_top_level_series_when_no_algorand_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A protocol whose payload has no chainTvls.Algorand entry still works via the top-level tvl series."""
    payload = {
        "chainTvls": {"Ethereum": {"tvl": []}},
        "tvl": [{"date": 1704067200, "totalLiquidityUSD": 500}],
    }
    monkeypatch.setattr(
        research_tools, "_guarded_get", lambda url, **_kw: _json_response(url, 200, payload)
    )
    result = _tool_get_defi_tvl_history("some-multichain-protocol")
    assert result["current_tvl_usd"] == 500


def test_unknown_protocol_returns_clean_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """DeFiLlama answers an unknown protocol slug with 400 (not 404, unlike the sibling /tvl endpoint) -- verified live against the real API 2026-08-07."""
    monkeypatch.setattr(
        research_tools, "_guarded_get", lambda url, **_kw: _json_response(url, 400, {})
    )
    result = _tool_get_defi_tvl_history("definitely-not-a-real-protocol-xyz")
    assert result["error"] == "not found on DeFiLlama — try the slug, e.g. 'tinyman'"


def test_requires_nonempty_protocol() -> None:
    """Rejects an empty protocol with an error, no network call -- unlike get_defi_tvl, there is no chain-wide mode here."""
    result = _tool_get_defi_tvl_history("")
    assert "error" in result


def test_network_error_is_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network exception is caught and returned as an error, never raised."""

    def raise_error(_url: str, **_kw: object) -> httpx.Response:
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(research_tools, "_guarded_get", raise_error)
    result = _tool_get_defi_tvl_history("algomint")
    assert "error" in result


def test_empty_history_is_a_clean_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A protocol with no TVL data at all (empty series) is a clean error, not a crash or an empty-but-ok result."""
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **_kw: _json_response(url, 200, _protocol_payload([])),
    )
    result = _tool_get_defi_tvl_history("brand-new-protocol")
    assert "error" in result


def test_tool_registered() -> None:
    """get_defi_tvl_history is registered as a tool schema and handler."""
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "get_defi_tvl_history" in names
    assert "get_defi_tvl_history" in handlers


def test_schema_requires_protocol() -> None:
    """Declares protocol as required (no chain-wide default, unlike get_defi_tvl)."""
    schemas, _handlers = research_tools_fn()
    schema = next(s for s in schemas if s["function"]["name"] == "get_defi_tvl_history")
    assert schema["function"]["parameters"]["required"] == ["protocol"]
