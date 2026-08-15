"""extract_pdf_from_page finds a PDF's real file URL behind a JS viewer (Google Docs viewer, PDF.js), then reads it — fetch_url alone only sees the wrapper page's chrome in that case."""

from __future__ import annotations

import httpx
import pytest

from app.modules.ai.research_tools import (
    _find_pdf_url_in_html,
    _tool_extract_pdf_from_page,
)


def test_find_pdf_url_prefers_google_docs_viewer_param() -> None:
    """A docs.google.com/viewer wrapper's url= param is the real file, and takes priority over any other .pdf-looking href on the same page (e.g. a nav link to an unrelated PDF)."""
    html = (
        '<a href="/other-doc.pdf">Other</a>'
        '<iframe src="https://docs.google.com/viewer?embedded=true&url='
        'https%3A%2F%2Fcgap.org%2Fdecks%2Fstablecoins.pdf"></iframe>'
    )
    result = _find_pdf_url_in_html(html, "https://cgap.org/reading/")
    assert result == "https://cgap.org/decks/stablecoins.pdf"


def test_find_pdf_url_falls_back_to_a_direct_pdf_href() -> None:
    """No viewer wrapper present — falls back to the first direct .pdf href/src, resolved to an absolute URL."""
    html = '<a href="/files/report.pdf">Download</a>'
    result = _find_pdf_url_in_html(html, "https://example.com/reading/")
    assert result == "https://example.com/files/report.pdf"


def test_find_pdf_url_returns_none_when_nothing_matches() -> None:
    """A page with no PDF link at all returns None, not a false match."""
    assert _find_pdf_url_in_html("<p>No documents here.</p>", "https://example.com/") is None


def test_extract_pdf_from_page_requires_url() -> None:
    """An empty url is a usage error."""
    result = _tool_extract_pdf_from_page("")
    assert "error" in result


def test_extract_pdf_from_page_handles_a_url_that_is_already_a_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When url itself is a direct PDF (content-type: application/pdf), parse it immediately without any viewer-discovery step."""
    resp = httpx.Response(
        200,
        headers={"content-type": "application/pdf"},
        content=b"%PDF-1.4 fake",
        request=httpx.Request("GET", "https://example.com/doc.pdf"),
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get_with_retry", lambda *a, **kw: resp,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._fetch_pdf_document",
        lambda _resp, **kw: {"url": kw["base"], "text": "extracted"},
    )
    result = _tool_extract_pdf_from_page("https://example.com/doc.pdf")
    assert result["text"] == "extracted"


def test_extract_pdf_from_page_finds_and_fetches_the_wrapped_pdf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wrapper page's HTML holds a discoverable PDF link — fetches it and returns extracted text plus found_pdf_url."""
    wrapper_resp = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        content=b'<a href="/decks/stablecoins.pdf">Read</a>',
        request=httpx.Request("GET", "https://cgap.org/reading/"),
    )
    pdf_resp = httpx.Response(
        200,
        headers={"content-type": "application/pdf"},
        content=b"%PDF-1.4 fake",
        request=httpx.Request("GET", "https://cgap.org/decks/stablecoins.pdf"),
    )
    calls = []

    def fake_get(url: str, **kw: object) -> httpx.Response:  # noqa: ARG001
        calls.append(url)
        return wrapper_resp if url == "https://cgap.org/reading/" else pdf_resp

    monkeypatch.setattr("app.modules.ai.research_tools._guarded_get_with_retry", fake_get)
    monkeypatch.setattr(
        "app.modules.ai.research_tools._fetch_pdf_document",
        lambda _resp, **kw: {"url": kw["base"], "text": "deck contents"},
    )
    result = _tool_extract_pdf_from_page("https://cgap.org/reading/")
    assert result["found_pdf_url"] == "https://cgap.org/decks/stablecoins.pdf"
    assert result["text"] == "deck contents"
    assert calls == ["https://cgap.org/reading/", "https://cgap.org/decks/stablecoins.pdf"]


def test_extract_pdf_from_page_falls_back_to_rendered_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No PDF link in the raw HTML (viewer injected client-side) — retries with a Playwright-rendered copy before giving up."""
    from app.modules.scraper.core.browser_scrape import BrowserPageResult

    wrapper_resp = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        content=b"<p>Loading viewer...</p>",
        request=httpx.Request("GET", "https://cgap.org/reading/"),
    )
    pdf_resp = httpx.Response(
        200,
        headers={"content-type": "application/pdf"},
        content=b"%PDF-1.4 fake",
        request=httpx.Request("GET", "https://cgap.org/decks/stablecoins.pdf"),
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get_with_retry",
        lambda url, **kw: wrapper_resp if "reading" in url else pdf_resp,  # noqa: ARG005
    )
    rendered = BrowserPageResult(
        title="Reading",
        text="x" * 100,
        final_url="https://cgap.org/reading/",
        engine="playwright",
        html='<iframe src="/decks/stablecoins.pdf"></iframe>',
    )
    monkeypatch.setattr(
        "app.modules.scraper.core.browser_scrape.fetch_page", lambda *a, **kw: rendered,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._fetch_pdf_document",
        lambda _resp, **kw: {"url": kw["base"], "text": "deck contents"},
    )
    result = _tool_extract_pdf_from_page("https://cgap.org/reading/")
    assert result["found_pdf_url"] == "https://cgap.org/decks/stablecoins.pdf"


def test_extract_pdf_from_page_errors_clearly_when_no_pdf_found_anywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither the raw nor rendered HTML has a discoverable PDF link — a clear error, not a crash or empty success."""
    wrapper_resp = httpx.Response(
        200,
        headers={"content-type": "text/html"},
        content=b"<p>Nothing here.</p>",
        request=httpx.Request("GET", "https://example.com/"),
    )
    monkeypatch.setattr(
        "app.modules.ai.research_tools._guarded_get_with_retry", lambda *a, **kw: wrapper_resp,  # noqa: ARG005
    )
    monkeypatch.setattr(
        "app.modules.scraper.core.browser_scrape.fetch_page",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("render failed")),  # noqa: ARG005
    )
    result = _tool_extract_pdf_from_page("https://example.com/")
    assert "error" in result
    assert "found_pdf_url" not in result


def test_extract_pdf_from_page_tool_registered() -> None:
    """Registers extract_pdf_from_page in both the tool schemas and handlers."""
    from app.modules.ai.research_tools import research_tools

    schemas, handlers = research_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "extract_pdf_from_page" in names
    assert "extract_pdf_from_page" in handlers
