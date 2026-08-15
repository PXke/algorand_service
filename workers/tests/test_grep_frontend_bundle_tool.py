"""grep_frontend_bundle searches a page's own JS bundles for a literal string -- for verifying SPA behavior (a wallet-connect requirement, a fee constant) that never renders as text fetch_url can read.

Root-caused 2026-07-24 (AlgoRank incident): an article claimed a dApp needed no wallet to vote based on the rendered page; the live JS bundle actually required a wallet-connect call, and no tool existed to check the bundle directly.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai.research_tools import _tool_grep_frontend_bundle


def _html_response(url: str, html: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "text/html"},
        content=html.encode(),
        request=httpx.Request("GET", url),
    )


def _js_response(url: str, body: str) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"content-type": "application/javascript"},
        content=body.encode(),
        request=httpx.Request("GET", url),
    )


def test_requires_url() -> None:
    """An empty url is a usage error, not a fetch attempt."""
    result = _tool_grep_frontend_bundle("", "connectWallet")
    assert "error" in result


def test_requires_search_term() -> None:
    """An empty search_term is a usage error, not a fetch attempt."""
    result = _tool_grep_frontend_bundle("https://example.com", "")
    assert "error" in result


def test_finds_a_match_in_a_bundle_with_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: the term is found in a linked script, with surrounding context and the source script URL."""
    page = _html_response(
        "https://example.com/",
        '<html><head><script src="/assets/app.js"></script></head></html>',
    )
    bundle = _js_response(
        "https://example.com/assets/app.js",
        "function vote(x){if(!wallet.connected){requireWalletConnect();}return submit(x)}",
    )

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        if url.endswith("app.js"):
            return bundle
        return page

    monkeypatch.setattr("app.modules.ai.research_tools._guarded_get_with_retry", fake_get)
    result = _tool_grep_frontend_bundle("https://example.com", "requireWalletConnect")
    assert result["match_count"] == 1
    assert "requireWalletConnect" in result["matches"][0]["context"]
    assert result["matches"][0]["script_url"] == "https://example.com/assets/app.js"


def test_no_match_across_bundles_is_reported_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A clean miss across every bundle is informative (the term isn't in the client code), not a failure."""
    page = _html_response(
        "https://example.com/",
        '<html><head><script src="/assets/app.js"></script></head></html>',
    )
    bundle = _js_response("https://example.com/assets/app.js", "function submit(x){return x}")

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        if url.endswith("app.js"):
            return bundle
        return page

    monkeypatch.setattr("app.modules.ai.research_tools._guarded_get_with_retry", fake_get)
    result = _tool_grep_frontend_bundle("https://example.com", "requireWalletConnect")
    assert result["match_count"] == 0
    assert result["matches"] == []
    assert result["scripts_checked"] == ["https://example.com/assets/app.js"]


def test_skips_known_third_party_tracker_scripts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Analytics/ad scripts never carry app logic and would crowd out real bundles under the script cap -- excluded before fetching."""
    page = _html_response(
        "https://example.com/",
        '<html><head>'
        '<script src="https://www.googletagmanager.com/gtag/js"></script>'
        '<script src="/assets/app.js"></script>'
        '</head></html>',
    )
    bundle = _js_response("https://example.com/assets/app.js", "requireWalletConnect()")
    fetched: list[str] = []

    def fake_get(url: str, **_kw: object) -> httpx.Response:
        fetched.append(url)
        if url.endswith("app.js"):
            return bundle
        return page

    monkeypatch.setattr("app.modules.ai.research_tools._guarded_get_with_retry", fake_get)
    result = _tool_grep_frontend_bundle("https://example.com", "requireWalletConnect")
    assert "https://www.googletagmanager.com/gtag/js" not in fetched
    assert result["scripts_checked"] == ["https://example.com/assets/app.js"]


def test_no_scripts_on_page_returns_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page with no <script src=...> tags at all can't be grepped -- a clear error, not a silent empty result."""
    page = _html_response("https://example.com/", "<html><body>No scripts here.</body></html>")
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get_with_retry", lambda *_a, **_kw: page,
    )
    result = _tool_grep_frontend_bundle("https://example.com", "anything")
    assert "error" in result
