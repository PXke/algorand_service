"""One-off backfill for migration 073 (articles_by_tag).

sync_tag_index only fires on NEW writes going forward (create, status
transition, in-place content/tag edit) -- every article already
status='published' at the time this migration lands has zero rows in the new
index until this script runs once. Read-only against `articles`, additive
writes to `articles_by_tag`; safe to re-run (INSERT is idempotent on the same
primary key, and sync_tag_index's own normalization is applied here too so a
re-run can't create a duplicate tag row for the same article under a
different casing).

Usage (after migration 073 has been applied to the target keyspace):
    PYTHONPATH=.:../shared python workers/scratch/backfill_articles_by_tag.py

Run from the `workers` directory (or with that PYTHONPATH) so `app.core.cassandra`
and `algorand_shared` both resolve, same as every other one-off script in this
directory.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime

from algorand_shared.article_statements import ArticleTagIndexStmts, ArticlesStmts

from app.core.cassandra import get_cassandra_session

KS = "algorand_platform"

# Comfortably covers this platform's real history (article-table
# consolidation landed 2026-08-24; nothing published predates this range).
# Adjust upward if this is ever run against an older keyspace.
_YEARS = range(2024, datetime.now(tz=UTC).year + 1)


def _normalize_tags(tags: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags or []:
        tag = (raw or "").strip().lower()
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def main() -> None:
    session = get_cassandra_session()
    session.default_fetch_size = 2000

    insert_stmt = session.prepare(
        f"INSERT INTO {KS}.articles_by_tag ("
        "tag, published_at, article_id, service_id, title, summary, image_url, "
        "source_url, slug, translations, first_published_at, updated_at, tags"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )

    articles_scanned = 0
    articles_with_tags = 0
    tag_rows_written = 0
    tag_counts: Counter[str] = Counter()

    for year in _YEARS:
        rows = session.execute(ArticlesStmts.LIST_IDS_BY_STATUS, ("published", year))
        for id_row in rows:
            full = session.execute(ArticlesStmts.GET_FULL_BY_ID, (id_row.article_id,)).one()
            if full is None or full.status != "published" or full.published_at is None:
                continue
            articles_scanned += 1
            tags = _normalize_tags(list(full.tags or []))
            if not tags:
                continue
            articles_with_tags += 1
            for tag in tags:
                session.execute(
                    insert_stmt,
                    (
                        tag,
                        full.published_at,
                        full.article_id,
                        full.service_id,
                        full.title,
                        full.summary,
                        full.image_url,
                        full.source_url,
                        full.slug,
                        full.translations,
                        full.first_published_at,
                        full.updated_at,
                        list(full.tags or []),
                    ),
                )
                tag_rows_written += 1
                tag_counts[tag] += 1

    print(f"articles scanned (status='published'): {articles_scanned}", flush=True)
    print(f"articles with at least one tag: {articles_with_tags}", flush=True)
    print(f"articles_by_tag rows written: {tag_rows_written}", flush=True)
    print(f"distinct tags: {len(tag_counts)}", flush=True)
    for tag, count in tag_counts.most_common(20):
        print(f"  {tag}: {count}", flush=True)

    # Reconciliation: DISTINCT tag scan on the new table should return exactly
    # the tags just written.
    verify_tags = {
        row.tag for row in session.execute(ArticleTagIndexStmts.LIST_TAGS) if row.tag
    }
    print(f"verify: {len(verify_tags)} distinct tags now in articles_by_tag", flush=True)
    assert verify_tags == set(tag_counts), "tag universe mismatch -- STOP, investigate"

    print("\nBACKFILL_DONE", flush=True)


if __name__ == "__main__":
    main()
