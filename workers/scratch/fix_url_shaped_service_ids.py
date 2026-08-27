"""One-off correction: two published articles carry a raw URL as their `service_id`
instead of a normalized slug (found during the 2026-08-27 venue-normalization review).
This makes `service_has_article(<normalized-id>)` read False even though the site is
already covered, so a future crawler artifact for it would falsely win a new_service
floor slot -- the same bug class as the algorand.co seed-canonical-id fix, but at the
article row itself rather than the seed.

`service_id` is a plain, non-key, SAI-indexed column on the consolidated `articles`
table (PRIMARY KEY ((status, year), published_at, article_id) -- migration 067), so a
plain UPDATE naming the full existing key is safe: no partition move, no phantom-row
risk (that gotcha was on the OLD articles_feed table, dropped in migration 074).
`articles_by_tag` (migration 073) also carries a denormalized copy of service_id and
must be reconciled via the same sync_tag_index() call every other articles write uses.
"""

from __future__ import annotations

from algorand_shared.article_statements import ArticlesStmts
from algorand_shared.article_tag_index import sync_tag_index
from algorand_shared.feed_cache import invalidate_feed_first_page
from app.core.cassandra import get_cassandra_session

CORRECTIONS = [
    ("https://www.scottgerrard.com", "scottgerrard-com"),
    ("https://kryptonurd.com/", "kryptonurd-com"),
]


def fix_one(session, old_service_id: str, new_service_id: str) -> None:
    collision = session.execute(ArticlesStmts.FIND_BY_SERVICE_ID, (new_service_id,)).one()
    if collision is not None:
        print(f"SKIP {old_service_id!r}: {new_service_id!r} already used by article {collision.article_id}")
        return

    hit = session.execute(ArticlesStmts.FIND_BY_SERVICE_ID, (old_service_id,)).one()
    if hit is None:
        print(f"NOT FOUND: {old_service_id!r}")
        return
    row = session.execute(ArticlesStmts.GET_FULL_BY_ID, (hit.article_id,)).one()
    if row is None or row.published_at is None:
        print(f"row vanished for {hit.article_id}")
        return

    session.execute(
        "UPDATE algorand_platform.articles SET service_id = %s "
        "WHERE status = %s AND year = %s AND published_at = %s AND article_id = %s",
        (new_service_id, row.status, row.year, row.published_at, row.article_id),
    )

    sync_tag_index(
        row.article_id,
        old_status=row.status,
        old_tags=list(row.tags or []),
        old_published_at=row.published_at,
        new_status=row.status,
        new_tags=list(row.tags or []),
        new_published_at=row.published_at,
        service_id=new_service_id,
        title=row.title,
        summary=row.summary,
        image_url=row.image_url,
        source_url=row.source_url,
        slug=row.slug,
        translations=dict(row.translations) if row.translations else None,
        first_published_at=row.first_published_at,
        updated_at=row.updated_at,
    )
    print(f"fixed {row.article_id}: {old_service_id!r} -> {new_service_id!r}")


def main() -> None:
    session = get_cassandra_session()
    for old, new in CORRECTIONS:
        fix_one(session, old, new)
    invalidate_feed_first_page()


if __name__ == "__main__":
    main()
