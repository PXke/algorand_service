"""Regression tests for the "empty is not none found" bug class (CLAUDE.md section 2.8).

A tool helper that hits an error must surface {"error": ...}, never silently
degrade to [] / "" that the writer reads as verified ground truth ("this repo
has no releases", "this forum has no categories", "Bluesky just isn't
configured" when it actually failed to authenticate).
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai import research_tools
from app.modules.ai.research_tools import (
    _bsky_access_token,
    _discourse_categories,
    _github_recent_commits,
    _github_releases,
    _github_top_contributors,
    _tool_discourse_forum,
    _tool_github_activity,
    _tool_search_bluesky,
)


def _raise_get(*_args: object, **_kwargs: object) -> httpx.Response:
    raise httpx.ConnectError("boom")


def test_github_releases_reports_error_not_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GitHub API failure surfaces as [{"error": ...}], not a bare []."""
    monkeypatch.setattr(research_tools, "_github_get", _raise_get)
    out = _github_releases("foo/bar", 5)
    assert out == [{"error": "boom"}]


def test_github_recent_commits_reports_error_not_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same fix as releases: a fetch failure must not read as "no commits"."""
    monkeypatch.setattr(research_tools, "_github_get", _raise_get)
    out = _github_recent_commits("foo/bar", 5)
    assert out == [{"error": "boom"}]


def test_github_top_contributors_reports_error_not_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same fix as releases: a fetch failure must not read as "no contributors"."""
    monkeypatch.setattr(research_tools, "_github_get", _raise_get)
    out = _github_top_contributors("foo/bar", 5)
    assert out == [{"error": "boom"}]


def test_github_activity_surfaces_partial_failure_instead_of_hiding_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repo whose metadata call succeeds but whose releases call fails.

    Must show that failure in the "releases" field, not a clean-looking [].
    """

    def fake_get(
        url: str,
        *,
        params: dict | None = None,  # noqa: ARG001
        timeout: float | None = None,  # noqa: ARG001
    ) -> httpx.Response:
        if url.endswith("/repos/foo/bar"):
            return httpx.Response(
                200,
                json={
                    "description": "d",
                    "stargazers_count": 1,
                    "pushed_at": "2026-01-01T00:00:00Z",
                    "archived": False,
                },
                request=httpx.Request("GET", url),
            )
        if url.endswith("/releases"):
            raise httpx.ConnectError("releases down")
        return httpx.Response(200, json=[], request=httpx.Request("GET", url))

    monkeypatch.setattr(research_tools, "_github_get", fake_get)
    out = _tool_github_activity("foo/bar")
    assert out["releases"] == [{"error": "releases down"}]
    # A genuinely-empty, successful call (commits/contributors here) must
    # still come back as a plain empty list, not swallowed into the same
    # error shape.
    assert out["recent_commits"] == []
    assert out["top_contributors"] == []


def test_discourse_categories_reports_error_not_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """A categories.json fetch failure surfaces as an error string, not silent []/{}."""
    monkeypatch.setattr(research_tools, "_guarded_get", _raise_get)
    categories, cat_names, error = _discourse_categories("https://forum.example", {})
    assert categories == []
    assert cat_names == {}
    assert error == "boom"


def test_discourse_forum_surfaces_categories_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """_tool_discourse_forum must propagate a categories fetch failure to the caller, mirroring how it already handles recent_topics' (data, error) shape."""

    def fake_about(base: str, hdr: dict) -> dict:  # noqa: ARG001
        return {"title": "Example Forum", "stats": {}}

    def fake_categories(base: str, hdr: dict) -> tuple[list, dict, str]:  # noqa: ARG001
        return [], {}, "categories endpoint 500"

    def fake_recent_topics(
        base: str,  # noqa: ARG001
        hdr: dict,  # noqa: ARG001
        n: int,  # noqa: ARG001
        cat_names: dict,  # noqa: ARG001
    ) -> tuple[list, str]:
        return [], ""

    monkeypatch.setattr(research_tools, "_discourse_about", fake_about)
    monkeypatch.setattr(research_tools, "_discourse_categories", fake_categories)
    monkeypatch.setattr(research_tools, "_discourse_recent_topics", fake_recent_topics)

    out = _tool_discourse_forum("https://forum.example")
    assert out["categories"] == []
    assert out["categories_error"] == "categories endpoint 500"


def test_bsky_access_token_distinguishes_unconfigured_from_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not-configured (no credentials) and an actual auth failure must be distinguishable, not both collapsed into a bare empty string."""
    # Not configured at all: both empty, no error.
    monkeypatch.delenv("BLUESKY_IDENTIFIER", raising=False)
    monkeypatch.delenv("BLUESKY_APP_PASSWORD", raising=False)
    token, error = _bsky_access_token()
    assert token == ""
    assert error == ""

    # Configured, but minting a session actually fails: must carry a real
    # error message the caller can distinguish from "not configured".
    monkeypatch.setenv("BLUESKY_IDENTIFIER", "someone.bsky.social")
    monkeypatch.setenv("BLUESKY_APP_PASSWORD", "app-password")
    research_tools._bsky_token_cache.clear()

    def fake_post(*_args: object, **_kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("bluesky is down")

    monkeypatch.setattr(httpx, "post", fake_post)
    token, error = _bsky_access_token()
    assert token == ""
    assert "bluesky is down" in error


def test_search_bluesky_reports_real_auth_failure_not_generic_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_tool_search_bluesky must surface the real token-mint error, not overwrite it with the generic "bluesky not configured" message."""
    monkeypatch.setattr(
        research_tools, "_bsky_access_token", lambda: ("", "session mint failed: 500")
    )
    out = _tool_search_bluesky("algorand")
    assert out["error"] == "session mint failed: 500"
    assert out["posts"] == []


def test_search_bluesky_retries_a_502_instead_of_failing_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-caused live 2026-08-28 (Lumi Rogue recompose, session 957f895a).

    bsky.social returned a straight 502 Bad Gateway on both of the writer's
    search_bluesky attempts, and the old bare `guarded_get` call had no
    retry, so the tool gave up on the FIRST 502 and the writer lost the
    community-sentiment angle entirely on a first-coverage story. 502 is a
    retryable status for every other external-API tool in this module
    (`_FETCH_RETRYABLE_STATUS`) -- search_bluesky must get the same policy
    via `_guarded_get_with_retry`, not a bespoke one-shot call.
    """
    monkeypatch.setattr(research_tools, "_bsky_access_token", lambda: ("tok", ""))
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    calls: list[dict] = []

    def fake_guarded_get(
        url: str,
        *,
        headers: dict | None = None,  # noqa: ARG001
        params: dict | None = None,
        timeout: float = 12.0,  # noqa: ARG001
    ) -> httpx.Response:
        calls.append({"url": url, "params": params})
        if len(calls) < 2:
            return httpx.Response(502, request=httpx.Request("GET", url))
        return httpx.Response(
            200,
            json={
                "posts": [
                    {
                        "record": {"text": "gm"},
                        "author": {"handle": "someone.bsky.social"},
                        "uri": "at://did:plc:abc/app.bsky.feed.post/xyz",
                        "likeCount": 1,
                    }
                ]
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(research_tools, "_guarded_get", fake_guarded_get)
    out = _tool_search_bluesky("Lumi Rogue")
    assert len(calls) == 2, "must retry once after the 502, not give up immediately"
    assert out["posts"][0]["author"] == "someone.bsky.social"
    assert "error" not in out
