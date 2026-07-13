from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.core.config import NEWS_FEED_BUCKET
from app.core.feed_bucket import feed_month, months_back


@dataclass(frozen=True)
class FeedArticleRow:
    article_id: str
    service_id: str
    title: str
    summary: str
    published_at_epoch: int
    translations: dict[str, str] | None = None


@dataclass(frozen=True)
class ArticleDetail:
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
    return sum(1 for row in list_feed_articles(limit=limit) if row.service_id == service_id)


def count_articles_published_on_utc_day(*, day_start_epoch: int, limit: int = 500) -> int:
    """Count feed articles published on or after UTC midnight for that day."""
    return sum(
        1 for row in list_feed_articles(limit=limit) if row.published_at_epoch >= day_start_epoch
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
        published_at = row.published_at
        if not published_at:
            continue
        if int(published_at.timestamp()) < day_start_epoch:
            continue
        tags = row.tags or []
        normalized = {str(t).lower() for t in tags}
        if needle in normalized:
            count += 1
    return count


def list_feed_articles(*, bucket: str = NEWS_FEED_BUCKET, limit: int = 100) -> list[FeedArticleRow]:
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
        items.append(
            FeedArticleRow(
                article_id=str(row.article_id),
                service_id=row.service_id,
                title=row.title,
                summary=row.summary or "",
                published_at_epoch=epoch,
                translations=dict(raw_translations) if raw_translations else None,
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
    """
    Store article in articles_by_id; optionally publish to articles_feed.
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
    Also stamps updated_at so the revision surfaces as dateModified."""
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
    session.execute(
        ArticleStmts.UPDATE, (title, summary, body, tag_list, updated_at, aid)
    )
    session.execute(
        FeedStmts.INSERT_BASIC,
        (
            feed_month(published_at),
            published_at,
            aid,
            existing.service_id,
            title,
            summary,
            tag_list,
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
) -> bool:
    """Swap a published article's content in place (approved recompose): same
    article_id, same URL, same published_at — new prose, tags and art, with
    updated_at stamped and stale translations cleared (the translation of the
    OLD prose must not keep serving; re-enqueue after this).

    Feed PK precision rule as update_article_image: reuse the raw published_at
    verbatim, never reconstruct from an epoch."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts, FeedStmts

    try:
        aid = UUID(article_id)
    except ValueError:
        return False
    session = get_cassandra_session()
    row = session.execute(ArticleStmts.GET_PUBLISHED_AT, (aid,)).one()
    if row is None or row.published_at is None:
        return False
    published_at = row.published_at
    existing = get_article(article_id)
    if existing is None:
        return False
    updated_at = datetime.now(tz=UTC)
    image = image_url or None
    session.execute(
        ArticleStmts.UPDATE_CONTENT_FULL,
        (title, summary, body, tags, image, updated_at, aid),
    )
    session.execute(ArticleStmts.CLEAR_TRANSLATIONS, (aid,))
    bucket = feed_month(published_at)
    session.execute(
        FeedStmts.UPDATE_CONTENT_FULL,
        (title, summary, tags, image, updated_at, bucket, published_at, aid),
    )
    session.execute(FeedStmts.CLEAR_TRANSLATIONS, (bucket, published_at, aid))
    return True


def update_article_image(article_id: str, image_url: str) -> bool:
    """Set an article's image_url in both the detail row and the feed projection.
    Used to backfill stories that published without a hero image.

    NOTE: the feed PK includes published_at at FULL (ms) precision — we read the
    raw timestamp from articles_by_id and reuse it verbatim. Reconstructing it
    from a seconds-truncated epoch would miss the real clustering key and upsert a
    phantom row with null service_id/title (which then 500s the feed)."""
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
    session.execute(
        FeedStmts.UPDATE_IMAGE,
        (image_url, feed_month(published_at), published_at, aid),
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
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts, FeedStmts
    from uuid import UUID

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

    session.execute(ArticleStmts.UPDATE_TRANSLATIONS, (translations, aid))
    session.execute(
        FeedStmts.UPDATE_TRANSLATIONS,
        (translations, feed_month(published_at), published_at, aid),
    )
    return True
