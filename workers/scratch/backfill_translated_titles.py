"""One-off backfill for migration 087 (articles.translated_titles / articles_by_tag.translated_titles).

`translated_titles` (lang -> JSON {title, summary}) is the lightweight
companion to the full `translations` map (lang -> JSON {title, summary,
body}) that ArticlesStmts.LIST_PUBLISHED_PAGE / ArticleTagIndexStmts.
LIST_PAGE/LIST_RECENT now select instead of the full map -- see migration
087's own comment for why (a 2026-08-28 performance audit found the feed/
tag-listing queries were shipping full translated article BODIES per row
just to render a headline). It's written going forward by
translate_article_task / translate_article_batch_task's `_persist`, but
every article translated BEFORE that code landed has `translations`
populated with no corresponding `translated_titles` until this backfill
runs.

Derives `translated_titles` from each article's EXISTING `translations` map
-- no new LLM calls, no re-translation, purely a re-encode of data already
stored: for each stored language, parses the JSON {title, summary, body}
and re-encodes {title, summary} only. Writes via update_article_translations's
existing merge path, passing an empty dict for `translations` (a no-op
Cassandra map addition) so the already-correct translations map is left
completely untouched -- only translated_titles is merged.

Scans via list_feed_articles (published, feed-visible articles only -- the
only rows the listing queries this backfill exists for actually read), then
re-fetches each one's full detail (get_article) for its real `translations`
map -- FeedArticleRow itself no longer carries the full map as of the same
migration this backfill is for, only the lightweight column.

Safe to re-run: additive Cassandra map merge, and this script also skips any
language already present in the target article's CURRENT translated_titles
(re-read fresh via get_article on every run), so a partial prior run (or a
language landing naturally via the live translate path in between) resumes
correctly instead of re-writing identical values.

Usage:
    PYTHONPATH=.:../shared python workers/scratch/backfill_translated_titles.py --dry-run
    PYTHONPATH=.:../shared python workers/scratch/backfill_translated_titles.py
    PYTHONPATH=.:../shared python workers/scratch/backfill_translated_titles.py --limit 5000

Run from the `workers` directory (or with that PYTHONPATH) so `app.core.cassandra`
and `algorand_shared` both resolve, same as every other one-off script in this
directory.
"""

from __future__ import annotations

import argparse
import json

from app.modules.newspaper.article_store import (
    get_article,
    list_feed_articles,
    update_article_translations,
)


def derive_translated_titles(translations: dict[str, str]) -> dict[str, str]:
    """{lang: JSON {title, summary}} derived from a full translations map, skipping any entry that isn't valid {title, summary, body} JSON."""
    out: dict[str, str] = {}
    for lang, raw in translations.items():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict):
            continue
        out[lang] = json.dumps(
            {"title": parsed.get("title", ""), "summary": parsed.get("summary", "")},
            ensure_ascii=False,
        )
    return out


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
        help="report each article's derived translated_titles languages without writing to Cassandra",
    )
    args = parser.parse_args()

    rows = list_feed_articles(limit=args.limit)
    print(f"scanned {len(rows)} published articles", flush=True)

    needs_backfill = 0
    backfilled = 0
    already_current = 0
    no_translations = 0
    anomalies = 0

    for r in rows:
        detail = get_article(r.article_id)
        if detail is None:
            anomalies += 1
            print(
                f"  ANOMALY {r.article_id} | {r.title!r} | no longer resolves via get_article",
                flush=True,
            )
            continue
        if not detail.translations:
            no_translations += 1
            continue

        derived = derive_translated_titles(detail.translations)
        if not derived:
            no_translations += 1
            continue

        existing = set((detail.translated_titles or {}).keys())
        missing = {lang: value for lang, value in derived.items() if lang not in existing}
        if not missing:
            already_current += 1
            continue

        needs_backfill += 1
        if args.dry_run:
            print(
                f"  would backfill {r.article_id} | {r.title!r} | langs={sorted(missing)}",
                flush=True,
            )
            continue

        if update_article_translations(r.article_id, {}, missing):
            backfilled += 1
        else:
            anomalies += 1
            print(
                f"  ERROR {r.article_id}: update_article_translations returned False "
                "(article no longer exists?)",
                flush=True,
            )

    print(
        f"\n{len(rows)} scanned, {needs_backfill} needed backfilling, "
        f"{already_current} already current, {no_translations} had no usable translations, "
        f"{anomalies} anomalies",
        flush=True,
    )
    if args.dry_run:
        print("\nDRY_RUN_DONE", flush=True)
    else:
        print(f"\nbackfilled={backfilled}", flush=True)
        print("\nBACKFILL_DONE", flush=True)


if __name__ == "__main__":
    main()
