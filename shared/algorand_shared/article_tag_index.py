"""Maintains `articles_by_tag`, the per-tag partition index (migration 073).

Powers tag-filtered feed pages and the topic-cloud aggregate without a
cross-partition scan-and-filter.

Every write path that can change an `articles` row's (status, tags,
published_at) triple -- create, status transition, in-place content edit,
tag-only correction -- must call `sync_tag_index` right after the `articles`
write, or this index silently drifts from the table it mirrors. Shared by
backend and workers for the same reason article_transitions.py is shared:
both services perform the exact same writes against the exact same table.

Unconditional delete-every-old-tag-row + insert-every-current-tag-row (not a
diff) -- mirrors transition_article_status's own delete-old-partition +
insert-new-partition style, and stays correct even when published_at also
moved (a diff keyed only on tag text would leave a stale row at the old
published_at). Tags typically number a handful per article, so this is a few
single-partition writes, not a scan.

Best-effort throughout: articles_by_tag is a read-path optimization, not the
system of record (`articles` still is) -- callers should wrap this the same
way they already wrap every other `articles` dual-write, with
contextlib.suppress(Exception) or an equivalent try/except.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID


def _normalize_tags(tags: list[str] | None) -> list[str]:
    """Same normalization NewsService applies when matching a tag filter/aggregating tag_stats: strip + lowercase, de-duplicated, order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags or []:
        tag = (raw or "").strip().lower()
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def sync_tag_index(
    article_id: UUID,
    *,
    old_status: str | None,
    old_tags: list[str] | None,
    old_published_at: datetime | None,
    new_status: str | None,
    new_tags: list[str] | None,
    new_published_at: datetime | None,
    service_id: str | None = None,
    title: str | None = None,
    summary: str | None = None,
    image_url: str | None = None,
    source_url: str | None = None,
    slug: str | None = None,
    translations: dict | None = None,
    translated_titles: dict | None = None,
    first_published_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> None:
    """Reconcile articles_by_tag for one `articles` row write.

    Only status='published' rows are ever indexed (the only status the
    public feed/topic-cloud reads) -- a no-op, zero Cassandra round-trips, if
    the row was never published and isn't becoming published now.
    """
    if old_status != "published" and new_status != "published":
        return

    from app.core.cassandra import get_cassandra_session

    from algorand_shared.article_statements import ArticleTagIndexStmts

    session = get_cassandra_session()

    if old_status == "published" and old_published_at is not None:
        for tag in _normalize_tags(old_tags):
            session.execute(ArticleTagIndexStmts.DELETE, (tag, old_published_at, article_id))

    if new_status == "published" and new_published_at is not None:
        for tag in _normalize_tags(new_tags):
            session.execute(
                ArticleTagIndexStmts.INSERT,
                (
                    tag,
                    new_published_at,
                    article_id,
                    service_id,
                    title,
                    summary,
                    image_url,
                    source_url,
                    slug,
                    translations,
                    translated_titles,
                    first_published_at,
                    updated_at,
                    list(new_tags or []),
                ),
            )


def set_slug_in_tag_index(
    article_id: UUID, *, tags: list[str] | None, published_at: datetime, slug: str
) -> None:
    """Back-fill articles_by_tag's slug column after a deferred slug claim.

    Slugs are claimed at release time (article_store.py's
    _claim_slug_for_feed), which runs AFTER sync_tag_index has already
    written the tag-index rows -- at that point no slug exists yet, so those
    rows are written with slug=NULL. This patches them once the real slug is
    known. slug is a non-key column on articles_by_tag, so this is a plain
    per-tag UPDATE, not a delete+insert.
    """
    if not slug:
        return

    from app.core.cassandra import get_cassandra_session

    from algorand_shared.article_statements import ArticleTagIndexStmts

    session = get_cassandra_session()
    for tag in _normalize_tags(tags):
        session.execute(ArticleTagIndexStmts.SET_SLUG, (slug, tag, published_at, article_id))
