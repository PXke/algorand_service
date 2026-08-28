"""Storage interface for articles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class StoredArticle:
    """A stored article's full data."""

    article_id: str
    service_id: str
    title: str
    summary: str
    body: str
    published_at_epoch: int
    trigger_txid: str | None = None
    trigger_round: int | None = None
    source_url: str | None = None
    tags: list[str] | None = None
    image_url: str | None = None
    slug: str | None = None
    translations: dict[str, str] | None = None
    # Lightweight lang -> JSON {title, summary} companion to `translations`
    # (which also carries body). Populated on feed/tag-listing rows (which
    # read `translated_titles` off Cassandra directly, migration 087) --
    # NOT populated on a full single-article detail row (get/get_many),
    # which continues to carry the complete `translations` map above and has
    # no need for this lighter duplicate. NewsService._to_feed_item /
    # list_feed_for_sitemap read this field, never `translations`, for
    # anything sourced from a feed/tag listing.
    translated_titles: dict[str, str] | None = None
    # Last content revision (edit/recompose); None = never revised.
    updated_at_epoch: int | None = None
    # Original first publication; differs from published_at_epoch only after
    # a recompose re-publish (which re-stamps published_at). None = never
    # recomposed.
    first_published_at_epoch: int | None = None
    # Admin-only unpublish: content stays intact, but NewsService.get_article
    # must treat this as not-found for every public caller. Never true for a
    # row read via the feed projection (draft articles are removed from
    # articles_feed), only via a direct articles_by_id lookup by id/slug.
    draft: bool = False


@dataclass
class TagSummary:
    """One tag's coverage over the live feed: how many published articles carry it, when it last appeared, and a sample of article ids (capped) to sum view counts over.

    ``count``/``last_epoch`` are exact (a single-partition COUNT / the newest
    clustering row on the Cassandra store), unlike ``article_ids`` which is a
    bounded sample -- large enough that the view-count total it feeds is a
    faithful approximation, never re-scanned in full just to rank the topic
    cloud.
    """

    tag: str
    count: int = 0
    last_epoch: int = 0
    article_ids: list[str] = field(default_factory=list)


class ArticleStore(Protocol):
    """Storage interface for articles."""

    def list_feed(self, *, feed_bucket: str = "main", limit: int = 50) -> list[StoredArticle]:
        """List recent feed rows, newest first."""
        ...

    def id_for_slug(self, slug: str) -> str | None:
        """Article id owning this permanent URL slug, or None."""
        ...

    def get(self, article_id: str) -> StoredArticle | None:
        """Fetch one article by id, or None if it does not exist."""
        ...

    def get_many(self, article_ids: list[str]) -> dict[str, StoredArticle]:
        """Fetch many articles by id; missing ids are omitted."""
        ...

    def list_by_tag_page(
        self, tag: str, *, limit: int = 50, cursor_epoch_ms: int | None = None
    ) -> tuple[list[StoredArticle], int | None]:
        """List published articles carrying `tag` (case/whitespace-insensitive), newest first, keyset-paginated. Returns (items, next_cursor_ms)."""
        ...

    def tag_summary(self) -> list[TagSummary]:
        """Per-tag coverage over the live feed (topic-cloud aggregate source data), one entry per distinct tag in use."""
        ...
