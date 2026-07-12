"""Reliable-topic selection shared by the sitemap, llms.txt and topic SSR.

Topics ARE the writer's own tags — the paper's real taxonomy (the fixed
human-defined sections were removed 2026-07). Not every tag deserves a
landing page though: singleton tags are noise, and a tag present on half the
corpus ("web", "discovery") carries no signal. One policy, applied everywhere.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable

from app.modules.news.models.schemas import ArticleFeedItem

# Slugs of the retired human sections → the closest real writer tag, so the
# indexed /section/* URLs 301 somewhere meaningful instead of 404ing.
SECTION_REDIRECTS: dict[str, str] = {
    "markets": "market",
    "security": "breaking",
    "developers": "sdk",
    "community": "community",
    "ecosystem": "ecosystem",
}

MIN_COUNT = 2
UBIQUITY_CEILING = 0.5  # tags on >=50% of the corpus are boilerplate
DEFAULT_CAP = 100

# Pipeline-stamped labels with no topical signal (mirrors the Flutter
# `kBoilerplateTags`); kickers/breadcrumbs skip past them.
BOILERPLATE_TAGS = frozenset({"web", "news", "discovery", "algorand", "generic", "service"})


def primary_tag(tags: list[str] | None) -> str | None:
    """First non-boilerplate tag, falling back to the plain first tag."""
    cleaned = [t.strip().lower() for t in (tags or []) if t.strip()]
    for tag in cleaned:
        if tag not in BOILERPLATE_TAGS:
            return tag
    return cleaned[0] if cleaned else None


def tag_counts(items: list[ArticleFeedItem]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for item in items:
        for raw in item.tags or []:
            tag = raw.strip().lower()
            if tag:
                counts[tag] += 1
    return counts


def reliable_tags(items: list[ArticleFeedItem], *, cap: int = DEFAULT_CAP) -> list[tuple[str, int]]:
    """(tag, article_count) pairs worth a landing page, most-covered first."""
    counts = tag_counts(items)
    total = len(items)
    picked = [
        (tag, n)
        for tag, n in counts.most_common()
        if n >= MIN_COUNT and (total <= 4 or n < total * UBIQUITY_CEILING)
    ]
    return picked[:cap]


def is_reliable_tag(tag: str, items: list[ArticleFeedItem]) -> bool:
    wanted = tag.strip().lower()
    return any(t == wanted for t, _ in reliable_tags(items))


def topic_feed_path(tag: str) -> str:
    slug = tag.strip().lower()
    return f"/feed/topic/{slug}.xml"


def items_for_tag(items: list[ArticleFeedItem], tag: str) -> list[ArticleFeedItem]:
    """Feed rows carrying this writer tag (case-insensitive)."""
    wanted = tag.strip().lower()
    return [
        item
        for item in items
        if any(t.strip().lower() == wanted for t in (item.tags or []))
    ]


_FEED_CACHE_TTL_SEC = 120
_feed_cache: dict[str, object] = {"mono": 0.0, "feed": [], "topics": []}


def cached_feed_snapshot(
    list_feed: Callable[..., list[ArticleFeedItem]],
    *,
    limit: int = 500,
) -> tuple[list[ArticleFeedItem], list[tuple[str, int]]]:
    """Short-lived feed + reliable-tags cache for SSR routes that share a scan."""
    now = time.monotonic()
    cached_at = float(_feed_cache["mono"])
    feed = _feed_cache.get("feed")
    topics = _feed_cache.get("topics")
    if (
        isinstance(feed, list)
        and feed
        and isinstance(topics, list)
        and now - cached_at < _FEED_CACHE_TTL_SEC
    ):
        return feed, topics
    fresh = list_feed(limit=limit)
    picked = reliable_tags(fresh)
    _feed_cache["mono"] = now
    _feed_cache["feed"] = fresh
    _feed_cache["topics"] = picked
    return fresh, picked
