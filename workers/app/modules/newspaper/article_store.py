"""Cassandra reads/writes for published article rows."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from algorand_shared.article_transitions import transition_article_status
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
    slug: str | None = None


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
        slug=getattr(row, "slug", None),
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
    status: str = "published",
    article_id: UUID | None = None,
    tags: list[str] | None = None,
    image_url: str = "",
    prompt_version: str = "",
) -> tuple[str, bool]:
    """Store article in articles_by_id; optionally publish to articles_feed.

    Also dual-writes into the new consolidated `articles` table (article-table
    consolidation, step 5) alongside the legacy tables above -- nothing reads
    from `articles` yet, this just keeps it populated so the eventual read
    cutover has data to switch onto. ``status`` MUST reflect where this row is
    actually headed (draft/on_hold/backlog/published) -- unlike
    ``publish_to_feed`` (which only controls the OLD articles_feed insert),
    `articles`' status is part of its partition key, so passing the wrong
    value here silently mislabels the row exactly the way the OLD scattered-
    presence-across-11-tables design used to. Callers creating an unlisted
    draft (publish_to_feed=False) MUST pass the real destination status
    explicitly; there is no safe default to infer it from.

    Returns (article_id, feed_published).
    """
    from algorand_shared.article_statements import ArticlesStmts
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts, FeedStmts
    from app.modules.newspaper.glossary_linker import auto_link_glossary_terms

    body = auto_link_glossary_terms(body)
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
    # article_id may be REUSED (e.g. recompose-under-review overwriting its
    # own draft): published_at is part of `articles`' partition key here
    # (unlike the old articles_by_id, keyed by article_id alone), so inserting
    # at a fresh published_at without deleting any existing row first would
    # leave an orphaned duplicate behind at the old partition key.
    old_row = session.execute(ArticlesStmts.GET_BY_ID, (article_id,)).one()
    if old_row is not None:
        session.execute(
            ArticlesStmts.DELETE,
            (old_row.status, old_row.year, old_row.published_at, article_id),
        )
    session.execute(
        ArticlesStmts.INSERT,
        (
            status,
            published_at.year,
            published_at,
            article_id,
            service_id,
            title,
            summary,
            body,
            image,
            tag_list,
            source_url,
            trigger_txid,
            trigger_round,
            None,  # slug: claimed separately below (feed path) or left unset
            None,  # translations: none at creation time
            None,  # first_published_at: NULL until a recompose re-publish sets it
            None,  # updated_at: NULL until an edit/recompose
            None,  # burst_day: set via a separate call when relevant
            prompt_version or None,
            None,  # composed_by_model: not yet plumbed through this call, accepted gap
            None,  # deleted_at: never set at creation
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
        # Claim the permanent URL slug at go-live. Held drafts deliberately do
        # NOT claim one: they may never publish, and a draft holding the clean
        # slug would push the real article to -2.
        _claim_slug_for_feed(article_id, title, published_at, status=status)
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
    from algorand_shared.article_statements import ArticlesStmts
    from app.core.cassandra import get_cassandra_session
    from app.modules.newspaper.glossary_linker import auto_link_glossary_terms

    existing = get_article(article_id)
    if existing is None:
        return False

    try:
        aid = UUID(article_id)
    except ValueError:
        return False

    body = auto_link_glossary_terms(body)
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
    # New `articles` table dual-write: in-place edit, published_at (part of
    # the partition key) doesn't move, so a plain UPDATE suffices -- no
    # delete+insert needed. Best-effort: the OLD-schema write above is
    # already durable, so a hiccup here must not undo it.
    new_row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
    if new_row is not None:
        try:
            session.execute(
                ArticlesStmts.UPDATE_CONTENT,
                (
                    title,
                    summary,
                    body,
                    tag_list,
                    image,
                    updated_at,
                    new_row.status,
                    new_row.year,
                    new_row.published_at,
                    aid,
                ),
            )
        except Exception:
            logger.warning("articles dual-write update failed for %s", aid, exc_info=True)
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
    # INSERT_FULL's own column list has no slug column (kept separate per
    # migration 056 -- see _claim_slug_for_feed), so without this the feed
    # row's slug silently stays whatever it was before this write -- fine for
    # an in-place upsert on an unchanged PK, but root-caused live 2026-08-10
    # (GSC "Page with redirect": 545 pages) as a real desync source: the
    # homepage reads slug from THIS projection, not articles_by_id, so any
    # article edited here shows uuid-form links on the homepage even though
    # articles_by_id.slug is fine, sending Google through an extra 301 on
    # every such article. Carry it explicitly.
    if existing.slug:
        session.execute(
            ArticleStmts.SET_FEED_SLUG, (existing.slug, feed_month(published_at), published_at, aid)
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

    DRAFT GUARD (2026-08-11, root-caused before it could bite live on the
    Lumi Rogue article): a drafted (admin-withdrawn) article used to get its
    feed row unconditionally rewritten and published_at re-stamped by this
    same path — a recompose approved for a withdrawn article would have
    silently un-drafted it back onto the public feed, the exact bug already
    fixed for the admin content-edit path (AdminCassandraStore._write_article)
    but not this one. Content still updates either way (that's the whole
    point of recomposing a draft to see if it's better); feed/publish
    timestamps are left untouched when drafted — restoring visibility stays
    set_article_draft's job exclusively.
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts, FeedStmts
    from app.modules.newspaper.glossary_linker import auto_link_glossary_terms

    try:
        aid = UUID(article_id)
    except ValueError:
        return None
    body = auto_link_glossary_terms(body)
    session = get_cassandra_session()
    row = session.execute(ArticleStmts.GET_PUBLISHED_AT_AND_DRAFT, (aid,)).one()
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
    if getattr(row, "draft", False):
        session.execute(
            ArticleStmts.UPDATE_CONTENT_KEEP_TIMESTAMPS,
            (title, summary, body, tags, image, now, aid),
        )
        session.execute(ArticleStmts.CLEAR_TRANSLATIONS, (aid,))
        _dual_write_draft_content(
            session, aid, title=title, summary=summary, body=body, tags=tags, image=image, now=now
        )
        return old_published_at
    session.execute(
        ArticleStmts.UPDATE_CONTENT_FULL,
        (title, summary, body, tags, image, now, first_published_at, now, aid),
    )
    session.execute(ArticleStmts.CLEAR_TRANSLATIONS, (aid,))
    session.execute(FeedStmts.DELETE, (feed_month(old_published_at), old_published_at, aid))
    _dual_write_recompose_transition(
        session,
        aid,
        title=title,
        summary=summary,
        body=body,
        tags=tags,
        image=image,
        first_published_at=first_published_at,
        now=now,
        slug=existing.slug,
    )
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
    # This path DELETEs the old feed row and INSERTs a genuinely new one at a
    # new published_at (the PK moves), so there is no existing row for
    # Cassandra to leave slug untouched on -- unlike update_article's in-place
    # upsert, every recompose unconditionally lost the feed-visible slug here
    # until this line (root-caused live 2026-08-10 alongside update_article's
    # narrower version of the same gap; see its comment for the GSC evidence).
    if existing.slug:
        session.execute(ArticleStmts.SET_FEED_SLUG, (existing.slug, feed_month(now), now, aid))
    return now


def _dual_write_draft_content(
    session: object,
    aid: UUID,
    *,
    title: str,
    summary: str,
    body: str,
    tags: list[str],
    image: str | None,
    now: datetime,
) -> None:
    """New `articles` table dual-write for replace_article_content's draft branch: content-only update on the row's current partition (drafted articles don't re-stamp published_at, status stays untouched -- restoring visibility stays set_article_draft's job). Best-effort."""
    from algorand_shared.article_statements import ArticlesStmts

    new_row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
    if new_row is None:
        return
    key = (new_row.status, new_row.year, new_row.published_at, aid)
    try:
        session.execute(ArticlesStmts.UPDATE_CONTENT, (title, summary, body, tags, image, now, *key))
        session.execute(ArticlesStmts.CLEAR_TRANSLATIONS, key)
    except Exception:
        logger.warning("articles dual-write update failed for %s", aid, exc_info=True)


def _dual_write_recompose_transition(
    session: object,
    aid: UUID,
    *,
    title: str,
    summary: str,
    body: str,
    tags: list[str],
    image: str | None,
    first_published_at: datetime,
    now: datetime,
    slug: str | None,
) -> None:
    """New `articles` table dual-write for replace_article_content's real-recompose branch: a full status-preserving transition, since published_at (part of the partition key) moves. Best-effort."""
    from algorand_shared.article_statements import ArticlesStmts

    try:
        transition_article_status(
            aid,
            new_published_at=now,
            title=title,
            summary=summary,
            body=body,
            tags=tags,
            image_url=image,
            first_published_at=first_published_at,
            updated_at=now,
            translations=None,
        )
        if slug:
            new_row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
            if new_row is not None:
                session.execute(
                    ArticlesStmts.SET_SLUG,
                    (slug, new_row.status, new_row.year, new_row.published_at, aid),
                )
    except Exception:
        logger.warning("articles dual-write transition failed for %s", aid, exc_info=True)


