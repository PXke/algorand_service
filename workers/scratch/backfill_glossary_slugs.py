"""One-off: backfill the new Typesense `glossary_slugs` field onto existing articles.

Feature (2026-08): glossary term pages cross-reference which articles mention
that term. Explicit constraint from the owner -- no new Cassandra table, since
a French reader's glossary links point at French-translated anchor text/title
while the underlying `/glossary/:slug` href stays the same across every
locale (glossary_linker.py links only the English body; translation carries
the markdown link straight through, translating only the visible text). So
the cross-reference lives in Typesense as a `glossary_slugs: string[]` field
on each article document, extracted by scanning the English body AND every
translated body for `/glossary/slug` links and unioning what's found (see
shared/algorand_shared/glossary_refs.py).

`upsert_article_document` (indexer.py) now computes this field on every
write going forward. This script is only for the backlog: every article
indexed BEFORE this field existed has no `glossary_slugs` at all, so a
glossary term page querying `glossary_slugs:=slug` would miss every article
published before this feature shipped, even ones that plainly reference the
term. Read-only against Cassandra; writes only reach Typesense, via the same
full `upsert_article_document` call `reindex_articles`'s Celery task and
backfill_typesense_translations.py both make -- running either of those
scripts also backfills this field as a side effect, but this one exists
specifically so the *reason* for the run at scale is visible in `git log` /
job history, not just its (barely different) inline output. Safe to re-run:
every write is an idempotent upsert keyed by article_id.

Usage (matches backfill_typesense_translations.py's own convention):
    PYTHONPATH=.:../shared python workers/scratch/backfill_glossary_slugs.py --dry-run
    PYTHONPATH=.:../shared python workers/scratch/backfill_glossary_slugs.py
    PYTHONPATH=.:../shared python workers/scratch/backfill_glossary_slugs.py --limit 5000

Run from the `workers` directory (or with that PYTHONPATH) so `app.core.cassandra`
and `algorand_shared` both resolve, same as every other one-off script in this
directory.
"""

from __future__ import annotations

import argparse

from algorand_shared.glossary_refs import extract_glossary_slugs

from app.modules.newspaper.article_store import get_article, list_feed_articles
from app.modules.search.core.indexer import upsert_article_document


def _translated_bodies(translations: dict[str, str] | None) -> list[str]:
    """Best-effort JSON-parse of each stored translation blob's `body` field, mirroring indexer.py's own parsing so the dry-run preview matches what the real upsert will find."""
    import json

    bodies: list[str] = []
    for raw in (translations or {}).values():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(parsed, dict):
            value = parsed.get("body")
            if isinstance(value, str) and value:
                bodies.append(value)
    return bodies


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
        help="report which articles reference which glossary slugs without writing to Typesense",
    )
    args = parser.parse_args()

    rows = list_feed_articles(limit=args.limit)
    print(f"scanned {len(rows)} published articles", flush=True)

    if args.dry_run:
        referencing = 0
        for r in rows:
            detail = get_article(r.article_id)
            if detail is None:
                continue
            slugs = extract_glossary_slugs(detail.body, *_translated_bodies(detail.translations))
            if slugs:
                referencing += 1
                print(f"  {r.article_id} | {r.title!r} | glossary_slugs={slugs}", flush=True)
        print(
            f"\n{referencing}/{len(rows)} articles reference at least one glossary term", flush=True
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
