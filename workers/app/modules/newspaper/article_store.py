"""Cassandra reads/writes for published article rows."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.core.config import NEWS_FEED_BUCKET
from app.core.feed_bucket import feed_month, months_back

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedArticleRow:
    """One article row as it appears in the feed projection."""
    article_id: str
    service_id: str
    title: str
    summary: str
    published_at_epoch: int
    translations: dict[str, str] | None = None
    # Original publication moment; differs from published_at_epoch only after
    # a recompose re-publish (which re-stamps published_at). None = never
    # recomposed.
    first_published_at_epoch: int | None = None


@dataclass(frozen=True)
class ArticleDetail:
    """Full article detail for the article-detail route."""
    article_id: str
    service_id: str
    title: str
    summary: str
    body: str
    published_at_epoch: int
    trigger_txid: str
    trigger_round: int
    source_url: str
    prompt_version: str = ""
    translations: dict[str, str] | None = None
    tags: tuple[str, ...] = ()


def get_article(article_id: str) -> ArticleDetail | None:
    """Load the full detail row for a published article, or None if not found."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts

    session = get_cassandra_session()
    try:
        aid = UUID(article_id)
    except ValueError:
        return None
    row = session.execute(ArticleStmts.GET_BY_ID, (aid,)).one()
    if row is None:
        return None
    published_at = row.published_at
    epoch = int(published_at.timestamp()) if published_at else 0
    return ArticleDetail(
        article_id=str(row.article_id),
        service_id=row.service_id,
        title=row.title,
        summary=row.summary or "",
        body=row.body or "",
        published_at_epoch=epoch,
        trigger_txid=row.trigger_txid or "",
        trigger_round=int(row.trigger_round) if row.trigger_round is not None else 0,
        source_url=row.source_url or "",
        prompt_version=getattr(row, "prompt_version", "") or "",
        translations=dict(row.translations) if row.translations else None,
        tags=tuple(row.tags or []),
    )


def article_exists(article_id: str | UUID) -> bool:
    """True when an article with this id has been published."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts

    try:
        aid = article_id if isinstance(article_id, UUID) else UUID(str(article_id))
    except ValueError:
        return False
    session = get_cassandra_session()
    row = session.execute(ArticleStmts.EXISTS, (aid,)).one()
    return row is not None


def count_articles_for_service(service_id: str, *, limit: int = 500) -> int:
    """Count how many of the most recent feed articles belong to this service."""
    return sum(1 for row in list_feed_articles(limit=limit) if row.service_id == service_id)


def count_articles_published_on_utc_day(*, day_start_epoch: int, limit: int = 500) -> int:
    """Count feed articles FIRST published on or after UTC midnight for that day.

    Uses first_published_at when present: a recompose re-publish re-stamps
    published_at to the apply time, and counting the refresh as a new publish
    would burn a real slot out of the daily cap.
    """
    return sum(
        1
        for row in list_feed_articles(limit=limit)
        if (row.first_published_at_epoch or row.published_at_epoch) >= day_start_epoch
    )


def count_feed_articles_with_tag_on_day(
    *,
    tag: str,
    day_start_epoch: int,
    limit: int = 500,
) -> int:
    """Count today's feed rows that include a given tag (e.g. breaking)."""
    from datetime import UTC, datetime

    from app.core.cassandra import execute_parallel_with_args
    from app.core.statements import FeedStmts

    buckets = list(months_back(datetime.now(tz=UTC), 2))
    rows = []
    for ok, page in execute_parallel_with_args(
        FeedStmts.BY_BUCKET_TAGS, [(mbucket, limit) for mbucket in buckets]
    ):
        if ok:
            rows.extend(list(page))
    needle = tag.strip().lower()
    count = 0
    for row in rows:
        # first_published_at survives recompose re-publishes; a refresh must
        # not count as a fresh publish for the daily caps.
        published_at = getattr(row, "first_published_at", None) or row.published_at
        if not published_at:
            continue
        if int(published_at.timestamp()) < day_start_epoch:
            continue
        tags = row.tags or []
        normalized = {str(t).lower() for t in tags}
        if needle in normalized:
            count += 1
    return count


