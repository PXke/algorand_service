from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.core.feed_bucket import cursor_from_ms, feed_month, months_back, to_ms
from app.modules.news.stores.base import StoredArticle


def _epoch(dt: datetime | None) -> int:
    """UTC epoch seconds from a stored timestamp. The Cassandra driver returns
    timezone-NAIVE datetimes that are already UTC; calling .timestamp() directly
    would make Python assume the server's local zone and shift the value (which
    is why 'Xh ago' looked wrong on non-UTC hosts)."""
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


class CassandraArticleStore:
    def insert(self, article: StoredArticle, *, feed_bucket: str = "main") -> None:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import NewsStmts

        session = get_cassandra_session()
        published_at = datetime.fromtimestamp(article.published_at_epoch, tz=UTC)
        article_uuid = UUID(article.article_id)

        tags = list(article.tags or [])
        session.execute(
            NewsStmts.INSERT_BY_ID,
            (
                article_uuid,
                article.service_id,
                article.title,
                article.summary,
                article.body,
                article.trigger_txid,
                article.trigger_round,
                article.source_url,
                published_at,
                tags,
                article.image_url,
            ),
        )
        session.execute(
            NewsStmts.INSERT_FEED,
            (
                feed_month(published_at),
                published_at,
                article_uuid,
                article.service_id,
                article.title,
                article.summary,
                tags,
                article.image_url,
            ),
        )

    def list_feed(self, *, feed_bucket: str = "main", limit: int = 50) -> list[StoredArticle]:
        items, _ = self.list_feed_page(limit=limit)
        return items

    def list_feed_page(
        self,
        *,
        limit: int = 50,
        cursor_epoch_ms: int | None = None,
        max_months: int = 18,
    ) -> tuple[list[StoredArticle], int | None]:
        """Keyset-paginated feed across month partitions. Returns (items,
        next_cursor_ms); next_cursor is None when no more pages."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import NewsStmts

        session = get_cassandra_session()
        cursor_dt = cursor_from_ms(cursor_epoch_ms)
        items: list[StoredArticle] = []
        last_dt = None
        for bucket in months_back(cursor_dt, max_months):
            if len(items) >= limit:
                break
            rows = session.execute(
                NewsStmts.FEED_PAGE,
                (bucket, cursor_dt, limit - len(items)),
            )
            for row in rows:
                pub = row.published_at
                last_dt = pub
                items.append(
                    StoredArticle(
                        article_id=str(row.article_id),
                        service_id=row.service_id,
                        title=row.title,
                        summary=row.summary,
                        body="",
                        published_at_epoch=_epoch(pub),
                        tags=list(row.tags or []),
                        image_url=getattr(row, "image_url", None),
                        source_url=getattr(row, "source_url", None),
                        translations=getattr(row, "translations", None),
                        updated_at_epoch=(
                            _epoch(getattr(row, "updated_at", None)) or None
                        ),
                    )
                )
        next_cursor = to_ms(last_dt) if (len(items) >= limit and last_dt) else None
        return items, next_cursor

    def get(self, article_id: str) -> StoredArticle | None:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import NewsStmts

        session = get_cassandra_session()
        try:
            aid = UUID(article_id)
        except ValueError:
            return None
        row = session.execute(NewsStmts.GET_FULL, (aid,)).one()
        if row is None:
            return None
        published_at = row.published_at
        epoch = _epoch(published_at)
        return StoredArticle(
            article_id=str(row.article_id),
            service_id=row.service_id,
            title=row.title,
            summary=row.summary,
            body=row.body,
            published_at_epoch=epoch,
            trigger_txid=row.trigger_txid,
            trigger_round=int(row.trigger_round) if row.trigger_round is not None else None,
            source_url=row.source_url,
            tags=list(row.tags or []),
            image_url=getattr(row, "image_url", None),
            translations=getattr(row, "translations", None),
            updated_at_epoch=_epoch(getattr(row, "updated_at", None)) or None,
        )
