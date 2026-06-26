"""Per-URL 6h recrawl cooldown: a link marked crawled is reported as recently
crawled (so enqueue + the crawl path skip it), keyed by normalized URL."""

from app.modules.crawler import url_queue


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def exists(self, key):
        return 1 if key in self.store else 0

    def set(self, key, value, ex=None):
        self.store[key] = value


def _patch_redis(monkeypatch, fake):
    import redis

    monkeypatch.setattr(redis, "from_url", lambda *a, **k: fake)


def test_mark_then_recently_crawled(monkeypatch):
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    assert url_queue.recently_crawled("https://x.com/a") is False
    url_queue.mark_url_crawled("https://x.com/a")
    assert url_queue.recently_crawled("https://x.com/a") is True


def test_cooldown_key_normalizes(monkeypatch):
    # Trailing slash + missing scheme normalize to the same key.
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    url_queue.mark_url_crawled("https://x.com/a/")
    assert url_queue.recently_crawled("x.com/a") is True


def test_blank_url_is_safe(monkeypatch):
    fake = _FakeRedis()
    _patch_redis(monkeypatch, fake)
    assert url_queue.recently_crawled("") is False
    url_queue.mark_url_crawled("")  # no-op, no crash
    assert fake.store == {}