def list_feed_articles(
    *, _bucket: str = NEWS_FEED_BUCKET, limit: int = 100
) -> list[FeedArticleRow]:
    """Return the most recent feed articles across the trailing 18 monthly buckets."""
    from datetime import UTC, datetime

    from app.core.cassandra import execute_parallel_with_args
    from app.core.statements import FeedStmts

    # Fan the per-month bucket reads out concurrently (newest bucket first), then
    # take the first `limit` rows. Each bucket is capped at `limit` so the union is
    # at most buckets*limit before truncation.
    buckets = list(months_back(datetime.now(tz=UTC), 18))
    rows = []
    for ok, page in execute_parallel_with_args(
        FeedStmts.BY_BUCKET, [(mbucket, limit) for mbucket in buckets]
    ):
        if ok:
            rows.extend(list(page))
        if len(rows) >= limit:
            break
    rows = rows[:limit]
    items: list[FeedArticleRow] = []
    for row in rows:
        published_at = row.published_at
        epoch = int(published_at.timestamp()) if published_at else 0
        # FeedStmts.BY_BUCKET doesn't select translations at all (unlike the
        # other call sites) — getattr(default=None), NOT row.translations
        # directly, or this raises AttributeError on every row and silently
        # breaks every caller (count_articles_published_on_utc_day and thus
        # the daily publish cap — found live 2026-07-13, self-inflicted by
        # the translations JSON-serialization fix earlier the same day).
        raw_translations = getattr(row, "translations", None)
        first_published = getattr(row, "first_published_at", None)
        items.append(
            FeedArticleRow(
                article_id=str(row.article_id),
                service_id=row.service_id,
                title=row.title,
                summary=row.summary or "",
                published_at_epoch=epoch,
                translations=dict(raw_translations) if raw_translations else None,
                first_published_at_epoch=(
                    int(first_published.timestamp()) if first_published else None
                ),
            )
        )
    return items


