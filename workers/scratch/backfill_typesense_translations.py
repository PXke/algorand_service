"""One-off: backfill existing article translations into Typesense.

Root cause (found 2026-08-25): `translate_article_batch_task`'s `_persist`
callback stored a landed translation to Cassandra and pinged IndexNow, but
never pushed it into Typesense -- the articles collection's schema had no
per-language fields at all until this fix (see indexer.py's
`_translation_field_defs` / `upsert_article_translation`). Every reader
search in French (or any of the other 7 translation languages) therefore
only ever matched English title/summary/body text, regardless of how much
translated content existed. The live hook is now fixed going forward
(`_persist` calls `upsert_article_translation` the moment a translation
lands), but every article translated BEFORE this fix is still invisible to
non-English search until its Typesense document is rebuilt with the new
fields. This script does that rebuild.

Read-only against Cassandra; writes only reach Typesense (a full
`upsert_article_document` per article, the same call `reindex_articles`'s
Celery task makes -- this script just runs that logic inline instead of
through Celery, since there's no LLM call in the loop and inline execution
reports progress directly). Safe to re-run: every write is an idempotent
upsert keyed by article_id.

Deliberately reindexes EVERY scanned article, not only ones with a
translations map -- a plain reindex is harmless (and cheap) for an
English-only article too, and this way an article's Typesense document
always reflects its current translations map exactly, including one that
lost a language (recompose clears translations; a stale title_fr from
before that recompose must not linger in the index).

Usage (matches backfill_stale_translations.py's own convention):
    PYTHONPATH=.:../shared python workers/scratch/backfill_typesense_translations.py --dry-run
    PYTHONPATH=.:../shared python workers/scratch/backfill_typesense_translations.py
    PYTHONPATH=.:../shared python workers/scratch/backfill_typesense_translations.py --limit 5000

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
        help="report what would be reindexed without writing to Typesense",
    )
    args = parser.parse_args()

    rows = list_feed_articles(limit=args.limit)
    translated_rows = [r for r in rows if r.translations]
    print(
        f"scanned {len(rows)} published articles, "
        f"{len(translated_rows)} have at least one stored translation",
        flush=True,
    )

    if args.dry_run:
        for r in translated_rows:
            langs = sorted(r.translations.keys())
            print(f"  would reindex {r.article_id} | {r.title!r} | langs={langs}", flush=True)
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
