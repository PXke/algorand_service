"""Cassandra-backed article storage."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.feed_bucket import cursor_from_ms, to_ms
from app.modules.news.stores.base import StoredArticle, TagSummary


def _epoch(dt: datetime | None) -> int:
    """UTC epoch seconds from a stored timestamp. The Cassandra driver returns timezone-NAIVE datetimes that are already UTC; calling .timestamp() directly would make Python assume the server's local zone and shift the value (which is why 'Xh ago' looked wrong on non-UTC hosts)."""
    if dt is None:
        return 0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _feed_row_to_stored(row: Any) -> StoredArticle:
    """Map a feed/tag-listing row (ArticlesStmts.LIST_PUBLISHED_PAGE or ArticleTagIndexStmts.LIST_PAGE/LIST_RECENT) to StoredArticle.

    These queries select `translated_titles` (lang -> JSON {title, summary}),
    not the full `translations` map -- migration 087, see
    LIST_PUBLISHED_PAGE's own comment. `translations` is left unset here
    (the row has no such attribute at all, since it isn't in the SELECT
    list); only `_full_row_to_stored`/`_articles_row_to_stored` below, which
    read a complete row, populate it.
    """
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
        translated_titles=dict(row.translated_titles)
        if getattr(row, "translated_titles", None)
        else None,
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
    return _build_stored_article(
        row, translations=dict(row.translations) if row.translations else None
    )


def _build_stored_article(row: Any, *, translations: dict[str, str] | None) -> StoredArticle:  # noqa: ANN401 -- duck-typed Cassandra row, matches _articles_row_to_stored's existing row: Any
    """Shared StoredArticle construction for a full `articles` row -- GET_FULL_BY_ID, GET_BY_ID_NO_TRANSLATIONS, and GET_BY_ID_WITH_TRANSLATION all select the same other columns, so only `translations` differs by caller (see `_articles_row_to_stored` / `_detail_row_to_stored`). Callers have already filtered out status='deleted'."""
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
        translations=translations,
        updated_at_epoch=_epoch(row.updated_at) or None,
        first_published_at_epoch=_epoch(row.first_published_at) or None,
        draft=row.status == "draft",
    )


def _detail_row_to_stored(row: Any, *, lang: str | None) -> StoredArticle | None:  # noqa: ANN401 -- duck-typed Cassandra row, matches _articles_row_to_stored's existing row: Any
    """Map a single-article DETAIL row (ArticlesStmts.GET_BY_ID_NO_TRANSLATIONS or GET_BY_ID_WITH_TRANSLATION) to StoredArticle, or None if it's not a publicly-fetchable row.

    Same status='deleted' filter / status='draft' flagging as
    `_articles_row_to_stored` (see its own docstring) -- both statements
    select the identical other columns as GET_FULL_BY_ID, just not the full
    `translations` map.

    GET_BY_ID_NO_TRANSLATIONS rows have no `translations` attribute at all
    (not in the SELECT list) -- `translations` comes back None, matching
    `_to_detail`'s "no lang requested" path, which never reads it anyway.
    GET_BY_ID_WITH_TRANSLATION rows carry `translations` as a single JSON
    STRING (the `translations[?]` map-element projection, aliased back to
    the column name `translations`), not a map -- this rebuilds it into the
    same `{lang: blob}` shape NewsService._to_detail already reads via
    `article.translations[lang]`, so its "no stored translation for this
    lang -> fall back to English" logic is unchanged.
    """
    if row.status == "deleted":
        return None
    blob = getattr(row, "translations", None)
    translations = {lang: blob} if (lang and blob) else None
    return _build_stored_article(row, translations=translations)


