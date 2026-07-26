"""Tool-registry behaviors driven by prod compose-session findings.

- fetch_url failures on hosts with a dedicated tool must steer the writer there
  via a `hint` in the error payload (the model routed around medium/reddit tools
  and hit 403 walls instead).
- entity-background OSINT tools register only for investigative story lanes;
  query_corporate_registry additionally needs its API token (401 without it).
- github_activity given a bare owner/org must list repos, not error.
"""

from typing import Any, ClassVar

import pytest

from app.modules.ai.research_tools import (
    _fetch_failure_hint,
    _tool_fetch_url,
    _tool_github_activity,
)
from app.modules.ai.writer_tools import all_tools


def _names(schemas: list[dict]) -> set[str]:
    return {s["function"]["name"] for s in schemas}


def test_fetch_hint_steers_medium_and_reddit_to_dedicated_tools() -> None:
    """fetch_url failure hints steer the writer to dedicated tools for Medium/GitHub and admit no Reddit data exists."""
    assert "medium_api_article_list" in _fetch_failure_hint(
        "https://medium.com/@author/post-123", "Client error '403 Forbidden'"
    )
    # reddit tool phased out 2026-07-16 (server IP is hard-blocked): the hint
    # must say no reddit data exists rather than steer to a tool that 403s.
    reddit_hint = _fetch_failure_hint(
        "https://www.reddit.com/r/algorand/comments/x/", "Client error '403 Blocked'"
    )
    assert "reddit_api_post_history" not in reddit_hint
    assert "no reddit data" in reddit_hint
    assert "github_activity" in _fetch_failure_hint(
        "https://github.com/algorand/go-algorand", "Client error '403 rate limited'"
    )


def test_reddit_tool_is_not_offered_but_stub_answers_without_network() -> None:
    """Phased out 2026-07-16: reddit hard-blocks this server's IP, so offering the tool burned one guaranteed-403 call per compose session. No schema is registered anymore; the stub handler stays for stale references and must answer without any network round-trip."""
    from app.modules.ai.research_tools import _tool_reddit_history
    from app.modules.ai.research_tools import research_tools as research_tools_fn

    schemas, handlers = research_tools_fn()
    assert "reddit_api_post_history" not in _names(schemas)
    assert "reddit_api_post_history" in handlers
    result = _tool_reddit_history("someuser")
    assert "reddit blocks" in result["error"]
    assert result["items"] == []


def test_fetch_hint_suggests_archive_for_gone_pages_only() -> None:
    """fetch_url failure hints suggest the archive tool only for gone (404) pages, not other failures."""
    assert "fetch_archive_text" in _fetch_failure_hint(
        "https://algonaut.space", "Client error '404 Not Found'"
    )
    assert _fetch_failure_hint("https://example.com", "timeout") == ""


def test_entity_osint_tools_gated_to_investigative_lanes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entity-background OSINT tools are gated to investigative topics (scam_alert), absent from the generic lane."""
    monkeypatch.setenv("OPENCORPORATES_API_TOKEN", "tok")
    generic, _ = all_tools(topic="generic")
    scam, _ = all_tools(topic="scam_alert")
    ungated, _ = all_tools()  # no topic: legacy callers keep everything

    osint = {
        "screen_sanctions_and_pep",
        "query_corporate_registry",
        "query_court_dockets",
        "search_leak_databases",
    }
    assert not (osint & _names(generic))
    assert osint <= _names(scam)
    assert osint <= _names(ungated)
    # Archive/infra tools stay available on every lane.
    assert "fetch_archive_text" in _names(generic)


def test_corporate_registry_needs_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """query_corporate_registry is only registered when its API token is configured."""
    monkeypatch.delenv("OPENCORPORATES_API_TOKEN", raising=False)
    schemas, handlers = all_tools(topic="scam_alert")
    assert "query_corporate_registry" not in _names(schemas)
    assert "query_corporate_registry" not in handlers
    assert "search_leak_databases" in _names(schemas)


def test_github_activity_bare_owner_lists_repos(monkeypatch: pytest.MonkeyPatch) -> None:
    """github_activity given a bare owner name lists their public repos."""
    import app.modules.ai.research_tools as rt

    class _Resp:
        status_code = 200

        @staticmethod
        def raise_for_status() -> None:
            return None

        @staticmethod
        def json() -> list[dict[str, str | int]]:
            return [
                {
                    "full_name": "AlgoNode/nodely-docs",
                    "description": "docs",
                    "stargazers_count": 5,
                    "pushed_at": "2026-06-30T00:00:00Z",
                }
            ]

    seen = {}

    def fake_get(url: str, **_kwargs: object) -> Any:  # noqa: ANN401 -- test double / fake response
        seen["url"] = url
        return _Resp()

    monkeypatch.setattr(rt, "_guarded_get", fake_get)
    out = _tool_github_activity("AlgoNode")
    assert seen["url"].endswith("/users/AlgoNode/repos")
    assert out["repos"][0]["repo"] == "AlgoNode/nodely-docs"
    assert "owner/name" in out["hint"]


def test_fetch_url_escalates_to_browser_on_thin_spa_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """A React/Vue shell (or a 'please enable JavaScript' fallback page) reads as ~empty over plain HTTP — the tool must retry with the Playwright renderer instead of reporting the shell as the page's real content."""
    import app.modules.ai.research_tools as rt
    from app.modules.scraper.core.base import ScrapeResult

    class _Resp:
        status_code = 200
        headers: ClassVar[dict[str, str]] = {"content-type": "text/html"}
        url = "https://example.com/play"
        text = '<html><body><div id="root"></div><script src="app.js"></script></body></html>'

        @staticmethod
        def raise_for_status() -> None:
            return None

    monkeypatch.setattr(rt, "_guarded_get", lambda *_a, **_k: _Resp())
    monkeypatch.setattr("app.modules.scraper.crawler_registry.is_web_spa_enabled", lambda: True)

    rendered = ScrapeResult(
        source_id="research-fetch_url",
        url="https://example.com/play",
        title="Rendered Title",
        text="Full rendered article body.",
        content_hash="x",
        links=[{"text": "a link", "url": "https://example.com/a"}],
    )

    class _FakeBrowserScraper:
        def scrape(self, _url: str, _source_id: str) -> ScrapeResult:
            return rendered

    monkeypatch.setattr(
        "app.modules.scraper.core.browser_scraper.BrowserScraper", _FakeBrowserScraper
    )

    out = _tool_fetch_url("https://example.com/play")
    assert out["title"] == "Rendered Title"
    assert out["text"] == "Full rendered article body."
