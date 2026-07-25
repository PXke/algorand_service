"""Feed listing, filtering, and translation overlay for article reads."""

from __future__ import annotations

import pytest

from app.modules.news.services.news_service import NewsService
from app.modules.news.stores.base import StoredArticle
from app.modules.news.stores.memory import InMemoryArticleStore


def test_news_feed_lists_articles_newest_first() -> None:
    """Orders the feed by published_at_epoch descending."""
    store = InMemoryArticleStore()
    store.insert(
        StoredArticle(
            article_id="a1",
            service_id="svc",
            title="Older",
            summary="s1",
            body="b1",
            published_at_epoch=100,
        )
    )
    store.insert(
        StoredArticle(
            article_id="a2",
            service_id="svc",
            title="Newer",
            summary="s2",
            body="b2",
            published_at_epoch=200,
        )
    )
    service = NewsService(store=store)
    items = service.list_feed()
    assert items[0].title == "Newer"
    assert items[1].title == "Older"


def test_news_feed_filters_by_service_id() -> None:
    """Restricts the feed to articles matching the given service_id."""
    store = InMemoryArticleStore()
    store.insert(
        StoredArticle(
            article_id="a1",
            service_id="svc-a",
            title="A",
            summary="s",
            body="b",
            published_at_epoch=100,
        )
    )
    store.insert(
        StoredArticle(
            article_id="a2",
            service_id="svc-b",
            title="B",
            summary="s",
            body="b",
            published_at_epoch=200,
        )
    )
    service = NewsService(store=store)
    items = service.list_feed(service_id="svc-a")
    assert [item.article_id for item in items] == ["a1"]


def test_get_article_detail() -> None:
    """Fetches full article detail, including body and trigger metadata, by id."""
    store = InMemoryArticleStore()
    store.insert(
        StoredArticle(
            article_id="id-1",
            service_id="svc",
            title="T",
            summary="S",
            body="Full body",
            published_at_epoch=1,
            trigger_txid="T" * 52,
            trigger_round=42,
            source_url="https://example.com",
        )
    )
    detail = NewsService(store=store).get_article("id-1")
    assert detail is not None
    assert detail.body == "Full body"
    assert detail.trigger_round == 42


def test_get_article_applies_translation_overlay() -> None:
    """Overlays the stored translation's title/summary/body when a lang is requested."""
    import json

    store = InMemoryArticleStore()
    store.insert(
        StoredArticle(
            article_id="id-fa",
            service_id="svc",
            title="English title",
            summary="English summary",
            body="English body",
            published_at_epoch=1,
            translations={
                "fa": json.dumps(
                    {
                        "title": "عنوان دری",
                        "summary": "خلاصه",
                        "body": "متن",
                    },
                    ensure_ascii=False,
                )
            },
        )
    )
    svc = NewsService(store=store)
    detail = svc.get_article("id-fa", lang="fa")
    assert detail is not None
    assert detail.title == "عنوان دری"
    assert detail.body == "متن"
    assert svc.translation_langs_for("id-fa") == ["fa"]


def _story(article_id: str, epoch: int, tags: list[str]) -> StoredArticle:
    return StoredArticle(
        article_id=article_id,
        service_id="svc",
        title=f"T-{article_id}",
        summary="s",
        body="b",
        published_at_epoch=epoch,
        tags=tags,
    )


def test_news_feed_filters_by_tag_case_insensitive() -> None:
    """Matches a tag filter regardless of case or trailing whitespace on stored tags."""
    store = InMemoryArticleStore()
    store.insert(_story("a1", 100, ["NFT", "algorand"]))
    store.insert(_story("a2", 200, ["defi"]))
    store.insert(_story("a3", 300, ["nft "]))
    service = NewsService(store=store)
    items, next_cursor = service.list_feed_page(tag="nft")
    assert [item.article_id for item in items] == ["a3", "a1"]
    assert next_cursor is None


def test_hot_feed_ranks_by_views_then_recency() -> None:
    """Falls back to recency ordering when all articles have zero views."""
    store = InMemoryArticleStore()
    store.insert(_story("a1", 100, []))
    store.insert(_story("a2", 200, []))
    store.insert(_story("a3", 300, []))
    service = NewsService(store=store)
    # Memory store has no view counters -> all zero; recency breaks the tie.
    items = service.hot_feed(limit=2)
    assert [item.article_id for item in items] == ["a3", "a2"]
    assert all(item.views == 0 for item in items)


def test_tag_stats_aggregates_counts_and_last_epoch() -> None:
    """Aggregates per-tag article counts and last-seen epoch case-insensitively."""
    store = InMemoryArticleStore()
    store.insert(_story("a1", 100, ["nft", "Algorand"]))
    store.insert(_story("a2", 200, ["NFT"]))
    store.insert(_story("a3", 300, ["defi", "algorand"]))
    stats = NewsService(store=store).tag_stats()
    assert stats["article_count"] == 3
    by_tag = {entry["tag"]: entry for entry in stats["tags"]}
    assert by_tag["nft"]["count"] == 2
    assert by_tag["nft"]["last_epoch"] == 200
    assert by_tag["algorand"]["count"] == 2
    assert by_tag["defi"]["count"] == 1


def test_hot_feed_velocity_vs_alltime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ranks "hot" by views-per-day velocity but "top" by lifetime view count."""
    import time

    from app.modules.news.stores import view_counts

    now = int(time.time())
    store = InMemoryArticleStore()
    store.insert(_story("old", now - 10 * 86400, []))  # 100 views / 10d = 10/d
    store.insert(_story("new", now - 1 * 86400, []))  # 30 views / 1d = 30/d
    monkeypatch.setattr(view_counts, "get_views_bulk", lambda _ids: {"old": 100, "new": 30})
    service = NewsService(store=store)
    hot = service.hot_feed(rank="hot")
    assert [i.article_id for i in hot] == ["new", "old"]
    top = service.hot_feed(rank="top")
    assert [i.article_id for i in top] == ["old", "new"]
    assert top[0].views == 100


def test_hot_feed_ages_recomposed_articles_from_first_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recompose re-publish re-stamps published_at. Velocity must age the article from first_published_at_epoch, or its lifetime views divided by a just-reset age would catapult any refreshed old story to #1 hot."""
    import time

    from app.modules.news.stores import view_counts

    now = int(time.time())
    store = InMemoryArticleStore()
    refreshed = _story("refreshed", now - 3600, [])  # recomposed 1h ago...
    refreshed.first_published_at_epoch = now - 30 * 86400  # ...born 30d ago
    store.insert(refreshed)
    store.insert(_story("new", now - 1 * 86400, []))  # 30 views / 1d = 30/d
    monkeypatch.setattr(view_counts, "get_views_bulk", lambda _ids: {"refreshed": 100, "new": 30})
    # refreshed: 100 views / 30d ≈ 3.3/d — well below new's 30/d. (With the
    # bug, 100 views / 0.25d floor = 400/d would have ranked it first.)
    hot = NewsService(store=store).hot_feed(rank="hot")
    assert [i.article_id for i in hot] == ["new", "refreshed"]
