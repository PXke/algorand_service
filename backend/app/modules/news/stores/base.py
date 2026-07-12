from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class StoredArticle:
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


class ArticleStore(Protocol):
    def insert(self, article: StoredArticle, *, feed_bucket: str = "main") -> None: ...

    def list_feed(self, *, feed_bucket: str = "main", limit: int = 50) -> list[StoredArticle]: ...

    def get(self, article_id: str) -> StoredArticle | None: ...
