"""search_bluesky: each post carries its own created_at, and results are engagement-ranked (sort=top), not chronological.

Regression tests (2026-09-08, Fable audit): the tool used to drop
`record.createdAt` from its output entirely, while being described (and
used elsewhere in the codebase, e.g. search_x's own docstring contrasting
against it) as returning "recent" posts -- with no date field, the writer
had no way to tell a months-old top-ranked post from an actually-recent one.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai import research_tools
from app.modules.ai.research_tools import _tool_search_bluesky


def test_search_bluesky_includes_created_at_per_post(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each returned post carries its own record.createdAt as created_at."""
    monkeypatch.setattr(research_tools, "_bsky_access_token", lambda: ("tok", ""))

    def fake_guarded_get(url: str, **_kw: object) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "posts": [
                    {
                        "record": {
                            "text": "gm algorand",
                            "createdAt": "2026-09-01T12:00:00.000Z",
                        },
                        "author": {"handle": "someone.bsky.social"},
                        "uri": "at://did:plc:abc/app.bsky.feed.post/xyz",
                        "likeCount": 5,
                    }
                ]
            },
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(research_tools, "_guarded_get", fake_guarded_get)
    out = _tool_search_bluesky("algorand")
    assert out["posts"][0]["created_at"] == "2026-09-01T12:00:00.000Z"


def test_search_bluesky_sorts_by_top_engagement_not_recency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual API call requests sort="top" -- confirms the docstring's own claim rather than assuming it."""
    monkeypatch.setattr(research_tools, "_bsky_access_token", lambda: ("tok", ""))
    captured: dict = {}

    def fake_guarded_get(url: str, *, params: dict | None = None, **_kw: object) -> httpx.Response:
        captured["params"] = params
        return httpx.Response(200, json={"posts": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(research_tools, "_guarded_get", fake_guarded_get)
    _tool_search_bluesky("algorand")
    assert captured["params"]["sort"] == "top"