def _claim_slug_for_feed(
    article_id: UUID, title: str, published_at: datetime, *, status: str = "published"
) -> None:
    """Assign a slug and mirror it onto the feed row (and the new `articles` row).

    Never raises: a missing slug degrades to a uuid URL, which still resolves,
    so slug assignment must not be able to fail a publish.
    """
    from algorand_shared.article_statements import ArticlesStmts
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts

    try:
        slug = ensure_article_slug(article_id, title)
        if slug:
            session = get_cassandra_session()
            session.execute(
                ArticleStmts.SET_FEED_SLUG,
                (slug, feed_month(published_at), published_at, article_id),
            )
            session.execute(
                ArticlesStmts.SET_SLUG,
                (slug, status, published_at.year, published_at, article_id),
            )
    except Exception as exc:
        logger.warning("slug claim failed for %s: %s", article_id, exc)


def ensure_article_slug(article_id: str | UUID, title: str) -> str | None:
    """Claim a permanent URL slug for an article, or return the one it already has.

    Called at publish. Without this, articles created after migration 056 have
    no slug and fall back to a uuid URL — which does not break anything visibly,
    so the migration would have quietly stopped applying to new stories.

    Idempotent and safe under concurrency: the claim is a lightweight
    transaction (IF NOT EXISTS), so two workers racing on the same title cannot
    both take one slug — the loser tries the next suffix.
    """
    from algorand_shared.slugs import slugify, unique_slug
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleStmts

    aid = article_id if isinstance(article_id, UUID) else UUID(str(article_id))
    session = get_cassandra_session()

    existing = session.execute(ArticleStmts.GET_ARTICLE_SLUG, (aid,)).one()
    if existing and existing.slug:
        return existing.slug

    base = slugify(title) or str(aid)
    for _attempt in range(50):
        candidate = unique_slug(
            title,
            fallback=str(aid),
            is_taken=lambda s: session.execute(ArticleStmts.SLUG_TAKEN, (s,)).one() is not None,
        )
        applied = session.execute(
            ArticleStmts.CLAIM_SLUG, (candidate, aid, datetime.now(tz=UTC))
        ).one()
        # LWT returns [applied] — False means another worker took it first.
        if applied is None or getattr(applied, "applied", True):
            session.execute(ArticleStmts.SET_ARTICLE_SLUG, (candidate, aid))
            return candidate
    logger.warning("could not claim a slug for %s (base=%s)", aid, base)
    return None


