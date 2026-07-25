"""Writer fetch_url → crawl queue enqueue."""

import pytest

from app.modules.ai import writer_tools as wt
from app.modules.crawler.writer_fetch_enqueue import maybe_enqueue_writer_fetched_url


def test_enqueue_skips_continuation_windows() -> None:
    """Does not enqueue a crawl for a "continue reading" (non-first) fetch window."""
    assert not maybe_enqueue_writer_fetched_url(
        {"url": "https://x.io/p", "text": "x" * 200, "chunk_chars": 200},
        is_continuation=True,
    )


def test_enqueue_first_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enqueues the fetched URL for crawling on the first (non-continuation) window."""
    created = []

    def _fake_enqueue(url: str, **kw: object) -> tuple[str, bool]:
        created.append((url, kw))
        return url, True

    monkeypatch.setattr("app.modules.crawler.url_queue.enqueue_url", _fake_enqueue)
    monkeypatch.setattr("app.core.config.URL_QUEUE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_FETCH_ENQUEUE_ENABLED", True, raising=False)

    assert maybe_enqueue_writer_fetched_url(
        {"url": "https://x.io/p", "text": "x" * 200, "chunk_chars": 200},
        is_continuation=False,
    )
    assert created[0][0] == "https://x.io/p"


def test_wrap_enqueue_skips_continue_reading(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fetch_url tool wrapper marks a continue_reading call as a continuation."""
    calls = []

    def _handler(**kwargs: object) -> dict:
        return {"url": kwargs["url"], "text": "body", "chunk_chars": 200}

    monkeypatch.setattr(
        "app.modules.crawler.writer_fetch_enqueue.maybe_enqueue_writer_fetched_url",
        lambda _result, **kw: calls.append(kw.get("is_continuation")) or False,
    )
    wrapped = wt._wrap_fetch_url_enqueue(_handler, {})
    wrapped(url="https://x.io/p", continue_reading=True)
    assert calls == [True]


def test_wrap_enqueue_first_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fetch_url tool wrapper marks a plain (non-continuation) call as not-a-continuation."""
    calls = []

    def _handler(**kwargs: object) -> dict:
        return {"url": kwargs["url"], "text": "body", "chunk_chars": 200}

    monkeypatch.setattr(
        "app.modules.crawler.writer_fetch_enqueue.maybe_enqueue_writer_fetched_url",
        lambda _result, **kw: calls.append(kw.get("is_continuation")) or False,
    )
    wrapped = wt._wrap_fetch_url_enqueue(_handler, {})
    wrapped(url="https://x.io/p")
    assert calls == [False]
