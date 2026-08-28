"""Click-based crawling for SPA domains href-following can't reach real content on."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from conftest import FakeRedis

from app.modules.crawler.interactive_crawl import (
    _homepage_url,
    _synthetic_click_url,
    crawl_interactively,
    maybe_trigger_interactive_crawl,
)


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


def test_homepage_url_derives_from_any_page_on_the_domain() -> None:
    """Whichever specific page triggered the check, exploration starts from the domain's homepage -- confirmed on lumirogue.com that the real interactive content sits behind homepage buttons, not any deep path."""
    assert _homepage_url("https://lumirogue.com/?view=gungi") == "https://lumirogue.com/"
    assert _homepage_url("https://www.ex.io/deep/path?x=1") == "https://www.ex.io/"


def _stub_diversity(
    monkeypatch: pytest.MonkeyPatch, *, needs_crawl: bool
) -> list[tuple[str, dict]]:
    dispatched: list[tuple[str, dict]] = []

    class _FakeTask:
        def delay(self, **kwargs: object) -> None:
            dispatched.append(("dispatched", kwargs))

    monkeypatch.setattr(
        "app.modules.crawler.crawled_page_store.domain_content_diversity",
        lambda _domain, **_kw: object(),
    )
    monkeypatch.setattr(
        "app.modules.crawler.crawled_page_store.needs_interactive_crawl", lambda _d: needs_crawl
    )
    monkeypatch.setattr(
        "app.modules.crawler.tasks.interactive_crawl_tasks.run_interactive_crawl_task",
        _FakeTask(),
    )
    return dispatched


def test_maybe_trigger_interactive_crawl_dispatches_when_needed(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001
) -> None:
    """A domain flagged by needs_interactive_crawl gets an async interactive-crawl task dispatched, starting from its homepage regardless of which specific page triggered the check."""
    monkeypatch.setattr("app.core.config.INTERACTIVE_CRAWL_ENABLED", True)
    dispatched = _stub_diversity(monkeypatch, needs_crawl=True)

    triggered = maybe_trigger_interactive_crawl(
        "https://lumirogue.com/?view=gungi", service_id="lumirogue-com"
    )

    assert triggered is True
    assert dispatched == [
        (
            "dispatched",
            {"entry_url": "https://lumirogue.com/", "service_id": "lumirogue-com"},
        )
    ]


def test_maybe_trigger_interactive_crawl_does_nothing_when_not_needed(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001
) -> None:
    """A domain whose diversity signal doesn't cross the threshold gets no dispatch."""
    monkeypatch.setattr("app.core.config.INTERACTIVE_CRAWL_ENABLED", True)
    dispatched = _stub_diversity(monkeypatch, needs_crawl=False)

    triggered = maybe_trigger_interactive_crawl("https://ex.io/about", service_id="ex-io")

    assert triggered is False
    assert dispatched == []


def test_maybe_trigger_interactive_crawl_respects_cooldown(
    monkeypatch: pytest.MonkeyPatch,
    patch_redis_from_url: FakeRedis,  # noqa: ARG001
) -> None:
    """A second check for the same domain within the cooldown window must not re-dispatch (or even re-compute diversity) -- the Redis claim is a single atomic rate limit for both."""
    monkeypatch.setattr("app.core.config.INTERACTIVE_CRAWL_ENABLED", True)
    dispatched = _stub_diversity(monkeypatch, needs_crawl=True)

    first = maybe_trigger_interactive_crawl("https://lumirogue.com/a", service_id="lumirogue-com")
    second = maybe_trigger_interactive_crawl("https://lumirogue.com/b", service_id="lumirogue-com")

    assert first is True
    assert second is False
    assert len(dispatched) == 1


def test_maybe_trigger_interactive_crawl_disabled_short_circuits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled via config: no Redis, no Cassandra, no dispatch -- doesn't even need patch_redis_from_url to pass."""
    monkeypatch.setattr("app.core.config.INTERACTIVE_CRAWL_ENABLED", False)
    assert maybe_trigger_interactive_crawl("https://lumirogue.com", service_id="svc") is False


def test_maybe_trigger_interactive_crawl_fails_open_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An infra hiccup here must not fail the page-storage path it's piggybacking on (CLAUDE.md invariant 2.9)."""
    monkeypatch.setattr("app.core.config.INTERACTIVE_CRAWL_ENABLED", True)

    def _raise() -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.core.redis_client.get_redis", _raise)
    assert maybe_trigger_interactive_crawl("https://lumirogue.com", service_id="svc") is False
