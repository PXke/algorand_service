"""fetch_page/click_and_read now delegate to PlaywrightSession instead of each launching+tearing down its own throwaway sync_playwright()/chromium.launch() -- closes a "no copy of existing logic" violation (CLAUDE.md sec 3): browser_scrape.py used to have THREE different ways to get a Chromium browser going (fetch_page's own launch, click_and_read's own launch, and PlaywrightSession's reusable one).

Both functions now accept an optional playwright_session: when given, they
reuse it (and never close it -- the caller that created it owns that); with
none given, a short-lived session is created and closed internally, same net
observable behavior as the old one-shot launch.
"""

from __future__ import annotations

from typing import ClassVar

import pytest

from app.modules.scraper.core import browser_scrape
from app.modules.scraper.core.browser_scrape import BrowserPageResult, click_and_read, fetch_page


class _FakeSession:
    """Stands in for PlaywrightSession -- records construction/close/method calls with no real Playwright involved."""

    instances: ClassVar[list[_FakeSession]] = []

    def __init__(self, *, storage_state_path: str | None = None) -> None:
        self.storage_state_path = storage_state_path
        self.closed = False
        self.fetch_calls: list[tuple] = []
        self.click_calls: list[tuple] = []
        type(self).instances.append(self)

    def fetch(
        self,
        url: str,
        *,
        wait_after_load_ms: int | None = None,
        timeout_ms: int | None = None,
        skip_login_wall_check: bool = False,
    ) -> BrowserPageResult:
        self.fetch_calls.append((url, wait_after_load_ms, timeout_ms, skip_login_wall_check))
        return BrowserPageResult(
            title="Fetched",
            text="x" * 100,
            final_url=url,
            engine="playwright-session",
        )

    def click_and_read(
        self,
        url: str,
        click_text: str,
        *,
        wait_after_click_ms: int = 1500,
        timeout_ms: int | None = None,
    ) -> BrowserPageResult:
        self.click_calls.append((url, click_text, wait_after_click_ms, timeout_ms))
        return BrowserPageResult(
            title="Clicked",
            text="y" * 100,
            final_url=url,
            engine="playwright-session-click",
        )

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _reset_fake_session_registry() -> None:
    _FakeSession.instances = []
    yield
    _FakeSession.instances = []


@pytest.fixture(autouse=True)
def _no_real_dns_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """click_and_read's SSRF guard resolves DNS for real -- stub it so these tests never touch the network."""
    monkeypatch.setattr("app.core.net_guard.assert_public_url", lambda url: url)


# --------------------------------------------------------------------------- #
# fetch_page
# --------------------------------------------------------------------------- #


def test_fetch_page_standalone_creates_and_closes_its_own_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No session passed in: fetch_page still works end to end -- a short-lived session is created, used once, and closed, so the caller sees no behavior change."""
    monkeypatch.setattr(browser_scrape, "PlaywrightSession", _FakeSession)

    result = fetch_page("https://example.com/page")

    assert result.text == "x" * 100
    assert result.final_url == "https://example.com/page"
    assert len(_FakeSession.instances) == 1
    owned = _FakeSession.instances[0]
    assert owned.fetch_calls == [("https://example.com/page", None, None, False)]
    assert owned.closed is True


def test_fetch_page_with_shared_session_reuses_it_and_never_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-supplied session is reused (its .fetch() is called), not re-launched, and fetch_page never closes a session it didn't create -- that stays the owning caller's job."""
    monkeypatch.setattr(browser_scrape, "PlaywrightSession", _FakeSession)
    shared = _FakeSession()
    # Constructing it above already registered it once; fetch_page must not
    # construct a second one.
    assert len(_FakeSession.instances) == 1

    result = fetch_page("https://example.com/page", playwright_session=shared)

    assert result.text == "x" * 100
    # still exactly the one session the caller made -- no throwaway launched
    assert len(_FakeSession.instances) == 1
    assert shared.fetch_calls == [("https://example.com/page", None, None, False)]
    assert shared.closed is False


def test_fetch_page_forwards_its_kwargs_to_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """wait_after_load_ms/timeout_ms/skip_login_wall_check reach the underlying session.fetch() call unchanged."""
    monkeypatch.setattr(browser_scrape, "PlaywrightSession", _FakeSession)
    shared = _FakeSession()

    fetch_page(
        "https://example.com",
        wait_after_load_ms=1234,
        timeout_ms=5678,
        skip_login_wall_check=True,
        playwright_session=shared,
    )

    assert shared.fetch_calls == [("https://example.com", 1234, 5678, True)]


def test_fetch_page_n_calls_through_one_shared_session_launch_exactly_one_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point: N fetch_page calls sharing one session must construct exactly ONE PlaywrightSession (one Chromium launch), not N."""
    monkeypatch.setattr(browser_scrape, "PlaywrightSession", _FakeSession)
    shared = _FakeSession()
    assert len(_FakeSession.instances) == 1  # the one the caller just made

    for i in range(5):
        fetch_page(f"https://example.com/{i}", playwright_session=shared)

    assert len(_FakeSession.instances) == 1  # still just the one -- no per-call launch
    assert len(shared.fetch_calls) == 5
    assert shared.closed is False


# --------------------------------------------------------------------------- #
# click_and_read
# --------------------------------------------------------------------------- #


def test_click_and_read_standalone_creates_and_closes_its_own_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No session passed in: click_and_read still works end to end via a short-lived, self-closed session."""
    monkeypatch.setattr(browser_scrape, "PlaywrightSession", _FakeSession)

    result = click_and_read("https://example.com", "About")

    assert result.text == "y" * 100
    assert len(_FakeSession.instances) == 1
    owned = _FakeSession.instances[0]
    assert owned.click_calls == [("https://example.com", "About", 1500, None)]
    assert owned.closed is True


def test_click_and_read_with_shared_session_reuses_it_and_never_closes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-supplied session is reused for the click, not re-launched, and is left open for the caller to close."""
    monkeypatch.setattr(browser_scrape, "PlaywrightSession", _FakeSession)
    shared = _FakeSession()
    assert len(_FakeSession.instances) == 1

    result = click_and_read("https://example.com", "About", playwright_session=shared)

    assert result.text == "y" * 100
    assert len(_FakeSession.instances) == 1
    assert shared.click_calls == [("https://example.com", "About", 1500, None)]
    assert shared.closed is False


def test_click_and_read_n_calls_through_one_shared_session_launch_exactly_one_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N click_and_read calls sharing one session must construct exactly ONE PlaywrightSession."""
    monkeypatch.setattr(browser_scrape, "PlaywrightSession", _FakeSession)
    shared = _FakeSession()
    assert len(_FakeSession.instances) == 1

    for i in range(4):
        click_and_read(f"https://example.com/{i}", "About", playwright_session=shared)

    assert len(_FakeSession.instances) == 1
    assert len(shared.click_calls) == 4
    assert shared.closed is False
