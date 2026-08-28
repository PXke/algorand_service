"""Cassandra reads/writes for published article rows."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import nh3
from algorand_shared.article_transitions import transition_article_status
from algorand_shared.feed_cache import invalidate_feed_first_page
from algorand_shared.slugs import (
    ensure_article_slug,
)

from app.core.config import NEWS_FEED_BUCKET

logger = logging.getLogger(__name__)

# Same allowlist as backend/app/core/sanitize.py's sanitize_markdown_body --
# kept as a separate constant here because backend and workers are separate
# deployable services with no shared import path for this (both have a
# top-level `app` package, so `app.core.sanitize` from workers would resolve
# to workers' own app.core, not backend's). Keep the two lists in sync by
# hand if the allowlist ever changes.
_SANITIZE_ALLOWED_TAGS = {
    "p",
    "br",
    "hr",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "del",
    "ins",
    "mark",
    "sub",
    "sup",
    "blockquote",
    "q",
    "cite",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "a",
    "img",
    "code",
    "pre",
    "kbd",
    "samp",
    "table",
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
    "span",
    "div",
}

_SANITIZE_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}

_SANITIZE_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def _sanitize_body(body: str) -> str:
    """Strip disallowed HTML from an article body before storage.

    Applied at every write path that lands writer/recompose output onto the
    `articles` table (insert_stored_article, replace_article_content) --
    the writer's output is LLM-composed markdown and could contain a raw
    `<script>`/`<iframe>`/`onerror=` payload that the frontend's `marked`
    renderer would otherwise pass straight through to `{@html}`.
    """
    return nh3.clean(
        body,
        tags=_SANITIZE_ALLOWED_TAGS,
        attributes=_SANITIZE_ALLOWED_ATTRIBUTES,
        url_schemes=_SANITIZE_ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    ).strip()


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
    """Load the full detail row for an article (any status -- callers include draft/recompose flows, not just published), or None if not found. 2026-08-24: reads `articles` directly (was `articles_by_id`), now that dual-write coverage is confirmed complete for every real article (see the article-table-consolidation plan's Phase 1)."""
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    try:
        aid = UUID(article_id)
    except ValueError:
        return None
    row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (aid,)).one()
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
    """True when an article with this id exists (any status). 2026-08-24: reads `articles` directly (was `articles_by_id`)."""
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    try:
        aid = article_id if isinstance(article_id, UUID) else UUID(str(article_id))
    except ValueError:
        return False
    session = get_cassandra_session()
    row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
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
    limit: int = 500,  # noqa: ARG001 -- kept for call-site compatibility; unbounded now that this is a single year-partition range scan, not a multi-bucket fan-out
) -> int:
    """Count that UTC day's published articles that include a given tag (e.g. breaking). 2026-08-24: reads `articles` directly (was `articles_feed`'s BY_BUCKET_TAGS, one query per month bucket)."""
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    day_start = datetime.fromtimestamp(day_start_epoch, tz=UTC)
    day_end = day_start + timedelta(days=1)
    session = get_cassandra_session()
    rows = session.execute(
        ArticlesStmts.COUNT_PUBLISHED_IN_RANGE, (day_start.year, day_start, day_end)
    )
    needle = tag.strip().lower()
    count = 0
    for row in rows:
        # first_published_at survives recompose re-publishes; a refresh must
        # not count as a fresh publish for the daily caps.
        published_at = row.first_published_at or row.published_at
        if not published_at or published_at < day_start:
            continue
        tags = row.tags or []
        normalized = {str(t).lower() for t in tags}
        if needle in normalized:
            count += 1
    return count