def update_article_image(article_id: str, image_url: str) -> bool:
    """Set an article's image_url in both the detail row and the feed projection.

    Used to backfill stories that published without a hero image.

    NOTE: the feed PK includes published_at at FULL (ms) precision — we read the
    raw timestamp from articles_by_id and reuse it verbatim. Reconstructing it
    from a seconds-truncated epoch would miss the real clustering key and upsert a
    phantom row with null service_id/title (which then 500s the feed).
    """
    from algorand_shared.article_statements import ArticlesStmts
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
    # New `articles` table dual-write. Best-effort.
    new_row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
    if new_row is not None:
        try:
            session.execute(
                ArticlesStmts.UPDATE_IMAGE,
                (image_url, new_row.status, new_row.year, new_row.published_at, aid),
            )
        except Exception:
            logger.warning("articles dual-write image update failed for %s", aid, exc_info=True)
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
    image_url: str = "",
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
        image_url=image_url,
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

    from algorand_shared.article_statements import ArticlesStmts
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
    # New `articles` table dual-write. Best-effort.
    new_row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
    if new_row is not None:
        try:
            session.execute(
                ArticlesStmts.UPDATE_TRANSLATIONS,
                (translations, new_row.status, new_row.year, new_row.published_at, aid),
            )
        except Exception:
            logger.warning(
                "articles dual-write translations update failed for %s", aid, exc_info=True
            )
    return True