def insert_stored_article(
    *,
    service_id: str,
    title: str,
    summary: str,
    body: str,
    trigger_txid: str,
    trigger_round: int,
    source_url: str,
    publish_to_feed: bool = True,
    article_id: UUID | None = None,
    tags: list[str] | None = None,
    image_url: str = "",
    prompt_version: str = "",
) -> tuple[str, bool]:
    """Store article in articles_by_id; optionally publish to articles_feed.

    Returns (article_id, feed_published).
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts, FeedStmts

    article_id = article_id or uuid.uuid4()
    published_at = datetime.now(tz=UTC)
    tag_list = list(tags or [])
    image = image_url or None

    session = get_cassandra_session()
    session.execute(
        ArticleStmts.INSERT,
        (
            article_id,
            service_id,
            title,
            summary,
            body,
            trigger_txid,
            trigger_round,
            source_url,
            published_at,
            tag_list,
            image,
            prompt_version or None,
        ),
    )
    if publish_to_feed:
        session.execute(
            FeedStmts.INSERT,
            (
                feed_month(published_at),
                published_at,
                article_id,
                service_id,
                title,
                summary,
                tag_list,
                image,
                source_url or None,
            ),
        )
        return str(article_id), True
    return str(article_id), False


def insert_article(
    *,
    service_id: str,
    title: str,
    summary: str,
    body: str,
    trigger_txid: str,
    trigger_round: int,
    source_url: str,
    article_id: UUID | None = None,
    tags: list[str] | None = None,
    image_url: str = "",
    prompt_version: str = "",
) -> str:
    """Insert a new article and publish it to the feed, returning the article id."""
    aid, _ = insert_stored_article(
        service_id=service_id,
        title=title,
        summary=summary,
        body=body,
        trigger_txid=trigger_txid,
        trigger_round=trigger_round,
        source_url=source_url,
        publish_to_feed=True,
        article_id=article_id,
        tags=tags,
        image_url=image_url,
        prompt_version=prompt_version,
    )
    return aid


def update_article(
    *,
    article_id: str,
    title: str,
    summary: str,
    body: str,
    tags: list[str] | None = None,
) -> bool:
    """Update article in place; refresh feed row at original published_at.

    The feed PK's published_at is FULL (ms) precision — read the raw timestamp
    from articles_by_id and reuse it verbatim (see update_article_image). This
    function used to reconstruct it from the seconds-truncated epoch, which
    upserts a phantom feed row with null service_id/title that 500s the feed.
    Also stamps updated_at so the revision surfaces as dateModified.
    """
    from app.core.cassandra import get_cassandra_session

    existing = get_article(article_id)
    if existing is None:
        return False

    try:
        aid = UUID(article_id)
    except ValueError:
        return False

    from app.core.statements import ArticleStmts, FeedStmts

    session = get_cassandra_session()
    pub_row = session.execute(ArticleStmts.GET_PUBLISHED_AT, (aid,)).one()
    if pub_row is None or pub_row.published_at is None:
        return False
    published_at = pub_row.published_at  # full precision, matches the feed PK
    tag_list = list(tags) if tags is not None else None
    if tag_list is None:
        row = session.execute(ArticleStmts.GET_TAGS, (aid,)).one()
        tag_list = list(row.tags or []) if row else []
    if "updated" not in {t.lower() for t in tag_list}:
        tag_list = [*tag_list, "updated"]

    updated_at = datetime.now(tz=UTC)
    session.execute(ArticleStmts.UPDATE, (title, summary, body, tag_list, updated_at, aid))
    # Complete feed row, not a partial one: this INSERT is an upsert, and on a
    # deleted feed row a partial write resurrects a degraded article (no image/
    # source). Harmless on live rows — Cassandra INSERT leaves unlisted columns
    # untouched, but every listed one must carry the real value.
    image_row = session.execute(ArticleStmts.GET_IMAGE, (aid,)).one()
    image = (image_row.image_url or None) if image_row else None
    session.execute(
        FeedStmts.INSERT_FULL,
        (
            feed_month(published_at),
            published_at,
            aid,
            existing.service_id,
            title,
            summary,
            tag_list,
            image,
            existing.source_url or None,
            # Carry the stored value (INSERT with null would tombstone it on
            # an article that was recomposed before this edit).
            getattr(pub_row, "first_published_at", None),
            updated_at,
        ),
    )
    return True


def replace_article_content(
    *,
    article_id: str,
    title: str,
    summary: str,
    body: str,
    tags: list[str],
    image_url: str,
) -> datetime | None:
    """Swap a published article's content in place (approved recompose): same article_id, same URL — new prose, tags and art, with stale translations cleared (the translation of the OLD prose must not keep serving; re-enqueue after this). Returns the new published_at, or None on failure.

    Recompose is a RE-publish (owner policy 2026-07-15): published_at is
    re-stamped to the apply time so the refreshed story returns to the top of
    the feed — safe because article URLs are id-based. published_at is part of
    the feed PK, so the row MOVES: the old row (located via the raw
    full-precision timestamp, never reconstructed from an epoch) is deleted
    and a COMPLETE new row inserted. Never a partial feed upsert here — one
    resurrected a deleted row without service_id and the feed API's defensive
    filter silently hid the article (incident 2026-07-15).
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts, FeedStmts

    try:
        aid = UUID(article_id)
    except ValueError:
        return None
    session = get_cassandra_session()
    row = session.execute(ArticleStmts.GET_PUBLISHED_AT, (aid,)).one()
    if row is None or row.published_at is None:
        return None
    old_published_at = row.published_at
    # Original publication date survives every re-publish: set once on the
    # first recompose, carried verbatim afterwards. Daily caps and hot
    # ranking read this instead of the re-stamped published_at.
    first_published_at = getattr(row, "first_published_at", None) or old_published_at
    existing = get_article(article_id)
    if existing is None:
        return None
    now = datetime.now(tz=UTC)
    image = image_url or None
    session.execute(
        ArticleStmts.UPDATE_CONTENT_FULL,
        (title, summary, body, tags, image, now, first_published_at, now, aid),
    )
    session.execute(ArticleStmts.CLEAR_TRANSLATIONS, (aid,))
    session.execute(FeedStmts.DELETE, (feed_month(old_published_at), old_published_at, aid))
    session.execute(
        FeedStmts.INSERT_FULL,
        (
            feed_month(now),
            now,
            aid,
            existing.service_id,
            title,
            summary,
            tags,
            image,
            existing.source_url or None,
            first_published_at,
            now,
        ),
    )
    return now


