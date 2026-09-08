"""search_leak_databases scrapes ICIJ Offshore Leaks' HTML search (no official API) -- a blocked/challenged response must not read as a confirmed zero-hit result."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai.investigative_tools import search_leak_databases


def test_a_wired_success_page_reports_real_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ordinary path: a 200 HTML search-results page with entity rows."""
    html = (
        "<table><tr><td><a href='/nodes/12345'>Acme Holdings Ltd</a></td></tr>"
        "<tr><td><a href='/nodes/67890'>Acme Trust</a></td></tr></table>"
    )
    resp = httpx.Response(
        200, text=html, request=httpx.Request("GET", "https://offshoreleaks.icij.org/search")
    )
    monkeypatch.setattr(
        "app.modules.ai.investigative_tools._guarded_get",
        lambda *_a, **_kw: resp,
    )
    result = search_leak_databases("Acme")
    assert result["hits"] == 2
    assert "Acme Holdings Ltd" in result["entities"]


def test_a_waf_challenge_202_reports_an_error_not_zero_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression (2026-09-08, Fable audit, confirmed live): ICIJ's AWS WAF edge answers every bare HTTP client with a 202 and an empty body -- that used to parse as an empty results table and report hits: 0, indistinguishable from a real zero-hit answer."""
    resp = httpx.Response(
        202, text="", request=httpx.Request("GET", "https://offshoreleaks.icij.org/search")
    )
    monkeypatch.setattr(
        "app.modules.ai.investigative_tools._guarded_get",
        lambda *_a, **_kw: resp,
    )
    result = search_leak_databases("Anyone")
    assert "error" in result
    assert "hits" not in result


def test_a_non_200_status_reports_an_error_not_zero_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-200 status (even with some body) is never trusted as a real results page."""
    resp = httpx.Response(
        503,
        text="<html>service unavailable</html>",
        request=httpx.Request("GET", "https://offshoreleaks.icij.org/search"),
    )
    monkeypatch.setattr(
        "app.modules.ai.investigative_tools._guarded_get",
        lambda *_a, **_kw: resp,
    )
    result = search_leak_databases("Anyone")
    assert "error" in result
    assert "hits" not in result


def test_a_genuine_empty_results_table_still_reports_zero_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real 200 search page that just has no matching rows is a confirmed zero, not an error."""
    html = "<html><body><table></table><p>No results found.</p></body></html>"
    resp = httpx.Response(
        200, text=html, request=httpx.Request("GET", "https://offshoreleaks.icij.org/search")
    )
    monkeypatch.setattr(
        "app.modules.ai.investigative_tools._guarded_get",
        lambda *_a, **_kw: resp,
    )
    result = search_leak_databases("Nobody Special")
    assert result["hits"] == 0
    assert "error" not in result
