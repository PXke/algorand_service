"""type_into_page types into a search/filter field and returns the page's content afterward — for content only reachable by typing (explorer search, docs search, filter forms)."""

from __future__ import annotations

from app.modules.ai.research_tools import _tool_type_into_page
from app.modules.scraper.core.browser_scrape import BrowserPageResult, BrowserScrapeError


class _FakeSession:
    def __init__(
        self, result: BrowserPageResult | None = None, error: Exception | None = None
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[tuple[str, str, str, bool]] = []

    def type_and_read(
        self, url: str, field_text: str, value: str, *, submit: bool = False
    ) -> BrowserPageResult:
        self.calls.append((url, field_text, value, submit))
        if self._error:
            raise self._error
        assert self._result is not None
        return self._result


def test_type_into_page_requires_url() -> None:
    """An empty url is a usage error, not a fetch attempt."""
    result = _tool_type_into_page("", "Search", "algo")
    assert "error" in result


def test_type_into_page_requires_field_text() -> None:
    """An empty field_text is a usage error, not a fetch attempt."""
    result = _tool_type_into_page("https://example.com", "", "algo")
    assert "error" in result


def test_type_into_page_requires_a_browser_session() -> None:
    """No playwright_session means no browser is available -- fail clearly rather than silently no-op."""
    result = _tool_type_into_page("https://example.com", "Search", "algo")
    assert "error" in result
    assert "session" in result["error"]


def test_type_into_page_returns_post_type_content() -> None:
    """Happy path: type_and_read succeeds, its result is shaped into the tool's public output."""
    page = BrowserPageResult(
        title="Explorer",
        text="Address ABC123 holds 42 ALGO",
        final_url="https://explorer.example/search?q=ABC123",
        engine="playwright-session-type",
    )
    session = _FakeSession(result=page)
    result = _tool_type_into_page(
        "explorer.example", "Search address", "ABC123", submit=True, playwright_session=session
    )
    assert result["title"] == "Explorer"
    assert result["typed_into"] == "Search address"
    assert result["submitted"] is True
    assert "42 ALGO" in result["text"]
    assert session.calls == [("https://explorer.example", "Search address", "ABC123", True)]


def test_type_into_page_surfaces_no_match_error_with_hint() -> None:
    """When no field matches, type_and_read's error (listing what fields WERE present) passes through."""
    err = BrowserScrapeError(
        "no input/textarea/select matching 'Nonexistent' found -- visible fields on the page include: ['Search', 'Email']"
    )
    session = _FakeSession(error=err)
    result = _tool_type_into_page(
        "https://example.com", "Nonexistent", "x", playwright_session=session
    )
    assert "error" in result
    assert "Search" in result["error"]


def test_type_into_page_tool_registered() -> None:
    """Registers type_into_page in both the tool schemas and handlers."""
    from app.modules.ai.research_tools import research_tools

    schemas, handlers = research_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "type_into_page" in names
    assert "type_into_page" in handlers