def update_article_image(article_id: str, image_url: str) -> bool:
    """Set an article's image_url in both the detail row and the feed projection.

    Used to backfill stories that published without a hero image.

    NOTE: the feed PK includes published_at at FULL (ms) precision — we read the
    raw timestamp from articles_by_id and reuse it verbatim. Reconstructing it
    from a seconds-truncated epoch would miss the real clustering key and upsert a
    phantom row with null service_id/title (which then 500s the feed).
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts, FeedStmts

    if not image_url:
        return False
    try:
        aid = UUID(article_id)
    except ValueError:
        return False
    session = get_cassandra_session()
    row = session.execute(ArticleStmts.GET_PUBLISHED_AT, (aid,)).one()
    if row is None or row.published_at is None:
        return False
    published_at = row.published_at  # full-precision datetime, matches the feed PK
    session.execute(ArticleStmts.UPDATE_IMAGE, (image_url, aid))
    feed_result = session.execute(
        FeedStmts.UPDATE_IMAGE,
        (image_url, feed_month(published_at), published_at, aid),
    )
    if not feed_result.was_applied:
        # IF EXISTS declined: no feed row at this PK (held article, deleted
        # row, or moved by a concurrent recompose). Correct no-op — the old
        # behavior upserted a phantom here.
        logger.warning(
            "update_article_image: no feed row for %s at %s — feed image skipped",
            article_id,
            published_at,
        )
    return True


def insert_article_if_absent(
    *,
    article_id: UUID,
    service_id: str,
    title: str,
    summary: str,
    body: str,
    trigger_txid: str,
    trigger_round: int,
    source_url: str,
    tags: list[str] | None = None,
    prompt_version: str = "",
) -> tuple[str, bool]:
    """Insert digest article; return (id, created). Skips when id already exists."""
    if article_exists(article_id):
        return str(article_id), False
    insert_article(
        service_id=service_id,
        title=title,
        summary=summary,
        body=body,
        trigger_txid=trigger_txid,
        trigger_round=trigger_round,
        source_url=source_url,
        article_id=article_id,
        tags=tags,
        prompt_version=prompt_version,
    )
    return str(article_id), True


def record_service_event(
    *,
    service_id: str,
    txid: str,
    round_num: int,
    match_kind: str,
    match_value: str,
) -> None:
    """Record a chain-matched service event (address/app/asset hit) for the watch feed."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceEventStmts

    session = get_cassandra_session()
    session.execute(
        ServiceEventStmts.INSERT,
        (
            service_id,
            datetime.now(tz=UTC),
            uuid.uuid4(),
            txid,
            round_num,
            match_kind,
            match_value,
        ),
    )


def update_article_translations(article_id: str, translations: dict[str, str]) -> bool:
    """Update article translations map; refresh feed row at original published_at."""
    from uuid import UUID

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts, FeedStmts

    try:
        aid = UUID(article_id)
    except ValueError:
        return False

    session = get_cassandra_session()

    # We must fetch the exact published_at timestamp to update the feed PK
    row = session.execute(ArticleStmts.GET_PUBLISHED_AT, (aid,)).one()
    if row is None or row.published_at is None:
        return False
    published_at = row.published_at

    detail_result = session.execute(ArticleStmts.UPDATE_TRANSLATIONS, (translations, aid))
    if not detail_result.was_applied:
        # Article deleted after this translation was enqueued — dropping the
        # write is correct (a plain upsert resurrected phantom rows).
        logger.warning(
            "update_article_translations: article %s no longer exists — dropped",
            article_id,
        )
        return False
    feed_result = session.execute(
        FeedStmts.UPDATE_TRANSLATIONS,
        (translations, feed_month(published_at), published_at, aid),
    )
    if not feed_result.was_applied:
        # No feed row at this PK: unlisted/held article, or the row moved
        # under us (recompose re-publish re-stamps published_at and re-enqueues
        # fresh translations, so this in-flight write is stale — drop it).
        logger.warning(
            "update_article_translations: no feed row for %s at %s — feed skipped",
            article_id,
            published_at,
        )
    return True
