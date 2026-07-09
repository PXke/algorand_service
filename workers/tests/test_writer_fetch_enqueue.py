"""Writer fetch_url → crawl queue enqueue."""

from app.modules.ai import writer_tools as wt
from app.modules.crawler.writer_fetch_enqueue import maybe_enqueue_writer_fetched_url


def test_enqueue_skips_continuation_windows() -> None:
    assert not maybe_enqueue_writer_fetched_url(
        {"url": "https://x.io/p", "text": "x" * 200, "chunk_chars": 200},
        is_continuation=True,
    )


def test_enqueue_first_window(monkeypatch) -> None:
    created = []

    def _fake_enqueue(url, **kw):
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


def test_wrap_enqueue_skips_continue_reading(monkeypatch) -> None:
    calls = []

    def _handler(**kwargs):
        return {"url": kwargs["url"], "text": "body", "chunk_chars": 200}

    monkeypatch.setattr(
        "app.modules.crawler.writer_fetch_enqueue.maybe_enqueue_writer_fetched_url",
        lambda result, **kw: calls.append(kw.get("is_continuation")) or False,
    )
    wrapped = wt._wrap_fetch_url_enqueue(_handler, {})
    wrapped(url="https://x.io/p", continue_reading=True)
    assert calls == [True]


def test_wrap_enqueue_first_read(monkeypatch) -> None:
    calls = []

    def _handler(**kwargs):
        return {"url": kwargs["url"], "text": "body", "chunk_chars": 200}

    monkeypatch.setattr(
        "app.modules.crawler.writer_fetch_enqueue.maybe_enqueue_writer_fetched_url",
        lambda result, **kw: calls.append(kw.get("is_continuation")) or False,
    )
    wrapped = wt._wrap_fetch_url_enqueue(_handler, {})
    wrapped(url="https://x.io/p")
    assert calls == [False]
