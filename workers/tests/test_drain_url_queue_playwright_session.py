"""drain_url_queue shares ONE PlaywrightSession across its whole per-tick item loop, instead of each item's browser-fallback hit launching its own throwaway Chromium.

Root-caused 2026-08-28: browser_scrape.py had three different ways to get a
Chromium browser going -- fetch_page's own one-shot launch, click_and_read's
own one-shot launch, and PlaywrightSession's reusable one (already used by
writer tools during a compose). drain_url_queue's beat fires every
URL_QUEUE_DRAIN_SECONDS (as low as 10s) and can process up to max_items (as
high as 10) per tick, each potentially hitting the SPA fallback -- up to
max_items separate ~2-5s/~300MB Chromium launches per tick, a real
contributor to the scrape-pool saturation incident referenced in
celery_app.py. See browser_scrape.py's fetch_page/click_and_read
playwright_session param and PlaywrightSession itself.
"""

from __future__ import annotations

import pytest

from app.modules.crawler.tasks.url_queue_tasks import drain_url_queue


class _FakeSession:
    """Records close() without touching real Playwright."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeDriver:
    """Records every playwright_session it was handed, across all items."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def scrape_from_queue_item(self, item: dict, *, playwright_session: object = None) -> dict:
        self.calls.append((item["url"], playwright_session))
        return {"status": "ok", "url": item["url"]}


def _queue(urls: list[str]) -> list[dict]:
    return [{"url": u, "queue_id": f"q-{i}"} for i, u in enumerate(urls)]


def test_drain_url_queue_shares_one_session_across_all_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """3 dequeued items in one tick must all be handed the SAME session object -- exactly one maybe_start_session() call for the whole batch, not one per item."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    pending = _queue(["https://svc.example/a", "https://svc.example/b", "https://svc.example/c"])
    monkeypatch.setattr(uq, "dequeue_url", lambda: pending.pop(0) if pending else None)
    monkeypatch.setattr(uq, "pending_url_count", lambda: 0)

    driver = _FakeDriver()
    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: driver)

    sessions_started: list[_FakeSession] = []

    def _fake_maybe_start_session() -> _FakeSession:
        session = _FakeSession()
        sessions_started.append(session)
        return session

    monkeypatch.setattr(uq, "maybe_start_session", _fake_maybe_start_session)

    out = drain_url_queue(max_items=5)

    assert out["processed"] == 3
    # exactly ONE browser session for the whole tick, not up to 3
    assert len(sessions_started) == 1
    the_session = sessions_started[0]
    # every item's scrape_from_queue_item got that SAME session, never None
    # and never a fresh one
    assert [s for _url, s in driver.calls] == [the_session, the_session, the_session]
    # closed exactly once, after the whole batch finished
    assert the_session.closed is True


def test_drain_url_queue_closes_the_shared_session_even_if_an_item_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mid-batch exception in one item's scrape must not leak the shared browser -- close() still runs via the enclosing try/finally."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    pending = _queue(["https://svc.example/a", "https://svc.example/b"])
    monkeypatch.setattr(uq, "dequeue_url", lambda: pending.pop(0) if pending else None)
    monkeypatch.setattr(uq, "pending_url_count", lambda: 0)

    sessions_started: list[_FakeSession] = []

    def _fake_maybe_start_session() -> _FakeSession:
        session = _FakeSession()
        sessions_started.append(session)
        return session

    monkeypatch.setattr(uq, "maybe_start_session", _fake_maybe_start_session)

    class _RaisingDriver:
        def scrape_from_queue_item(self, item: dict, *, playwright_session: object = None) -> dict:  # noqa: ARG002
            raise RuntimeError("boom mid-batch")

    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: _RaisingDriver())

    with pytest.raises(RuntimeError, match="boom mid-batch"):
        drain_url_queue(max_items=5)

    assert len(sessions_started) == 1
    assert sessions_started[0].closed is True


def test_drain_url_queue_never_starts_a_session_when_queue_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No items to process still starts (and immediately closes) exactly the one session maybe_start_session() would have started -- proves the session lifecycle wraps the whole batch, not conditioned on there being work, while still never launching more than one."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    monkeypatch.setattr(uq, "dequeue_url", lambda: None)
    monkeypatch.setattr(uq, "pending_url_count", lambda: 0)

    def _boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("must not scrape anything when the queue is empty")

    monkeypatch.setattr(
        uq, "WebCrawlerDriver", lambda: type("D", (), {"scrape_from_queue_item": _boom})()
    )

    sessions_started: list[_FakeSession] = []

    def _fake_maybe_start_session() -> _FakeSession:
        session = _FakeSession()
        sessions_started.append(session)
        return session

    monkeypatch.setattr(uq, "maybe_start_session", _fake_maybe_start_session)

    out = drain_url_queue(max_items=5)

    assert out["processed"] == 0
    assert len(sessions_started) == 1
    assert sessions_started[0].closed is True


def test_drain_url_queue_tolerates_no_session_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """maybe_start_session() returning None (SPA lane disabled, or Chromium failed to launch) must not break the drain -- items are processed with playwright_session=None, same as before this change, and there is nothing to close."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    pending = _queue(["https://svc.example/a"])
    monkeypatch.setattr(uq, "dequeue_url", lambda: pending.pop(0) if pending else None)
    monkeypatch.setattr(uq, "pending_url_count", lambda: 0)
    monkeypatch.setattr(uq, "maybe_start_session", lambda: None)

    driver = _FakeDriver()
    monkeypatch.setattr(uq, "WebCrawlerDriver", lambda: driver)

    out = drain_url_queue(max_items=5)

    assert out["processed"] == 1
    assert driver.calls == [("https://svc.example/a", None)]
