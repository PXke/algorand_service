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


def get_article(article_id: str) -> ArticleDetail | None:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    try:
        aid = UUID(article_id)
    except ValueError:
        return None
    row = session.execute(
        """
        SELECT article_id, service_id, title, summary, body,
               trigger_txid, trigger_round, source_url, published_at
        FROM articles_by_id
        WHERE article_id = %s
        """,
        (aid,),
    ).one()
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
    )


def article_exists(article_id: str | UUID) -> bool:
    from app.core.cassandra import get_cassandra_session

    try:
        aid = article_id if isinstance(article_id, UUID) else UUID(str(article_id))
    except ValueError:
        return False
    session = get_cassandra_session()
    row = session.execute(
        "SELECT article_id FROM articles_by_id WHERE article_id = %s",
        (aid,),
    ).one()
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

    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    rows = []
    for mbucket in months_back(datetime.now(tz=UTC), 2):
        page = session.execute(
            """
            SELECT published_at, tags
            FROM articles_feed
            WHERE bucket = %s
            LIMIT %s
            """,
            (mbucket, limit),
        )
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

    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    rows = []
    for mbucket in months_back(datetime.now(tz=UTC), 18):
        if len(rows) >= limit:
            break
        page = session.execute(
            """
            SELECT article_id, service_id, title, summary, published_at
            FROM articles_feed
            WHERE bucket = %s
            LIMIT %s
            """,
            (mbucket, limit - len(rows)),
        )
        rows.extend(list(page))
    items: list[FeedArticleRow] = []
    for row in rows:
        published_at = row.published_at
        epoch = int(published_at.timestamp()) if published_at else 0
        items.append(
            FeedArticleRow(
                article_id=str(row.article_id),
                service_id=row.service_id,
                title=row.title,
                summary=row.summary or "",
                published_at_epoch=epoch,
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
) -> tuple[str, bool]:
    """
    Store article in articles_by_id; optionally publish to articles_feed.
    Returns (article_id, feed_published).
    """
    from app.core.cassandra import get_cassandra_session

    article_id = article_id or uuid.uuid4()
    published_at = datetime.now(tz=UTC)
    tag_list = list(tags or [])
    image = image_url or None

    session = get_cassandra_session()
    session.execute(
        """
        INSERT INTO articles_by_id (
          article_id, service_id, title, summary, body,
          trigger_txid, trigger_round, source_url, published_at, tags, image_url
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
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
        ),
    )
    if publish_to_feed:
        session.execute(
            """
            INSERT INTO articles_feed (
              bucket, published_at, article_id, service_id, title, summary, tags,
              image_url, source_url
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
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
    """Update article in place; refresh feed row at original published_at."""
    from app.core.cassandra import get_cassandra_session

    existing = get_article(article_id)
    if existing is None:
        return False

    try:
        aid = UUID(article_id)
    except ValueError:
        return False

    published_at = datetime.fromtimestamp(existing.published_at_epoch, tz=UTC)
    tag_list = list(tags) if tags is not None else None
    if tag_list is None:
        from app.core.cassandra import get_cassandra_session as gcs

        row = gcs().execute(
            "SELECT tags FROM articles_by_id WHERE article_id = %s",
            (aid,),
        ).one()
        tag_list = list(row.tags or []) if row else []
    if "updated" not in {t.lower() for t in tag_list}:
        tag_list = [*tag_list, "updated"]

    session = get_cassandra_session()
    session.execute(
        """
        UPDATE articles_by_id
        SET title = %s, summary = %s, body = %s, tags = %s
        WHERE article_id = %s
        """,
        (title, summary, body, tag_list, aid),
    )
    session.execute(
        """
        INSERT INTO articles_feed (
          bucket, published_at, article_id, service_id, title, summary, tags
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            feed_month(published_at),
            published_at,
            aid,
            existing.service_id,
            title,
            summary,
            tag_list,
        ),
    )
    return True


def update_article_image(article_id: str, image_url: str) -> bool:
    """Set an article's image_url in both the detail row and the feed projection.
    Used to backfill stories that published without a hero image.

    NOTE: the feed PK includes published_at at FULL (ms) precision — we read the
    raw timestamp from articles_by_id and reuse it verbatim. Reconstructing it
    from a seconds-truncated epoch would miss the real clustering key and upsert a
    phantom row with null service_id/title (which then 500s the feed)."""
    from app.core.cassandra import get_cassandra_session

    if not image_url:
        return False
    try:
        aid = UUID(article_id)
    except ValueError:
        return False
    session = get_cassandra_session()
    row = session.execute(
        "SELECT published_at FROM articles_by_id WHERE article_id = %s", (aid,)
    ).one()
    if row is None or row.published_at is None:
        return False
    published_at = row.published_at  # full-precision datetime, matches the feed PK
    session.execute(
        "UPDATE articles_by_id SET image_url = %s WHERE article_id = %s",
        (image_url, aid),
    )
    session.execute(
        "UPDATE articles_feed SET image_url = %s "
        "WHERE bucket = %s AND published_at = %s AND article_id = %s",
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

    session = get_cassandra_session()
    session.execute(
        """
        INSERT INTO service_events (
          service_id, occurred_at, event_id, txid, round, match_kind, match_value
        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
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
