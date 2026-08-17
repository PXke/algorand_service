"""_extract_visible_text must not concatenate the same landmark content twice.

Multiple selectors matching the same (or a nested) element -- root-caused live 2026-08-17 on
lumirogue.com, where a shared footer block appeared six times over in the material handed to
compose.
"""

from __future__ import annotations

from app.modules.scraper.core.browser_scrape import _extract_visible_text


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
    """Maps each landmark selector to whatever text it should "match".

    Mirrors a real page where e.g. `<main role="main" id="content">` matches three different
    selectors at once.
    """

    def __init__(self, selector_text: dict[str, str]) -> None:
        self._selector_text = selector_text

    def locator(self, selector: str) -> _FakeLocator | _EmptyLocator:
        text = self._selector_text.get(selector, "")
        return _FakeLocator(text) if text else _EmptyLocator()

    def inner_text(self, selector: str) -> str:  # noqa: ARG002 -- body fallback, unused in these tests
        return ""


_LONG_ENOUGH = "x" * 101  # selectors below the 100-char floor are skipped entirely


def test_same_element_matching_two_selectors_is_not_duplicated() -> None:
    """<main role="main"> matches BOTH `main` and `[role='main']` -- must be counted once."""
    shared_text = f"Real article content here. {_LONG_ENOUGH}"
    page = _FakePage({"main": shared_text, "[role='main']": shared_text})

    result = _extract_visible_text(page)

    assert result.count("Real article content here.") == 1


def test_nested_narrower_selector_is_not_duplicated() -> None:
    """#content nested INSIDE <main> -- main's text already contains it, so it must be skipped."""
    inner = f"Footer block repeated content. {_LONG_ENOUGH}"
    outer = f"Header. {inner} More body text padding to clear the length floor too. {_LONG_ENOUGH}"
    page = _FakePage({"main": outer, "#content": inner})

    result = _extract_visible_text(page)

    assert result.count("Footer block repeated content.") == 1


def test_genuinely_distinct_landmarks_are_both_kept() -> None:
    """Distinct landmark content must survive -- the fix must not over-dedupe unrelated sections."""
    main_text = f"Main landmark content. {_LONG_ENOUGH}"
    article_text = f"Article landmark content, totally different. {_LONG_ENOUGH}"
    page = _FakePage({"main": main_text, "article": article_text})

    result = _extract_visible_text(page)

    assert "Main landmark content." in result
    assert "Article landmark content, totally different." in result


def test_falls_back_to_body_when_no_landmark_matches() -> None:
    """No landmark selector matches anything -- falls back to the whole body's text."""
    page = _FakePage({})
    page.inner_text = lambda selector: "whole body text"  # noqa: ARG005 -- test stub override

    result = _extract_visible_text(page)

    assert result == "whole body text"
