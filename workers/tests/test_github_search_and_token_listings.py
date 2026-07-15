"""New research tools requested by a stronger research model via suggest_tool
on a real CompX recompose (2026-07-14): github_repository_search (the model's
owner guess 404'd and it had no way to search GitHub by keyword) and
search_token_listings (confirm whether an ASA is actually tradeable instead
of assuming so from its supply)."""

from __future__ import annotations

import httpx

from app.modules.ai import research_tools
from app.modules.ai.research_tools import (
    _tool_github_repository_search,
    _tool_search_token_listings,
)
from app.modules.ai.research_tools import (
    research_tools as research_tools_fn,
)


def _json_response(url: str, status_code: int, payload) -> httpx.Response:
    return httpx.Response(
        status_code, json=payload, request=httpx.Request("GET", url)
    )


def test_github_repository_search_returns_candidates(monkeypatch):
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **kw: _json_response(
            url,
            200,
            {
                "total_count": 1,
                "items": [
                    {
                        "full_name": "someorg/compx-contracts",
                        "description": "CompX smart contracts",
                        "stargazers_count": 12,
                        "pushed_at": "2026-06-01T00:00:00Z",
                    }
                ],
            },
        ),
    )
    result = _tool_github_repository_search("CompX Algorand")
    assert result["total_count"] == 1
    assert result["results"] == [
        {
            "repo": "someorg/compx-contracts",
            "description": "CompX smart contracts",
            "stars": 12,
            "pushed_at": "2026-06-01T00:00:00Z",
        }
    ]


def test_github_repository_search_requires_nonempty_query():
    result = _tool_github_repository_search("")
    assert "error" in result


def test_github_repository_search_no_results_is_not_an_error(monkeypatch):
    monkeypatch.setattr(
        research_tools,
        "_guarded_get",
        lambda url, **kw: _json_response(url, 200, {"total_count": 0, "items": []}),
    )
    result = _tool_github_repository_search("compx-io")
    assert result["total_count"] == 0
    assert result["results"] == []
    assert "error" not in result


def test_github_repository_search_tool_registered():
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "github_repository_search" in names
    assert "github_repository_search" in handlers


def test_search_token_listings_reports_both_dexes(monkeypatch):
    def fake_get(url, **kw):
        if "tinyman" in url:
            return _json_response(
                url,
                200,
                {
                    "is_verified": True,
                    "liquidity_in_usd": "134.37",
                    "price_in_usd": None,
                    "last_day_volume_in_usd": "1.03",
                    "last_week_volume_in_usd": "5.72",
                },
            )
        return _json_response(
            url,
            200,
            {
                "results": [
                    {
                        "primary_asset": {"unit_name": "COMPX", "tvl_usd": "10.0"},
                        "secondary_asset": {"unit_name": "USDC"},
                    }
                ]
            },
        )

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_search_token_listings(1732165149)
    assert result["asset_id"] == 1732165149
    assert result["tinyman"]["listed"] is True
    assert result["tinyman"]["liquidity_usd"] == "134.37"
    assert result["pact"]["listed"] is True
    assert result["pact"]["pool_count"] == 1
    assert result["pact"]["pools"][0]["pair"] == "COMPX/USDC"


def test_search_token_listings_not_listed_on_tinyman(monkeypatch):
    def fake_get(url, **kw):
        if "tinyman" in url:
            return _json_response(url, 404, {})
        return _json_response(url, 200, {"results": []})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_search_token_listings(999)
    assert result["tinyman"] == {"listed": False}
    assert result["pact"]["listed"] is False
    assert result["pact"]["pool_count"] == 0


def test_search_token_listings_uses_pacts_real_filter_param(monkeypatch):
    """Regression-pin a real incident (2026-07-14/15): Pact's API silently
    ignores an unrecognized `asset_id` query param and returns its ENTIRE
    ~3900-pool listing instead of erroring — confirmed live, a COMPX query
    returned unrelated USDC/goUSD and ALGO/gALGO pools with count=3863,
    matching the platform total exactly. `primary_asset__on_chain_id` is
    the real filter (verified live to match the asset on either side of
    the pool, despite the name)."""
    captured_params: list = []

    def fake_get(url, **kw):
        if "tinyman" in url:
            return _json_response(url, 404, {})
        captured_params.append(kw.get("params"))
        return _json_response(url, 200, {"results": []})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    _tool_search_token_listings(1732165149)

    assert len(captured_params) == 1
    assert captured_params[0]["primary_asset__on_chain_id"] == "1732165149"
    assert "asset_id" not in captured_params[0]


def test_search_token_listings_requires_numeric_asset_id():
    result = _tool_search_token_listings("not-a-number")
    assert "error" in result


def test_search_token_listings_tool_registered():
    schemas, handlers = research_tools_fn()
    names = {s["function"]["name"] for s in schemas}
    assert "search_token_listings" in names
    assert "search_token_listings" in handlers
