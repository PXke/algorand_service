"""Cassandra-backed article storage."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.feed_bucket import cursor_from_ms, feed_month, to_ms
from app.modules.news.stores.base import StoredArticle


def _epoch(dt: datetime | None) -> int:
    """UTC epoch seconds from a stored timestamp. The Cassandra driver returns timezone-NAIVE datetimes that are already UTC; calling .timestamp() directly would make Python assume the server's local zone and shift the value (which is why 'Xh ago' looked wrong on non-UTC hosts)."""
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _feed_row_to_stored(row: Any) -> StoredArticle:
    pub = row.published_at
    return StoredArticle(
        article_id=str(row.article_id),
        service_id=row.service_id,
        title=row.title,
        summary=row.summary,
        body="",
        published_at_epoch=_epoch(pub),
        tags=list(row.tags or []),
        image_url=getattr(row, "image_url", None),
        source_url=getattr(row, "source_url", None),
        slug=getattr(row, "slug", None),
        translations=dict(row.translations) if row.translations else None,
        updated_at_epoch=(_epoch(getattr(row, "updated_at", None)) or None),
        first_published_at_epoch=(_epoch(getattr(row, "first_published_at", None)) or None),
    )


def _full_row_to_stored(row: Any) -> StoredArticle:
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
        slug=getattr(row, "slug", None),
        translations=dict(row.translations) if row.translations else None,
        updated_at_epoch=_epoch(getattr(row, "updated_at", None)) or None,
        draft=bool(getattr(row, "draft", False)),
    )


def _articles_row_to_stored(row: Any) -> StoredArticle | None:
    """Map a new `articles` table row to StoredArticle, or None if it's not a publicly-fetchable row.

    status='deleted' -> None: the OLD articles_by_id path hard-deletes on
    delete_article, so get() naturally returned None for a deleted id -- the
    new table instead TRANSITIONS to status='deleted' (row preserved,
    tombstoned) rather than removing the row, so this filter has to be
    explicit or a deleted article's full content would become readable again.
    The separate 410-vs-404 routing (deleted_articles tombstone table, see
    sitemap.py) is untouched by this and keeps working exactly as before --
    it never depended on get()'s return value, only on its own table.

    status='draft' -> draft=True: this is exactly what the old schema's
    separate articles_by_id.draft boolean column meant; the caller's own
    admin-only draft gate (NewsService._fetch_detail) is unchanged and reads
    this same flag.

    status in ('on_hold', 'backlog') is still returned (draft=False) --
    matching the OLD schema's behavior exactly: an unlisted article has
    always been directly fetchable by id (just not listed/indexed), since
    articles_by_id never had a separate "is this listed" check inside get()
    itself.
    """
    if row.status == "deleted":
        return None
    epoch = _epoch(row.published_at)
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
        image_url=row.image_url,
        slug=row.slug,
        translations=dict(row.translations) if row.translations else None,
        updated_at_epoch=_epoch(row.updated_at) or None,
        first_published_at_epoch=_epoch(row.first_published_at) or None,
        draft=row.status == "draft",
    )