class CassandraArticleStore:
    """Cassandra-backed article storage."""

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
                    ArticlesStmts.LIST_PUBLISHED_PAGE,
                    (years[index + 1], cursor_dt, remaining_after),
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
        from algorand_shared.article_statements import ArticlesStmts

        from app.core.cassandra import get_cassandra_session

        clean = (slug or "").strip().lower()
        if not clean:
            return None
        row = get_cassandra_session().execute(ArticlesStmts.GET_BY_SLUG, (clean,)).one()
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

    def get_detail(
        self,
        article_id: str,
        *,
        lang: str | None = None,
        overlap: Callable[[], Any] | None = None,
    ) -> StoredArticle | None:
        """Fetch one article for the single-article DETAIL read path (NewsService._fetch_detail / get_article / get_article_ignoring_draft_gate), projecting only the translation `lang` actually needs instead of `get()`'s complete `translations` map.

        This is a NEW method alongside `get()`, not a change to it -- `get()`
        keeps reading GET_FULL_BY_ID exactly as before for its other callers
        (translation_langs_for needs every stored language's key, admin/
        backfill tools need the complete row). See
        ArticlesStmts.GET_BY_ID_NO_TRANSLATIONS / GET_BY_ID_WITH_TRANSLATION
        for the read-cost reasoning; `_detail_row_to_stored` builds a
        StoredArticle whose `translations` carries the same `{lang: blob}`
        shape NewsService._to_detail already reads, so its "no stored
        translation for this lang -> fall back to English" behavior is
        unchanged -- this is a read-cost optimization only.
        """
        from algorand_shared.article_statements import ArticlesStmts

        from app.core.cassandra import execute_then

        try:
            aid = UUID(article_id)
        except ValueError:
            return None
        if lang:
            row = execute_then(
                ArticlesStmts.GET_BY_ID_WITH_TRANSLATION, (lang, aid), overlap=overlap
            ).one()
        else:
            row = execute_then(
                ArticlesStmts.GET_BY_ID_NO_TRANSLATIONS, (aid,), overlap=overlap
            ).one()
        if row is None:
            return None
        return _detail_row_to_stored(row, lang=lang)

    def _fetch_many(
        self,
        article_ids: list[str],
        *,
        stmt: Any,  # noqa: ANN401 -- PreparedStatement, typed Any to avoid a TYPE_CHECKING import just for this helper
        args_for: Callable[[UUID], tuple],
        mapper: Callable[[Any], StoredArticle | None],
    ) -> dict[str, StoredArticle]:
        """Shared id-parsing + concurrent-fetch + row-mapping loop behind get_many/get_many_detail."""
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
                stmt, [args_for(aid) for _, aid in pairs], raise_on_error=False
            ),
            strict=True,
        ):
            if not ok:
                continue
            row = result.one() if hasattr(result, "one") else None
            if row is not None:
                stored = mapper(row)
                if stored is not None:
                    out[raw] = stored
        return out

    def get_many(self, article_ids: list[str]) -> dict[str, StoredArticle]:
        """Fetch many articles by id concurrently; missing ids are omitted."""
        from algorand_shared.article_statements import ArticlesStmts

        return self._fetch_many(
            article_ids,
            stmt=ArticlesStmts.GET_FULL_BY_ID,
            args_for=lambda aid: (aid,),
            mapper=_articles_row_to_stored,
        )

    def get_many_detail(
        self, article_ids: list[str], *, lang: str | None = None
    ) -> dict[str, StoredArticle]:
        """Fetch many articles for the bulk DETAIL read path (NewsService.get_articles -- RSS feed / llms-full.txt), projecting only the translation `lang` needs instead of `get_many()`'s complete `translations` map.

        A NEW method alongside `get_many()`, not a change to it --
        `get_many()` keeps reading GET_FULL_BY_ID exactly as before for its
        other caller (admin's list_draft_articles, which needs the complete
        row shape). Same reasoning as `get_detail`.
        """
        from algorand_shared.article_statements import ArticlesStmts

        if lang:
            return self._fetch_many(
                article_ids,
                stmt=ArticlesStmts.GET_BY_ID_WITH_TRANSLATION,
                args_for=lambda aid: (lang, aid),
                mapper=lambda row: _detail_row_to_stored(row, lang=lang),
            )
        return self._fetch_many(
            article_ids,
            stmt=ArticlesStmts.GET_BY_ID_NO_TRANSLATIONS,
            args_for=lambda aid: (aid,),
            mapper=lambda row: _detail_row_to_stored(row, lang=None),
        )

    def list_by_tag_page(
        self, tag: str, *, limit: int = 50, cursor_epoch_ms: int | None = None
    ) -> tuple[list[StoredArticle], int | None]:
        """Keyset-paginated feed of published articles carrying `tag`, from the maintained `articles_by_tag` index (migration 073) -- a direct single-partition read, not a scan-and-filter over the whole feed.

        Same cursor convention as list_feed_page: cursor_epoch_ms is the
        published_at (ms) of the last item on the previous page, defaulting
        to "just past now" for the first page.
        """
        from algorand_shared.article_statements import ArticleTagIndexStmts

        from app.core.cassandra import get_cassandra_session

        clean = (tag or "").strip().lower()
        if not clean:
            return [], None
        cursor_dt = cursor_from_ms(cursor_epoch_ms)
        rows = list(
            get_cassandra_session().execute(
                ArticleTagIndexStmts.LIST_PAGE, (clean, cursor_dt, limit)
            )
        )
        items = [_feed_row_to_stored(row) for row in rows]
        last_dt = rows[-1].published_at if rows else None
        next_cursor = to_ms(last_dt) if (len(items) >= limit and last_dt) else None
        return items, next_cursor

    def tag_summary(self, *, sample_limit: int = 200) -> list[TagSummary]:
        """Per-tag (count, last_epoch, sample article ids) from `articles_by_tag`, replacing the old 500-row-scan-plus-500-point-reads tag_stats path with: one DISTINCT scan for the (small, stable) tag universe, then a COUNT + a bounded LIST fanned out concurrently per tag.

        ``sample_limit`` bounds the per-tag article-id sample used to sum
        view counts and find the last-seen epoch -- irrelevant at this
        platform's real per-tag volume, a safety cap against a pathological
        single tag dominating the corpus.

        The fan-out itself (one COUNT + one LIST_RECENT per tag, every call)
        is cached (``app.core.cache.cached_json``, keyed on ``sample_limit``
        so a caller asking for a different sample size can't be served a
        mismatched cached one) -- this runs unconditionally on every call
        with no bound on tag count, and tag_stats (its only production
        caller) is itself only cached at the HTTP route layer, not here at
        the source of the actual Cassandra fan-out.
        """
        from app.core.cache import cached_json

        def compute() -> list[dict]:
            from algorand_shared.article_statements import ArticleTagIndexStmts

            from app.core.cassandra import execute_parallel_with_args, get_cassandra_session

            tags = sorted(
                {
                    row.tag
                    for row in get_cassandra_session().execute(ArticleTagIndexStmts.LIST_TAGS)
                    if row.tag
                }
            )
            if not tags:
                return []
            count_results = execute_parallel_with_args(
                ArticleTagIndexStmts.COUNT, [(t,) for t in tags], raise_on_error=False
            )
            sample_results = execute_parallel_with_args(
                ArticleTagIndexStmts.LIST_RECENT,
                [(t, sample_limit) for t in tags],
                raise_on_error=False,
            )
            out: list[dict] = []
            for tag, (count_ok, count_res), (sample_ok, sample_res) in zip(
                tags, count_results, sample_results, strict=True
            ):
                count = 0
                if count_ok:
                    count_row = count_res.one()
                    if count_row is not None:
                        count = int(count_row.count)
                rows = list(sample_res) if sample_ok else []
                last_epoch = _epoch(rows[0].published_at) if rows else 0
                out.append(
                    {
                        "tag": tag,
                        "count": count,
                        "last_epoch": last_epoch,
                        "article_ids": [str(r.article_id) for r in rows],
                    }
                )
            return out

        data = cached_json(f"news:tag-summary:{sample_limit}", 600, compute)
        return [TagSummary(**entry) for entry in data]