def list_feed_articles(
    *, _bucket: str = NEWS_FEED_BUCKET, limit: int = 100
) -> list[FeedArticleRow]:
    """Return the most recent `limit` published articles. 2026-08-24: reads `articles` directly (was `articles_feed`'s BY_BUCKET, one query per month bucket up to 18) -- same year-partition keyset pattern backend's CassandraArticleStore.list_feed_page already uses, almost always just the current year's partition at this platform's ~7/day volume, falling back to prior years only when the current year doesn't have `limit` rows yet."""
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    cursor_dt = datetime.now(tz=UTC)
    rows: list = []
    # 3 years back comfortably covers the old 18-month scan's intent; the
    # loop breaks as soon as `limit` is satisfied, so extra years are only
    # ever queried if genuinely needed.
    for year in range(cursor_dt.year, cursor_dt.year - 3, -1):
        remaining = limit - len(rows)
        if remaining <= 0:
            break
        rows.extend(
            session.execute(ArticlesStmts.LIST_PUBLISHED_PAGE, (year, cursor_dt, remaining))
        )
    items: list[FeedArticleRow] = []
    for row in rows:
        published_at = row.published_at
        epoch = int(published_at.timestamp()) if published_at else 0
        first_published = row.first_published_at
        items.append(
            FeedArticleRow(
                article_id=str(row.article_id),
                service_id=row.service_id,
                title=row.title,
                summary=row.summary or "",
                published_at_epoch=epoch,
                translations=dict(row.translations) if row.translations else None,
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
    interest_score: float | None = None,
    approved_at: datetime | None = None,
) -> tuple[str, bool]:
    """Store an article in the consolidated `articles` table; optionally publish it to the feed (status='published' with a claimed slug).

    ``status`` MUST reflect where this row is actually headed (draft/on_hold/
    backlog/published) -- `articles`' status is part of its partition key, so
    passing the wrong value here silently mislabels the row exactly the way
    the OLD scattered-presence-across-11-tables design used to. Callers
    creating an unlisted draft (publish_to_feed=False) MUST pass the real
    destination status explicitly; there is no safe default to infer it from.

    Returns (article_id, feed_published).
    """
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session
    from app.modules.newspaper.glossary_linker import auto_link_glossary_terms

    if not publish_to_feed and status == "published":
        # `articles.status` IS public-feed membership since the 2026-08-24
        # consolidation, so "unlisted draft" + status='published' is a
        # contradiction that puts a slug-less draft row straight onto the
        # live feed as a duplicate of the real article. Exactly this bit
        # live three times (HesabPay 08-22, AlgoRank 08-26, Al Goanna
        # 08-27): recompose_published stored its draft with
        # publish_to_feed=False but relied on this parameter's "published"
        # default. Fail SAFE rather than loud -- the compose that produced
        # this draft is minutes of LLM work, and an article wrongly parked
        # on_hold is recoverable from the admin UI, while a stray draft on
        # the public feed is a live incident.
        logger.error(
            "insert_stored_article: publish_to_feed=False with status='published' "
            "for %s -- contradictory, coercing to status='on_hold' (caller must "
            "pass the real destination status explicitly)",
            article_id,
        )
        status = "on_hold"

    body = _sanitize_body(body)
    body = auto_link_glossary_terms(body)
    article_id = article_id or uuid.uuid4()
    published_at = datetime.now(tz=UTC)
    tag_list = list(tags or [])
    image = image_url or None

    session = get_cassandra_session()
    # article_id may be REUSED (e.g. recompose-under-review overwriting its
    # own draft): published_at is part of `articles`' partition key here
    # (unlike the old articles_by_id, keyed by article_id alone), so inserting
    # at a fresh published_at without deleting any existing row first would
    # leave an orphaned duplicate behind at the old partition key.
    # GET_FULL_BY_ID (not GET_BY_ID) -- the tags-index sync below needs the
    # old row's tags, not just its partition key.
    old_row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (article_id,)).one()
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
            prompt_version or None,
            None,  # composed_by_model: not yet plumbed through this call, accepted gap
            None,  # deleted_at: never set at creation
            datetime.now(tz=UTC),  # status_updated_at: the first-ever status assignment
            interest_score,
            approved_at,
            # views: a reused article_id (recompose-under-review overwriting
            # its own row) carries the old row's tally forward -- the old
            # counter table survived recomposes (keyed by article_id alone),
            # so the folded-in column must too. Fresh inserts start NULL
            # (reads treat NULL as 0).
            getattr(old_row, "views", None) if old_row is not None else None,
        ),
    )
    # articles_by_tag dual-write (migration 073): covers both branches this
    # function can take (fresh insert, and a reused article_id whose OLD row
    # may have been a live published article, e.g. recompose-under-review
    # overwriting its own draft). No slug yet at this point -- slug is
    # claimed below (feed path only) and back-filled onto any tag-index rows
    # by _claim_slug_for_feed itself.
    try:
        from algorand_shared.article_tag_index import sync_tag_index

        sync_tag_index(
            article_id,
            old_status=old_row.status if old_row is not None else None,
            old_tags=list(old_row.tags or []) if old_row is not None else None,
            old_published_at=old_row.published_at if old_row is not None else None,
            new_status=status,
            new_tags=tag_list,
            new_published_at=published_at,
            service_id=service_id,
            title=title,
            summary=summary,
            image_url=image,
            source_url=source_url,
            slug=None,
            translations=None,
            first_published_at=None,
            updated_at=None,
        )
    except Exception:
        logger.warning("articles_by_tag dual-write failed for %s", article_id, exc_info=True)
    if publish_to_feed:
        # Claim the permanent URL slug at go-live. Held drafts deliberately do
        # NOT claim one: they may never publish, and a draft holding the clean
        # slug would push the real article to -2.
        _claim_slug_for_feed(article_id, title, published_at)
        invalidate_feed_first_page()
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
    """Update an article's content in place on the `articles` table.

    Reads the row's current partition key (status/year/published_at) first
    (2026-08-24: was `articles_by_id`) and reuses it verbatim for the UPDATE —
    published_at is part of the partition key and never moves for an in-place
    content edit. Also stamps updated_at so the revision surfaces as
    dateModified.
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

    session = get_cassandra_session()
    new_row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (aid,)).one()
    if new_row is None or new_row.published_at is None:
        return False
    tag_list = list(tags) if tags is not None else list(new_row.tags or [])
    if "updated" not in {t.lower() for t in tag_list}:
        tag_list = [*tag_list, "updated"]

    updated_at = datetime.now(tz=UTC)
    # Complete content update: an in-place edit, published_at (part of the
    # partition key) doesn't move, so a plain UPDATE suffices -- no
    # delete+insert needed. Slug is untouched by this statement (not in its
    # column list), so it stays whatever it already was -- set once at
    # publish/release time, never recomputed on a content edit.
    image = new_row.image_url or None
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
        # articles_by_tag dual-write: published_at doesn't move for an
        # in-place edit, but tags can (the "updated" tag is always appended
        # above), so the tag-index rows still need reconciling.
        from algorand_shared.article_tag_index import sync_tag_index

        sync_tag_index(
            aid,
            old_status=new_row.status,
            old_tags=list(new_row.tags or []),
            old_published_at=new_row.published_at,
            new_status=new_row.status,
            new_tags=tag_list,
            new_published_at=new_row.published_at,
            service_id=new_row.service_id,
            title=title,
            summary=summary,
            image_url=image,
            source_url=new_row.source_url,
            slug=new_row.slug,
            translations=dict(new_row.translations) if new_row.translations else None,
            first_published_at=new_row.first_published_at,
            updated_at=updated_at,
        )
    except Exception:
        logger.warning("articles dual-write update failed for %s", aid, exc_info=True)
    invalidate_feed_first_page()
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
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session
    from app.modules.newspaper.glossary_linker import auto_link_glossary_terms

    try:
        aid = UUID(article_id)
    except ValueError:
        return None
    body = _sanitize_body(body)
    body = auto_link_glossary_terms(body)
    session = get_cassandra_session()
    # 2026-08-24: reads `articles` directly (was `articles_by_id`'s
    # GET_PUBLISHED_AT_AND_DRAFT) -- "draft" is now status == 'draft' rather
    # than a separate boolean column.
    row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (aid,)).one()
    if row is None or row.published_at is None:
        return None
    old_published_at = row.published_at
    # Original publication date survives every re-publish: set once on the
    # first recompose, carried verbatim afterwards. Daily caps and hot
    # ranking read this instead of the re-stamped published_at.
    first_published_at = row.first_published_at or old_published_at
    existing = get_article(article_id)
    if existing is None:
        return None
    now = datetime.now(tz=UTC)
    image = image_url or None
    if row.status == "draft":
        _dual_write_draft_content(
            session, aid, title=title, summary=summary, body=body, tags=tags, image=image, now=now
        )
        return old_published_at
    # published_at (part of `articles`' partition key) moves on a real
    # recompose re-publish, so this is a status-preserving delete-old-
    # partition + insert-new-partition transition, not a plain UPDATE --
    # also clears translations (new prose invalidates every existing
    # translation) and carries the slug forward onto the new partition.
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
    """New `articles` table dual-write for replace_article_content's draft branch: content-only update on the row's current partition (drafted articles don't re-stamp published_at, status stays untouched -- restoring visibility stays set_article_draft's job). Best-effort.

    No articles_by_tag sync needed here: this branch only runs when
    row.status == 'draft' (checked by the caller) and status stays untouched
    -- a drafted article was never in the tag index and doesn't enter it now.
    """
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


def _claim_slug_for_feed(article_id: UUID, title: str, published_at: datetime) -> None:
    """Assign a slug (ensure_article_slug now claims AND writes it onto `articles` in one step, 2026-08-27) and sync it onto the articles_by_tag index.

    Never raises: a missing slug degrades to a uuid URL, which still resolves,
    so slug assignment must not be able to fail a publish. The articles_by_tag
    dual-write remains this function's own job: tag-index rows for this
    article (written by sync_tag_index at insert/transition time, BEFORE a
    slug existed) need the same back-fill, or a tag-filtered feed page would
    show this article with a permanently missing slug even after the main
    feed already has it. slug is a non-key column on articles_by_tag, so this
    is a plain per-tag UPDATE, not a delete+insert.
    """
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    try:
        slug = ensure_article_slug(article_id, title)
        if not slug:
            return
        session = get_cassandra_session()
        row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (article_id,)).one()
        if row is not None:
            from algorand_shared.article_tag_index import set_slug_in_tag_index

            set_slug_in_tag_index(
                article_id, tags=list(row.tags or []), published_at=published_at, slug=slug
            )
    except Exception as exc:
        logger.warning("slug claim failed for %s: %s", article_id, exc)


# ensure_article_slug moved to algorand_shared.slugs (2026-08-27) and
# re-exported above -- backend's review-approval publish path needed the
# exact same claim logic, not a second copy that could drift.


def update_article_image(article_id: str, image_url: str) -> bool:
    """Set an article's image_url on the `articles` row.

    Used to backfill stories that published without a hero image.
    """
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    if not image_url:
        return False
    try:
        aid = UUID(article_id)
    except ValueError:
        return False
    session = get_cassandra_session()
    new_row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
    if new_row is None or new_row.published_at is None:
        return False
    try:
        session.execute(
            ArticlesStmts.UPDATE_IMAGE,
            (image_url, new_row.status, new_row.year, new_row.published_at, aid),
        )
    except Exception:
        logger.warning("articles dual-write image update failed for %s", aid, exc_info=True)
    invalidate_feed_first_page()
    return True


def get_article_views(article_id: str) -> int | None:
    """Current view tally for an article (NULL column reads as 0), or None when no such article exists.

    Single fresh SAI lookup on articles.views (migration 084) -- the read
    half of flush_pending_views' read-current-total-then-patch cycle.
    """
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    try:
        aid = UUID(article_id)
    except (ValueError, TypeError):
        return None
    row = get_cassandra_session().execute(ArticlesStmts.GET_VIEWS_BY_ID, (aid,)).one()
    if row is None:
        return None
    return int(row.views) if row.views is not None else 0


def update_article_views(article_id: str, views: int) -> bool:
    """Set an article's absolute view tally on the `articles` row.

    Same shape as update_article_image: read the row's CURRENT partition/
    clustering key fresh (published_at is part of the key and moves on a
    recompose re-publish), then patch just the one column with that fresh
    key. Patching with a stale/cached key would upsert a phantom row at a
    partition nothing reads -- the exact bug class that bit articles_feed
    twice. Sole caller today is flush_pending_views (a single periodic
    beat, so no concurrent read-modify-write on the same article).

    No feed-cache invalidation here (unlike update_article_image): view
    counts are joined onto feed items at read time via get_views_bulk, not
    baked into the cached feed page, and the old counter bump never
    invalidated anything either.
    """
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    try:
        aid = UUID(article_id)
    except (ValueError, TypeError):
        return False
    session = get_cassandra_session()
    new_row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
    if new_row is None or new_row.published_at is None:
        return False
    try:
        session.execute(
            ArticlesStmts.UPDATE_VIEWS,
            (views, new_row.status, new_row.year, new_row.published_at, aid),
        )
    except Exception:
        logger.warning("articles views update failed for %s", aid, exc_info=True)
        return False
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
    """Update an article's translations map on the `articles` table."""
    from uuid import UUID

    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    try:
        aid = UUID(article_id)
    except ValueError:
        return False

    session = get_cassandra_session()

    # 2026-08-24: reads `articles` directly (was `articles_by_id`). Doubles as
    # the "does this article still exist" guard -- a translation task can
    # outlive the article it was enqueued for, and dropping the write then is
    # correct (a plain upsert would resurrect a deleted article as a
    # translations-only phantom row).
    new_row = session.execute(ArticlesStmts.GET_BY_ID, (aid,)).one()
    if new_row is None or new_row.published_at is None:
        return False

    # `articles` table update. Best-effort.
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
