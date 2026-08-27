"""One-off backfill for migration 084 (articles.views).

article_view_counts (migration 026, a COUNTER table keyed only on article_id)
is being replaced by a plain `articles.views` int column. A raw CQL migration
can't copy values across since the two tables have different key shapes
(article_view_counts is a flat PRIMARY KEY(article_id); articles' key
includes status/year/published_at), so this script does it: full scan of
article_view_counts, one update_article_views() call per row -- reusing the
same read-current-key-then-patch helper flush_pending_views uses, so a
recompose that has since moved an article's published_at is handled
correctly rather than upserting a phantom row.

Additive and safe to re-run: articles.views starts NULL, so a re-run just
writes the same counter value again. Run this ONCE after migration 084 and
the cutover code (article_store.py's insert_stored_article carry-forward,
view_counts.py's flush/get_views* rewrite) are both live -- otherwise a view
recorded between the counter table's old write path being retired and this
backfill running could be silently skipped (the counter table stops getting
new increments the moment the cutover code deploys, so run this backfill
right after that deploy, not before).

Usage:
    PYTHONPATH=.:../shared python workers/scratch/backfill_article_views.py

Run from the `workers` directory (or with that PYTHONPATH) so `app.core.cassandra`
and `algorand_shared` both resolve, same as every other one-off script in this
directory.
"""

from __future__ import annotations

from app.core.cassandra import get_cassandra_session
from app.modules.newspaper.article_store import get_article_views, update_article_views

KS = "algorand_platform"


def main() -> None:
    session = get_cassandra_session()
    session.default_fetch_size = 2000

    rows = session.execute(f"SELECT article_id, views FROM {KS}.article_view_counts")

    scanned = 0
    applied = 0
    skipped_no_article = 0
    skipped_already_higher = 0
    failed = 0

    for row in rows:
        scanned += 1
        article_id = str(row.article_id)
        counter_views = int(row.views or 0)
        if counter_views <= 0:
            continue

        current = get_article_views(article_id)
        if current is None:
            # Article no longer exists (purged since the counter row was
            # written) -- nothing to backfill onto.
            skipped_no_article += 1
            continue
        if current >= counter_views:
            # Already caught up (e.g. a re-run, or the flush beat already
            # accumulated fresh views past the old counter's snapshot) --
            # never move the tally backwards.
            skipped_already_higher += 1
            continue

        if update_article_views(article_id, counter_views):
            applied += 1
        else:
            failed += 1

    print(f"article_view_counts rows scanned: {scanned}", flush=True)
    print(f"articles.views backfilled: {applied}", flush=True)
    print(f"skipped (article no longer exists): {skipped_no_article}", flush=True)
    print(f"skipped (already at/above counter value): {skipped_already_higher}", flush=True)
    print(f"failed writes: {failed}", flush=True)
    print("\nBACKFILL_DONE", flush=True)


if __name__ == "__main__":
    main()
