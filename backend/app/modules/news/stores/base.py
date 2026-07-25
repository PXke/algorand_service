"""Storage interface for articles."""

from __future__ import annotations

from dataclasses import dataclass
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
    translations: dict[str, str] | None = None
    # Last content revision (edit/recompose); None = never revised.
    updated_at_epoch: int | None = None
    # Original first publication; differs from published_at_epoch only after
    # a recompose re-publish (which re-stamps published_at). None = never
    # recomposed.
    first_published_at_epoch: int | None = None


class ArticleStore(Protocol):
    """Storage interface for articles."""
    def insert(self, article: StoredArticle, *, feed_bucket: str = "main") -> None:
        """Insert a new article and its feed row."""
        ...

    def list_feed(self, *, feed_bucket: str = "main", limit: int = 50) -> list[StoredArticle]:
        """List recent feed rows, newest first."""
        ...

    def get(self, article_id: str) -> StoredArticle | None:
        """Fetch one article by id, or None if it does not exist."""
        ...
