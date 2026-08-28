"""PlaywrightSession._read_page must use the same main/article-landmark-preferring, dedup'd extraction as the module-level fetch_page/click_and_read (_extract_visible_text), not a raw page.inner_text("body") dump.

Root-caused 2026-08-28 while unifying fetch_page/click_and_read onto
PlaywrightSession: the session's own _read_page had silently diverged from
the standalone extraction path (a second, worse-quality copy of "get the
page's text" -- see _extract_visible_text's own docstring for the
2026-08-17 footer-duplication incident this exists to avoid), which would
have regressed fetch_page/click_and_read's output quality the moment they
started delegating to the session. Unifying on _extract_visible_text here is
what makes that delegation safe.
"""

from __future__ import annotations

from app.modules.scraper.core.browser_scrape import PlaywrightSession

_LONG_ENOUGH = "x" * 101  # _extract_visible_text skips chunks under 100 chars


class _FakeLocator:
    def __init__(self, text: str) -> None:
        self._text = text

    def count(self) -> int:
        return 1 if self._text else 0

    @property
    def first(self) -> _FakeLocator:
        return self

    def inner_text(self, timeout: int = 2000) -> str:  # noqa: ARG002 -- matches real Locator signature
        return self._text


class _EmptyLocator:
    def count(self) -> int:
        return 0


class _FakePage:
    """A page whose <main> landmark holds the real content, while a raw body dump would ALSO carry a duplicated footer block six times over -- the exact shape _extract_visible_text exists to avoid."""

    def __init__(self, main_text: str, body_text: str) -> None:
        self._main_text = main_text
        self._body_text = body_text
        self.url = "https://example.com/article"

    def evaluate(self, _script: str) -> None:
        pass  # _expand_collapsed_content's accordion-expand -- no-op for this fake

    def wait_for_timeout(self, _ms: int) -> None:
        pass

    def title(self) -> str:
        return "Article Title"

    def locator(self, selector: str) -> _FakeLocator | _EmptyLocator:
        if selector == "main":
            return _FakeLocator(self._main_text)
        return _EmptyLocator()

    def inner_text(self, _selector: str) -> str:
        return self._body_text

    def content(self) -> str:
        return "<html></html>"


def _make_session() -> PlaywrightSession:
    """A PlaywrightSession with no real Playwright/Chromium behind it -- _read_page only touches self._storage_state_path and the page it's handed, so a bare instance (bypassing __init__'s real launch) is enough."""
    session = object.__new__(PlaywrightSession)
    session._storage_state_path = ""  # noqa: SLF001 -- test constructs the instance directly
    return session


def test_read_page_prefers_the_main_landmark_over_a_raw_body_dump() -> None:
    main_text = f"Real article content, the thing a visitor actually reads. {_LONG_ENOUGH}"
    # A raw body dump would repeat the footer block, unrelated to the actual
    # article content -- if _read_page ever regresses to page.inner_text("body"),
    # this assertion catches it.
    body_text = "Footer. Join Discord. Follow Bluesky. " * 6
    page = _FakePage(main_text, body_text)
    session = _make_session()

    result = session._read_page(page, engine="playwright-session")  # noqa: SLF001

    assert "Real article content" in result.text
    assert "Join Discord" not in result.text


def test_read_page_falls_back_to_body_when_no_landmark_matches() -> None:
    page = _FakePage("", "whole body text, long enough to clear the floor. " + _LONG_ENOUGH)
    session = _make_session()

    result = session._read_page(page, engine="playwright-session")  # noqa: SLF001

    assert "whole body text" in result.text
