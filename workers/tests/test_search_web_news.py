"""search_web (SearXNG-backed): general web search was returning almost no freshness signal — the general engines (Bing/DuckDuckGo web) essentially never carry a publish date. Root-caused 2026-07-15 alongside a live engine-config audit (brave/wikidata/startpage/qwant all confirmed dead — 429/CAPTCHA/access- denied from their own servers, not our request volume) that also found Bing News/DuckDuckGo News/Google News already enabled upstream but never queried: querying categories=general,news in the same call, then surfacing whichever publish date exists, fixes both the coverage gap and the missing date."""

from __future__ import annotations

from typing import Self

import pytest

from app.modules.ai.research_tools import _tool_search_web


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self, payload: dict, *, captured: list) -> None:
        self._payload = payload
        self._captured = captured

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get(self, _url: str, params: tuple | None = None) -> _FakeResponse:
        self._captured.append(params)
        return _FakeResponse(self._payload)


def test_search_web_requests_general_and_news_categories(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list = []
    monkeypatch.setattr(
        "httpx.Client", lambda **_kw: _FakeClient({"results": []}, captured=captured)
    )
    monkeypatch.setattr("app.core.config.SEARXNG_URL", "http://127.0.0.1:8888")

    _tool_search_web("algorand nft marketplace")

    assert len(captured) == 1
    assert captured[0]["categories"] == "general,news"


def test_search_web_surfaces_published_date(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "results": [
            {"title": "Old general hit", "url": "https://a.example/", "content": "x"},
            {
                "title": "Dated news hit",
                "url": "https://b.example/",
                "content": "y",
                "publishedDate": "2026-07-14T10:00:00",
            },
        ]
    }
    monkeypatch.setattr("httpx.Client", lambda **_kw: _FakeClient(payload, captured=[]))
    monkeypatch.setattr("app.core.config.SEARXNG_URL", "http://127.0.0.1:8888")

    result = _tool_search_web("algorand nft marketplace")

    assert result["results"][0]["published_date"] == "2026-07-14T10:00:00"
    assert result["results"][1]["published_date"] is None


def test_search_web_ranks_dated_results_before_undated_when_truncating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dated result must not get pushed out by the `limit` truncation just because it happened to come later in SearXNG's own result order."""
    undated = [
        {"title": f"undated {i}", "url": f"https://u{i}.example/", "content": "x"} for i in range(5)
    ]
    dated = {
        "title": "the one dated hit",
        "url": "https://dated.example/",
        "content": "y",
        "publishedDate": "2026-07-14T10:00:00",
    }
    payload = {"results": [*undated, dated]}  # dated hit is LAST in raw order
    monkeypatch.setattr("httpx.Client", lambda **_kw: _FakeClient(payload, captured=[]))
    monkeypatch.setattr("app.core.config.SEARXNG_URL", "http://127.0.0.1:8888")

    result = _tool_search_web("algorand nft marketplace", limit=3)

    urls = [r["url"] for r in result["results"]]
    assert "https://dated.example/" in urls


def test_search_web_not_configured_without_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.core.config.SEARXNG_URL", "")

    result = _tool_search_web("anything")

    assert result["error"] == "web search not configured"
    assert result["results"] == []


def test_search_web_surfaces_suggestions(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "results": [],
        "suggestions": ["algorand nft marketplaces", "algo nft market"],
    }
    monkeypatch.setattr("httpx.Client", lambda **_kw: _FakeClient(payload, captured=[]))
    monkeypatch.setattr("app.core.config.SEARXNG_URL", "http://127.0.0.1:8888")

    result = _tool_search_web("algorand nft marketplce")

    assert result["suggestions"] == ["algorand nft marketplaces", "algo nft market"]


def test_search_web_omits_suggestions_key_when_none(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"results": [], "suggestions": []}
    monkeypatch.setattr("httpx.Client", lambda **_kw: _FakeClient(payload, captured=[]))
    monkeypatch.setattr("app.core.config.SEARXNG_URL", "http://127.0.0.1:8888")

    result = _tool_search_web("a well-formed query")

    assert "suggestions" not in result
