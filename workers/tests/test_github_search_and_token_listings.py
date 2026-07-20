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


def test_owner_liveness_flags_active_owner(monkeypatch):
    """An archived repo under an owner still pushing to other repos = superseded,
    not defunct (the Pera Wallet case)."""
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(UTC) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
    monkeypatch.setattr(research_tools, "_github_owner_repos", lambda owner: {
        "owner": owner,
        "repos": [
            {"repo": "perawallet/pera-wallet", "pushed_at": "2024-08-26T00:00:00Z",
             "stars": 200, "archived": True},
            {"repo": "perawallet/pera-react-native", "pushed_at": recent,
             "stars": 40, "archived": False},
        ],
    })
    out = research_tools._owner_liveness("perawallet", exclude="perawallet/pera-wallet")
    assert "OWNER STILL ACTIVE" in out["verdict"]
    assert out["active_repos"] and out["active_repos"][0]["repo"] == "perawallet/pera-react-native"


def test_owner_liveness_dormant_when_no_recent(monkeypatch):
    monkeypatch.setattr(research_tools, "_github_owner_repos", lambda owner: {
        "owner": owner,
        "repos": [{"repo": "x/old", "pushed_at": "2020-01-01T00:00:00Z",
                   "stars": 1, "archived": False}],
    })
    out = research_tools._owner_liveness("x")
    assert "dormant" in out["verdict"].lower()
    assert out["active_repos"] == []


def test_github_activity_archived_repo_attaches_owner_liveness(monkeypatch):
    """An archived repo returns owner_liveness so the writer can't conclude the
    project is dead from the archived flag alone."""
    from datetime import UTC, datetime, timedelta

    recent = (datetime.now(UTC) - timedelta(days=2)).isoformat().replace("+00:00", "Z")

    def fake_get(url, **kw):
        if url.endswith("/repos/perawallet/pera-wallet"):
            return _json_response(url, 200, {"description": "old monorepo",
                                             "stargazers_count": 200,
                                             "pushed_at": "2024-08-26T00:00:00Z",
                                             "archived": True})
        if "/users/perawallet/repos" in url:
            return _json_response(url, 200, [
                {"full_name": "perawallet/pera-react-native", "description": "app",
                 "stargazers_count": 40, "pushed_at": recent, "archived": False},
            ])
        # releases / commits / contributors
        return _json_response(url, 200, [])

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    out = research_tools._tool_github_activity("perawallet/pera-wallet")
    assert out["archived"] is True
    assert "owner_liveness" in out
    assert "OWNER STILL ACTIVE" in out["owner_liveness"]["verdict"]


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


def test_github_get_retries_unauthenticated_when_token_rejected(monkeypatch):
    """Root-caused 2026-07-16: the prod GITHUB_TOKEN expired, and GitHub
    answers 401 to ANY request carrying a revoked token — so every github_*
    tool call started failing verbatim ('401 Unauthorized' straight into the
    research trace of the isitalgorandsbirthday.com compose), even though the
    same request would have SUCCEEDED unauthenticated (rate-limited harder,
    but working). A dead token must degrade to anonymous access, not take the
    whole tool family down."""
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_expired_token")
    calls: list[dict] = []

    def fake_get(url, **kw):
        # Copy — _github_get mutates the same dict in place for the retry.
        calls.append(dict(kw.get("headers") or {}))
        if "Authorization" in (kw.get("headers") or {}):
            return _json_response(url, 401, {"message": "Bad credentials"})
        return _json_response(
            url,
            200,
            {"total_count": 1, "items": [{"full_name": "real/repo"}]},
        )

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_github_repository_search("algorand birthday")
    assert len(calls) == 2
    assert "Authorization" in calls[0]
    assert "Authorization" not in calls[1]
    assert result["results"][0]["repo"] == "real/repo"
    assert "error" not in result


def test_github_get_no_retry_without_token(monkeypatch):
    # Anonymous 401 (should not happen, but) must not double the request.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    calls: list[str] = []

    def fake_get(url, **kw):
        calls.append(url)
        return _json_response(url, 401, {"message": "nope"})

    monkeypatch.setattr(research_tools, "_guarded_get", fake_get)
    result = _tool_github_repository_search("whatever")
    assert len(calls) == 1
    assert "error" in result


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
