"""news.api.routes.feed(): server-side cache-aside caching (same `cached_json` primitive as /tags and /hot) plus write-path invalidation via feed_cache.invalidate_feed_first_page()."""

from __future__ import annotations

import fnmatch

import pytest

from app.core.http import QueryParams, Request
from app.modules.news.api import routes as news_routes
from app.schemas import ArticleFeedItem


class FakeRedis:
    """In-memory stand-in for the redis-py client, covering the get/set/delete/scan_iter surface app.core.cache and algorand_shared.feed_cache use."""

    def __init__(self) -> None:
        """Start with an empty in-process key/value store."""
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        """Return the stored value for a key, or None if absent."""
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> bool:  # noqa: ARG002 -- ex accepted, not enforced (no real TTL in-memory)
        """Set a key's value; ex (TTL) is accepted but not enforced."""
        self.store[key] = value
        return True

    def delete(self, *keys: str) -> int:
        """Delete the given keys, returning the count actually removed."""
        removed = 0
        for key in keys:
            if key in self.store:
                del self.store[key]
                removed += 1
        return removed

    def scan_iter(self, match: str, count: int = 200) -> list[str]:  # noqa: ARG002 -- count is a real-Redis batching hint, irrelevant in-memory
        """Return every stored key matching the glob pattern."""
        return [key for key in list(self.store) if fnmatch.fnmatch(key, match)]


def _req(**params: str) -> Request:
    return Request(
        method="GET",
        headers={},
        query_params=QueryParams(params),  # type: ignore[arg-type]
        path_params={},
    )


def _item(article_id: str = "a1") -> ArticleFeedItem:
    return ArticleFeedItem(
        article_id=article_id,
        service_id="svc",
        title="Title",
        summary="Summary",
        published_at_epoch=1_700_000_000,
    )


@pytest.fixture
def _fake_cache_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Back both app.core.cache's client and feed_cache's redis.from_url() with the same in-memory store, so a write's invalidation is visible to a subsequent read in the same test."""
    fake = FakeRedis()
    monkeypatch.setattr("app.core.cache._client", lambda: fake)

    import redis

    monkeypatch.setattr(redis, "from_url", lambda *_a, **_k: fake)
    return fake


@pytest.mark.usefixtures("_fake_cache_redis")
def test_feed_cache_hit_avoids_second_store_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """A repeated identical request must not re-hit NewsService.list_feed_page."""
    calls: list[dict] = []

    def fake_list_feed_page(**kwargs: object) -> tuple[list[ArticleFeedItem], int | None]:
        calls.append(kwargs)
        return [_item()], None

    monkeypatch.setattr(news_routes.news_service, "list_feed_page", fake_list_feed_page)

    resp1 = news_routes.feed(_req(limit="10"))
    resp2 = news_routes.feed(_req(limit="10"))

    assert len(calls) == 1
    assert resp1.description == resp2.description
    assert resp1.status_code == 200
    assert resp2.status_code == 200


@pytest.mark.usefixtures("_fake_cache_redis")
def test_feed_cache_key_scoped_by_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    """Different limit/service_id/tag/lang/cursor combos must not collide in the cache -- each is a fresh compute."""
    calls: list[dict] = []

    def fake_list_feed_page(**kwargs: object) -> tuple[list[ArticleFeedItem], int | None]:
        calls.append(kwargs)
        return [_item()], None

    monkeypatch.setattr(news_routes.news_service, "list_feed_page", fake_list_feed_page)

    news_routes.feed(_req(limit="10"))
    news_routes.feed(_req(limit="20"))
    news_routes.feed(_req(limit="10", service_id="svc-a"))
    news_routes.feed(_req(limit="10", tag="defi"))
    news_routes.feed(_req(limit="10", lang="fr"))
    news_routes.feed(_req(limit="10", cursor="1700000000000"))

    assert len(calls) == 6


@pytest.mark.usefixtures("_fake_cache_redis")
def test_write_invalidation_busts_first_page_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """After a write calls invalidate_feed_first_page(), the next identical first-page request must recompute rather than serve the stale cached entry."""
    from algorand_shared.feed_cache import invalidate_feed_first_page

    calls: list[dict] = []

    def fake_list_feed_page(**kwargs: object) -> tuple[list[ArticleFeedItem], int | None]:
        calls.append(kwargs)
        return [_item()], None

    monkeypatch.setattr(news_routes.news_service, "list_feed_page", fake_list_feed_page)

    news_routes.feed(_req(limit="10"))
    assert len(calls) == 1

    # Simulate a write (article publish/edit/delete) invalidating the cache.
    invalidate_feed_first_page()

    news_routes.feed(_req(limit="10"))
    assert len(calls) == 2  # cache was busted, not served stale


@pytest.mark.usefixtures("_fake_cache_redis")
def test_write_invalidation_does_not_touch_cursor_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cursor'd (non-first) pages are stable once cached -- invalidate_feed_first_page() must not evict them."""
    from algorand_shared.feed_cache import invalidate_feed_first_page

    calls: list[dict] = []

    def fake_list_feed_page(**kwargs: object) -> tuple[list[ArticleFeedItem], int | None]:
        calls.append(kwargs)
        return [_item()], None

    monkeypatch.setattr(news_routes.news_service, "list_feed_page", fake_list_feed_page)

    news_routes.feed(_req(limit="10", cursor="1700000000000"))
    assert len(calls) == 1

    invalidate_feed_first_page()

    news_routes.feed(_req(limit="10", cursor="1700000000000"))
    assert len(calls) == 1  # still cached -- untouched by first-page invalidation
