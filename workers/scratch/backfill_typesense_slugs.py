"""One-off: backfill the missing Typesense `slug` field onto existing articles.

Root cause (found 2026-08-26, user report "Goana" search result linked to a
raw-UUID URL): the Typesense `articles` collection schema never declared a
`slug` field at all, and neither `upsert_article_document` write path
(workers' search/core/indexer.py, backend's core/typesense_client.py) ever
included one in the document payload -- even though every published
article's REAL row in Cassandra has a perfectly good slug (see
article_store.ensure_article_slug / ArticleDetail.slug). Typesense silently
drops fields absent from the collection schema, so every indexed article's
search-result document had no slug key whatsoever, and the frontend's
`articleCanonicalPath` (frontend/src/lib/seo.ts) falls back to the raw
article_id UUID whenever `slug` is falsy -- a site-wide search-result-link
bug, not a one-off content problem.

Both write paths now declare the `slug` field on the schema (patched into an
existing live collection the same way tokens/translation/glossary_slugs
fields were, via `_ensure_slug_field`) and include it in every full
document upsert. This script is only for the backlog: every article indexed
BEFORE this fix has no `slug` in its Typesense document until it's next
edited/recomposed/reindexed, so this rebuilds each one now instead of
waiting on that.

Read-only against Cassandra; writes only reach Typesense, via the same full
`upsert_article_document` call `reindex_articles`'s Celery task,
backfill_typesense_translations.py, and backfill_glossary_slugs.py all make.
Safe to re-run: every write is an idempotent upsert keyed by article_id.

Usage (matches backfill_typesense_translations.py's own convention):
    PYTHONPATH=.:../shared python workers/scratch/backfill_typesense_slugs.py --dry-run
    PYTHONPATH=.:../shared python workers/scratch/backfill_typesense_slugs.py
    PYTHONPATH=.:../shared python workers/scratch/backfill_typesense_slugs.py --limit 5000

Run from the `workers` directory (or with that PYTHONPATH) so `app.core.cassandra`
and `algorand_shared` both resolve, same as every other one-off script in this
directory.
"""

from __future__ import annotations

import argparse

from app.modules.newspaper.article_store import get_article, list_feed_articles
from app.modules.search.core.indexer import upsert_article_document


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="max published articles to scan (default 5000, comfortably above the live corpus size)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report each article's real Cassandra slug without writing to Typesense",
    )
    args = parser.parse_args()

    rows = list_feed_articles(limit=args.limit)
    print(f"scanned {len(rows)} published articles", flush=True)

    if args.dry_run:
        missing_slug = 0
        for r in rows:
            detail = get_article(r.article_id)
            if detail is None:
                print(f"  SKIP {r.article_id} | {r.title!r} | article no longer resolves", flush=True)
                continue
            if not detail.slug:
                missing_slug += 1
                print(
                    f"  ANOMALY {r.article_id} | {r.title!r} | "
                    f"no slug in Cassandra either -- would index with slug absent",
                    flush=True,
                )
            else:
                print(
                    f"  would set slug={detail.slug!r} on {r.article_id} | {r.title!r}",
                    flush=True,
                )
        print(
            f"\n{len(rows)} scanned, {missing_slug} have no slug in Cassandra at all "
            "(unexpected -- every published article should have claimed one at go-live)",
            flush=True,
        )
        print("\nDRY_RUN_DONE", flush=True)
        return

    indexed = 0
    skipped = 0
    errors = 0
    for r in rows:
        detail = get_article(r.article_id)
        if detail is None:
            skipped += 1
            continue
        outcome = upsert_article_document(
            article_id=detail.article_id,
            title=detail.title,
            summary=detail.summary,
            body=detail.body,
            service_id=detail.service_id,
            published_at_epoch=detail.published_at_epoch,
            tags=list(detail.tags or []),
            translations=detail.translations,
            slug=detail.slug,
        )
        status = outcome.get("status", "")
        if status == "indexed":
            indexed += 1
        elif status == "error":
            errors += 1
            print(f"  ERROR {r.article_id}: {outcome.get('detail')}", flush=True)
        else:
            skipped += 1
            print(f"  SKIPPED {r.article_id}: {outcome.get('reason', status)}", flush=True)

    print(f"\nindexed={indexed} skipped={skipped} errors={errors}", flush=True)
    print("\nBACKFILL_DONE", flush=True)


if __name__ == "__main__":
    main()