class CassandraArticleStore:
    """Cassandra-backed article storage."""

    def insert(self, article: StoredArticle, *, feed_bucket: str = "main") -> None:
        # UNREACHABLE IN PRODUCTION as of 2026-07-13: NewsService (the only
        # consumer of ArticleStore) never calls this — real article writes
        # happen entirely in the `workers` package via insert_article /
        # insert_stored_article, which write full-precision datetimes
        # directly and bypass this class. Kept only to satisfy the
        # ArticleStore Protocol (InMemoryArticleStore's insert() IS live,
        # used by ~20 tests). If this ever gets wired up for real: the
        # published_at reconstruction below TRUNCATES to whole seconds
        # (StoredArticle.published_at_epoch is an int), which silently
        # upserts a phantom duplicate row in articles_feed whose PRIMARY KEY
        # includes published_at at full precision — found and cleaned up one
        # such duplicate (Bitrue article, 2026-07-13) whose origin predates
        # this comment and could not be traced to a live call site.
        """Insert a new article and its feed row. Unreachable in production; kept to satisfy ArticleStore."""
        _ = feed_bucket  # single-bucket schema; accepted for protocol parity
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
                article.source_url,
            ),
        )

    def list_feed(self, *, feed_bucket: str = "main", limit: int = 50) -> list[StoredArticle]:
        """List recent feed rows, newest first (wraps list_feed_page, discarding the cursor).

        The keyword must stay spelled `feed_bucket` to match ArticleStore;
        test_news_store_protocol guards it.
        """
        _ = feed_bucket  # single-bucket schema; accepted for protocol parity
        items, _cursor = self.list_feed_page(limit=limit)
        return items

    def list_feed_page(
        self,
        *,
        limit: int = 50,
        cursor_epoch_ms: int | None = None,
        max_months: int = 18,
    ) -> tuple[list[StoredArticle], int | None]:
        """Keyset-paginated feed across `articles`' (status='published', year) partitions. Returns (items, next_cursor_ms); next_cursor is None when no more pages.

        `articles` doubles as the feed projection (article-table
        consolidation, step 5 read cutover, 2026-08-24) -- year is the
        partition granularity here (not month, like the old articles_feed),
        since per-year partitions comfortably hold this platform's real
        article volume (~7/day). ``max_months`` is kept as the param name for
        call-site compatibility but now means "how many years of history, at
        minimum, to cover" -- max(2, ceil(max_months/12)) so the existing
        18-month default still reaches back 2 years.

        Prefetches the next year partition while mapping the current page so
        Cassandra round-trips overlap with CPU work.
        """
        import math

        from algorand_shared.article_statements import ArticlesStmts
        from app.core.cassandra import execute_async

        cursor_dt = cursor_from_ms(cursor_epoch_ms)
        max_years = max(2, math.ceil(max_months / 12))
        years = [cursor_dt.year - i for i in range(max_years)]
        items: list[StoredArticle] = []
        last_dt = None

        fut = execute_async(ArticlesStmts.LIST_PUBLISHED_PAGE, (years[0], cursor_dt, limit))
        for index in range(len(years)):
            rows = list(fut.result())
            remaining_after = limit - len(items) - len(rows)
            if remaining_after > 0 and index + 1 < len(years):
                fut = execute_async(
                    ArticlesStmts.LIST_PUBLISHED_PAGE, (years[index + 1], cursor_dt, remaining_after)
                )
            else:
                fut = None
            for row in rows:
                if len(items) >= limit:
                    break
                last_dt = row.published_at
                items.append(_feed_row_to_stored(row))
            if len(items) >= limit or fut is None:
                break

        next_cursor = to_ms(last_dt) if (len(items) >= limit and last_dt) else None
        return items, next_cursor

    def id_for_slug(self, slug: str) -> str | None:
        """Article id owning this permanent URL slug, or None."""
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import NewsStmts

        clean = (slug or "").strip().lower()
        if not clean:
            return None
        row = get_cassandra_session().execute(NewsStmts.ID_BY_SLUG, (clean,)).one()
        return str(row.article_id) if row and row.article_id else None

    def get(
        self,
        article_id: str,
        *,
        overlap: Callable[[], Any] | None = None,
    ) -> StoredArticle | None:
        """Fetch one article's full data by id, or None if it does not exist.

        ``overlap`` runs after the query is dispatched and before waiting, so
        callers can do unrelated work (Redis, header checks) in parallel.

        Reads from the new consolidated `articles` table via its SAI index on
        article_id (article-table consolidation, step 5 read cutover,
        2026-08-24) -- benchmarked against a resolver-table design first (see
        the plan), a direct SAI point lookup costs ~0.4ms more than knowing
        the partition key outright, negligible at this platform's real data
        volume.
        """
        from algorand_shared.article_statements import ArticlesStmts
        from app.core.cassandra import execute_then

        try:
            aid = UUID(article_id)
        except ValueError:
            return None
        row = execute_then(ArticlesStmts.GET_FULL_BY_ID, (aid,), overlap=overlap).one()
        if row is None:
            return None
        return _articles_row_to_stored(row)

    def get_many(self, article_ids: list[str]) -> dict[str, StoredArticle]:
        """Fetch many articles by id concurrently; missing ids are omitted."""
        from algorand_shared.article_statements import ArticlesStmts
        from app.core.cassandra import execute_parallel_with_args

        pairs: list[tuple[str, UUID]] = []
        for raw in article_ids:
            try:
                pairs.append((raw, UUID(raw)))
            except ValueError:
                continue
        if not pairs:
            return {}
        out: dict[str, StoredArticle] = {}
        for (raw, _), (ok, result) in zip(
            pairs,
            execute_parallel_with_args(
                ArticlesStmts.GET_FULL_BY_ID, [(aid,) for _, aid in pairs], raise_on_error=False
            ),
            strict=True,
        ):
            if not ok:
                continue
            row = result.one() if hasattr(result, "one") else None
            if row is not None:
                stored = _articles_row_to_stored(row)
                if stored is not None:
                    out[raw] = stored
        return out
