"""Reliable-topic selection shared by the sitemap, llms.txt and topic SSR.

Topics ARE the writer's own tags — the paper's real taxonomy (the fixed
human-defined sections were removed 2026-07). Not every tag deserves a
landing page though: singleton tags are noise, and a tag present on half the
corpus ("web", "discovery") carries no signal. One policy, applied everywhere.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

import msgspec

# display_tag_label and primary_tag are re-exported for callers that import
# them from this module; the redundant alias is the explicit re-export idiom
# and keeps them from reading as unused imports.
from algorand_shared.taxonomy import (
    display_tag_label as display_tag_label,
)
from algorand_shared.taxonomy import (
    meta_tags,
)
from algorand_shared.taxonomy import (
    primary_tag as primary_tag,
)

from app.core import serialization
from app.core.cache import cached_json
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

MIN_COUNT = 10
UBIQUITY_CEILING = 0.5  # tags on >=50% of the corpus are boilerplate
DEFAULT_CAP = 100
_FEED_CACHE_TTL_SEC = 120
_FEED_SNAPSHOT_KEY = "seo:feed-snapshot"

# Pipeline-stamped labels with no topical signal; kickers/breadcrumbs skip past
# them. `primary_tag` and `display_tag_label` now come from shared/taxonomy.json
# — the SPA generates its copy from the same file. They used to be two
# hand-maintained lists (6 entries here, 18 in the frontend), so an article
# tagged ["update", "defi"] was served to crawlers as "Update" and shown to
# readers as "DeFi" once the SPA hydrated: same URL, two sections.
#
# Note this list governs the KICKER only. Which tags get a landing page is a
# separate policy below (MIN_COUNT + UBIQUITY_CEILING), deliberately so — a tag
# can be too generic to headline a story yet still worth an index page.
BOILERPLATE_TAGS = meta_tags()


def tag_counts(items: list[ArticleFeedItem]) -> Counter[str]:
    """Count how many feed items carry each lowercased tag."""
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
    """Return whether the given tag qualifies for its own topic landing page."""
    wanted = tag.strip().lower()
    return any(t == wanted for t, _ in reliable_tags(items))


def topic_feed_path(tag: str) -> str:
    """Return the RSS feed URL path for a given topic tag."""
    slug = tag.strip().lower()
    return f"/feed/topic/{slug}.xml"


def items_for_tag(items: list[ArticleFeedItem], tag: str) -> list[ArticleFeedItem]:
    """Feed rows carrying this writer tag (case-insensitive)."""
    wanted = tag.strip().lower()
    return [item for item in items if any(t.strip().lower() == wanted for t in (item.tags or []))]


def cached_feed_snapshot(
    list_feed: Callable[..., list[ArticleFeedItem]],
    *,
    limit: int = 500,
) -> tuple[list[ArticleFeedItem], list[tuple[str, int]]]:
    """Short-lived feed + reliable-tags cache for SSR routes that share a scan."""
    def compute() -> dict[str, object]:
        fresh = list_feed(limit=limit)
        return {
            "feed": serialization.to_builtins(fresh),
            "topics": reliable_tags(fresh),
        }

    data = cached_json(_FEED_SNAPSHOT_KEY, _FEED_CACHE_TTL_SEC, compute)
    feed = msgspec.convert(data["feed"], list[ArticleFeedItem])
    topics = data["topics"]
    assert isinstance(topics, list)
    return feed, topics
