"""fetch_google_doc reads a publicly-shared Google Doc's plain text via its export endpoint -- fetch_url alone only sees the JS editor shell loading."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai.research_tools import _tool_fetch_google_doc


def test_requires_url() -> None:
    """An empty url is a usage error, not a fetch attempt."""
    result = _tool_fetch_google_doc("")
    assert "error" in result


def test_rejects_a_non_google_docs_url() -> None:
    """A url that isn't a docs.google.com/document/d/... link is a clear usage error."""
    result = _tool_fetch_google_doc("https://example.com/whitepaper")
    assert "error" in result


def test_reads_the_export_endpoint_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """Happy path: extracts the doc id, hits the txt export endpoint, and returns the sliced plain text."""
    resp = httpx.Response(
        200,
        text="Section 1: Tokenomics\n\nTotal supply is 1,000,000,000 tokens.",
        request=httpx.Request(
            "GET",
            "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/export?format=txt",
        ),
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get_with_retry", lambda *_a, **_kw: resp,
    )
    result = _tool_fetch_google_doc(
        "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit?usp=sharing"
    )
    assert "Total supply is 1,000,000,000 tokens." in result["text"]
    assert result["url"] == "https://docs.google.com/document/d/1AbCdEfGhIjKlMnOp/edit?usp=sharing"


def test_private_doc_reports_plainly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A doc that isn't shared publicly 401s or redirects to a Google login page -- reported as a clear 'not public' error, not an empty/garbled result."""
    resp = httpx.Response(
        401,
        text="",
        request=httpx.Request(
            "GET", "https://docs.google.com/document/d/1PrivateDocId/export?format=txt"
        ),
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get_with_retry", lambda *_a, **_kw: resp,
    )
    result = _tool_fetch_google_doc("https://docs.google.com/document/d/1PrivateDocId/edit")
    assert "not publicly viewable" in result["error"]


def test_paginates_like_fetch_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long doc slices into windows with has_more/scroll metadata, same shape as fetch_url."""
    long_text = "word " * 5000
    resp = httpx.Response(
        200,
        text=long_text,
        request=httpx.Request(
            "GET", "https://docs.google.com/document/d/1LongDocId/export?format=txt"
        ),
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get_with_retry", lambda *_a, **_kw: resp,
    )
    result = _tool_fetch_google_doc("https://docs.google.com/document/d/1LongDocId/edit", max_chars=1000)
    assert result["has_more"] is True
    assert result["scroll"]["continue_reading"] is True


def test_fetch_google_doc_tool_registered() -> None:
    """Registers fetch_google_doc in both the tool schemas and handlers."""
    from app.modules.ai import research_tools

    schemas, handlers = research_tools.research_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "fetch_google_doc" in names
    assert "fetch_google_doc" in handlers
