"""Click-based crawling for SPA domains href-following can't reach real content on."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.modules.crawler.interactive_crawl import _synthetic_click_url, crawl_interactively


def test_synthetic_click_url_is_stable_and_slugified() -> None:
    """Same (entry_url, click_text) must always produce the same pseudo-URL (page_id_for_url hashes it, so a repeat interactive crawl updates the same stored row instead of accumulating a new one every run)."""
    url = _synthetic_click_url("https://lumirogue.com", "Try the demo (tutorial)")
    assert url == "https://lumirogue.com#interactive:try-the-demo-tutorial"
    assert url == _synthetic_click_url("https://lumirogue.com", "Try the demo (tutorial)")


def test_synthetic_click_url_strips_trailing_slash_on_entry() -> None:
    """A trailing slash on entry_url mustn't produce a double-slash before the fragment."""
    assert _synthetic_click_url("https://lumirogue.com/", "About").endswith(
        "lumirogue.com#interactive:about"
    )


@dataclass
class _FakeResult:
    title: str = "A Screen"
    text: str = "Some real content from clicking."


@dataclass
class _FakeSession:
    clickable: list[str]
    fail_on: set[str] = field(default_factory=set)
    opened: list[str] = field(default_factory=list)
    clicked: list[str] = field(default_factory=list)
    closed: bool = False

    def interactive_open(self, url: str, **_kw: object) -> _FakeResult:
        self.opened.append(url)
        return _FakeResult()

    def interactive_clickable_texts(self, limit: int = 25) -> list[str]:
        return self.clickable[:limit]

    def interactive_click(self, click_text: str, **_kw: object) -> _FakeResult:
        from app.modules.scraper.core.browser_scrape import BrowserScrapeError

        self.clicked.append(click_text)
        if click_text in self.fail_on:
            raise BrowserScrapeError(f"no element matching {click_text!r}")
        return _FakeResult(title=f"Screen for {click_text}", text=f"Content after {click_text}")

    def close(self) -> None:
        self.closed = True


def _install_fake_session(monkeypatch: pytest.MonkeyPatch, fake: _FakeSession) -> None:
    monkeypatch.setattr("app.modules.scraper.core.browser_scrape.PlaywrightSession", lambda: fake)


def _install_fake_indexer(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    stored: list[dict] = []

    def fake_index_crawled_page(**kwargs: object) -> dict[str, str]:
        stored.append(kwargs)
        return {"status": "indexed"}

    monkeypatch.setattr(
        "app.modules.search.tasks.index_tasks.index_crawled_page", fake_index_crawled_page
    )
    return stored


def test_crawl_interactively_clicks_every_discovered_element_and_stores_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every clickable text the entry page reports gets clicked from a fresh copy of the entry page and its result handed to index_crawled_page."""
    fake = _FakeSession(clickable=["Try the demo (tutorial)", "Rankings", "About this project"])
    _install_fake_session(monkeypatch, fake)
    stored = _install_fake_indexer(monkeypatch)

    n = crawl_interactively("https://lumirogue.com", service_id="lumirogue-com")

    assert n == 3
    assert fake.clicked == ["Try the demo (tutorial)", "Rankings", "About this project"]
    # Re-opens the entry page before EVERY click -- each click samples from
    # the same baseline state, not a chain (1 initial open + 1 per click).
    assert fake.opened.count("https://lumirogue.com") == 4
    assert len(stored) == 3
    assert stored[0]["url"] == "https://lumirogue.com#interactive:try-the-demo-tutorial"
    assert stored[0]["text"] == "Content after Try the demo (tutorial)"
    assert fake.closed is True


def test_crawl_interactively_skips_a_failed_click_but_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale/vanished click target must not abort the rest of the exploration."""
    fake = _FakeSession(clickable=["Rankings", "Ghost Button", "About"], fail_on={"Ghost Button"})
    _install_fake_session(monkeypatch, fake)
    stored = _install_fake_indexer(monkeypatch)

    n = crawl_interactively("https://lumirogue.com", service_id="lumirogue-com")

    assert n == 2  # Rankings and About stored; Ghost Button's failure skipped
    assert len(stored) == 2


def test_crawl_interactively_respects_max_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """More clickable elements than max_steps must not all get clicked -- this is exploration, not exhaustive traversal."""
    fake = _FakeSession(clickable=[f"Button {i}" for i in range(10)])
    _install_fake_session(monkeypatch, fake)
    _install_fake_indexer(monkeypatch)

    n = crawl_interactively("https://lumirogue.com", service_id="svc", max_steps=3)

    assert n == 3
    assert len(fake.clicked) == 3


def test_crawl_interactively_returns_zero_when_playwright_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing Playwright install degrades to 'explored nothing', never raises into the crawl pipeline."""

    def _raise_import_error() -> None:
        raise ImportError("playwright not installed")

    monkeypatch.setattr(
        "app.modules.scraper.core.browser_scrape.PlaywrightSession", _raise_import_error
    )
    assert crawl_interactively("https://lumirogue.com", service_id="svc") == 0


def test_crawl_interactively_returns_zero_when_open_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed initial navigation to the entry page degrades to 'explored nothing', not a raised exception."""

    class _FailsToOpen:
        def interactive_open(self, _url: str, **_kw: object) -> None:
            raise RuntimeError("navigation timed out")

        def close(self) -> None:
            pass

    monkeypatch.setattr("app.modules.scraper.core.browser_scrape.PlaywrightSession", _FailsToOpen)
    assert crawl_interactively("https://lumirogue.com", service_id="svc") == 0
