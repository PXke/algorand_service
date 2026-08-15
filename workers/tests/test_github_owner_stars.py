"""_github_owner_repos root cause (2026-08-10, live incident): its 8-repo 'repos' list is sorted by recency, not stars, so a real org-wide claim drew on only the 8 most recently pushed repos out of many more — chopmob-cloud/AlgoVoi's 8-repo recent window genuinely showed 0 stars each, while 4 different, less-recently-touched repos elsewhere in its 112 total had 1 star each. total_public_repos / total_stars_across_all_repos close that gap."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai.research_tools import _github_owner_repos


def _repos_response(url: str, repos: list[dict]) -> httpx.Response:
    return httpx.Response(200, json=repos, request=httpx.Request("GET", url))


def test_github_owner_repos_reports_org_wide_star_total_not_just_the_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression-pin the actual chopmob-cloud incident shape: an 8-repo recent window all showing 0 stars, while the true org-wide total (from paginating everything) is nonzero."""
    profile = httpx.Response(
        200, json={"public_repos": 10}, request=httpx.Request("GET", "https://api.github.com/users/chopmob-cloud")
    )
    recent_8 = [{"full_name": f"chopmob-cloud/recent-{i}", "stargazers_count": 0, "pushed_at": "2026-08-10T00:00:00Z", "archived": False} for i in range(8)]
    all_10 = [
        *recent_8,
        {"full_name": "chopmob-cloud/use-wallet-algovoi", "stargazers_count": 1, "pushed_at": "2026-08-07T00:00:00Z"},
        {"full_name": "chopmob-cloud/substrate-comparisons", "stargazers_count": 1, "pushed_at": "2026-08-04T00:00:00Z"},
    ]

    def fake_get(url: str, *, params: dict | None = None, timeout: float | None = None) -> httpx.Response:  # noqa: ARG001
        if url.endswith("/users/chopmob-cloud"):
            return profile
        if params and params.get("sort") == "pushed":
            return _repos_response(url, recent_8)
        return _repos_response(url, all_10)

    monkeypatch.setattr("app.modules.ai.research_tools._github_get", fake_get)

    result = _github_owner_repos("chopmob-cloud")

    assert result["total_public_repos"] == 10
    assert all(r["stars"] == 0 for r in result["repos"])
    assert result["total_stars_across_all_repos"] == 2
    assert result["total_stars_may_be_incomplete"] is False
    assert "do NOT generalize" in result["hint"]


def test_github_owner_repos_flags_incomplete_star_total_for_a_huge_org(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An org with more repos than the page cap covers gets an honest lower-bound flag, not a false-precision total."""
    profile = httpx.Response(
        200, json={"public_repos": 500}, request=httpx.Request("GET", "https://api.github.com/users/huge-org")
    )
    one_page = [
        {"full_name": f"huge-org/r{i}", "stargazers_count": 1, "pushed_at": "2026-08-10T00:00:00Z", "archived": False}
        for i in range(100)
    ]

    def fake_get(url: str, *, params: dict | None = None, timeout: float | None = None) -> httpx.Response:  # noqa: ARG001
        if url.endswith("/users/huge-org"):
            return profile
        return _repos_response(url, one_page)

    monkeypatch.setattr("app.modules.ai.research_tools._github_get", fake_get)

    result = _github_owner_repos("huge-org")

    assert result["total_stars_may_be_incomplete"] is True


def test_github_owner_repos_missing_owner_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 on the profile lookup surfaces as a clean error, not a crash."""

    def fake_get(url: str, *, params: dict | None = None, timeout: float | None = None) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.modules.ai.research_tools._github_get", fake_get)

    result = _github_owner_repos("nonexistent-owner-xyz")

    assert "error" in result
