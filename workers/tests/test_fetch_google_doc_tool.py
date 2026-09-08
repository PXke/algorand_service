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
        "app.modules.ai.research_tools._guarded_get_with_retry",
        lambda *_a, **_kw: resp,
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
        "app.modules.ai.research_tools._guarded_get_with_retry",
        lambda *_a, **_kw: resp,
    )
    result = _tool_fetch_google_doc("https://docs.google.com/document/d/1PrivateDocId/edit")
    assert "not publicly viewable" in result["error"]


def test_paginates_via_its_own_offset_param(monkeypatch: pytest.MonkeyPatch) -> None:
    """A long doc slices into windows and hands back a usable next_offset -- not fetch_url's continue_reading, which this tool doesn't accept.

    Regression (2026-09-08, Fable audit): the schema promises a caller can
    "call again with offset=next_offset", but the result used to strip
    _next_offset and inject a scroll hint pointing at fetch_url instead --
    the model had no number to actually continue with, and the wrong tool
    name besides.
    """
    long_text = "word " * 5000
    resp = httpx.Response(
        200,
        text=long_text,
        request=httpx.Request(
            "GET", "https://docs.google.com/document/d/1LongDocId/export?format=txt"
        ),
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get_with_retry",
        lambda *_a, **_kw: resp,
    )
    result = _tool_fetch_google_doc(
        "https://docs.google.com/document/d/1LongDocId/edit", max_chars=1000
    )
    assert result["has_more"] is True
    assert result["next_offset"] == 1000
    assert "scroll" not in result
    assert "_next_offset" not in result

    cont = _tool_fetch_google_doc(
        "https://docs.google.com/document/d/1LongDocId/edit",
        max_chars=1000,
        offset=result["next_offset"],
    )
    assert cont["text"] == long_text[1000:2000]


def test_no_next_offset_key_when_document_is_fully_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """next_offset stays absent (not null) once has_more is false, matching this codebase's field-absent-means-nothing-more convention."""
    resp = httpx.Response(
        200,
        text="short doc",
        request=httpx.Request(
            "GET", "https://docs.google.com/document/d/1ShortDocId/export?format=txt"
        ),
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get_with_retry",
        lambda *_a, **_kw: resp,
    )
    result = _tool_fetch_google_doc("https://docs.google.com/document/d/1ShortDocId/edit")
    assert result["has_more"] is False
    assert "next_offset" not in result


def test_fetch_google_doc_tool_registered() -> None:
    """Registers fetch_google_doc in both the tool schemas and handlers."""
    from app.modules.ai import research_tools

    schemas, handlers = research_tools.research_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "fetch_google_doc" in names
    assert "fetch_google_doc" in handlers
