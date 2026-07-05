"""Per-URL 6h recrawl cooldown: a link marked crawled is reported as recently
crawled (so enqueue + the crawl path skip it), keyed by normalized URL."""

from app.modules.crawler import url_queue


def test_mark_then_recently_crawled(patch_redis_from_url):
    assert url_queue.recently_crawled("https://x.com/a") is False
    url_queue.mark_url_crawled("https://x.com/a")
    assert url_queue.recently_crawled("https://x.com/a") is True


def test_cooldown_key_normalizes(patch_redis_from_url):
    # Trailing slash + missing scheme normalize to the same key.
    url_queue.mark_url_crawled("https://x.com/a/")
    assert url_queue.recently_crawled("x.com/a") is True


def test_blank_url_is_safe(patch_redis_from_url):
    assert url_queue.recently_crawled("") is False
    url_queue.mark_url_crawled("")  # no-op, no crash
    assert patch_redis_from_url.store == {}
