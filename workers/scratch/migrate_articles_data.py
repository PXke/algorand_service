"""Step 4 of the article-consolidation plan: one-time data migration.

Reads the full-keyspace backup JSON (step 1) and writes into the new
`articles` + `article_history` tables (step 3's DDL). Nothing reads from
these new tables yet -- this is purely additive, the old tables and all
existing call sites are untouched.

Precise status-derivation rule (must be exact, not "derive it" -- getting it
wrong silently mislabels real articles):
  - article_id in draft_articles         -> draft
  - elif article_id in deleted_articles  -> deleted (rare; a deleted article's
                                             articles_by_id row is normally
                                             already gone, so in practice this
                                             branch has nothing to migrate)
  - elif article_id in pending_feed_queue -> backlog
  - elif published_at is set             -> published (articles_feed presence
                                             is NOT used as a signal -- it's
                                             only 45% complete)
  - else                                  -> on_hold

composed_by_model / article_history.model / session_id / session_created_at
are left NULL for every migrated row -- there is no historical link from an
existing article to the compose_sessions row that wrote it (that link is
exactly what article_history's new columns exist to capture going forward,
starting at the write-path cutover in step 5). This is an accepted, explicit
gap, not an oversight.
"""

import json
import uuid
from collections import Counter
from datetime import UTC, datetime

from app.core.cassandra import get_cassandra_session

KS = "algorand_platform"
BACKUP_DIR = "/home/guillaume/full_keyspace_backup"


def _load(name: str) -> list[dict]:
    with open(f"{BACKUP_DIR}/{name}.json") as f:
        return json.load(f)


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s)


def main() -> None:
    s = get_cassandra_session()
    s.default_fetch_size = 2000

    articles = _load("articles_by_id")
    draft_ids = {r["article_id"] for r in _load("draft_articles")}
    deleted_rows = {r["article_id"]: r for r in _load("deleted_articles")}
    backlog_ids = {r["article_id"] for r in _load("pending_feed_queue")}
    versions = _load("article_versions")

    print(
        f"source table sizes -- articles_by_id={len(articles)} "
        f"draft_articles={len(draft_ids)} deleted_articles={len(deleted_rows)} "
        f"pending_feed_queue={len(backlog_ids)} article_versions={len(versions)}",
        flush=True,
    )

    insert_article = s.prepare(
        f"INSERT INTO {KS}.articles ("
        "status, year, published_at, article_id, service_id, title, summary, body, "
        "image_url, tags, source_url, trigger_txid, trigger_round, slug, translations, "
        "first_published_at, updated_at, burst_day, prompt_version, deleted_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    insert_history = s.prepare(
        f"INSERT INTO {KS}.article_history ("
        "article_id, version, title, summary, body, edit_reason, editor, edited_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )

    status_counts: Counter[str] = Counter()
    skipped_orphans: list[str] = []
    written = 0

    for r in articles:
        aid_str = r["article_id"]
        aid = uuid.UUID(aid_str)
        published_at = _parse_ts(r.get("published_at"))

        if published_at is None and r.get("title") is None:
            # Genuinely empty stub row -- every field null except article_id
            # and a fallback slug. published_at is a clustering column on
            # `articles` (can't be null), and there is no real content to
            # migrate anyway. Skip rather than fabricate a timestamp; flagged
            # below for manual cleanup of the OLD articles_by_id row.
            skipped_orphans.append(aid_str)
            continue

        if aid_str in draft_ids:
            status = "draft"
        elif aid_str in deleted_rows:
            status = "deleted"
        elif aid_str in backlog_ids:
            status = "backlog"
        elif published_at is not None:
            status = "published"
        else:
            status = "on_hold"
        status_counts[status] += 1

        if published_at is not None:
            year = published_at.year
        else:
            # Has real content (title set) but somehow no published_at --
            # not the orphan case above. Bucket under the current year
            # rather than dropping a real article; flagged for follow-up.
            year = datetime.now(tz=UTC).year

        deleted_at = _parse_ts(deleted_rows.get(aid_str, {}).get("deleted_at"))

        s.execute(
            insert_article,
            (
                status,
                year,
                published_at,
                aid,
                r.get("service_id"),
                r.get("title"),
                r.get("summary"),
                r.get("body"),
                r.get("image_url"),
                r.get("tags"),
                r.get("source_url"),
                r.get("trigger_txid"),
                r.get("trigger_round"),
                r.get("slug"),
                r.get("translations"),
                _parse_ts(r.get("first_published_at")),
                _parse_ts(r.get("updated_at")),
                r.get("burst_day"),
                r.get("prompt_version"),
                deleted_at,
            ),
        )
        written += 1

    print(f"articles written: {written}", flush=True)
    print(f"status breakdown: {dict(status_counts)}", flush=True)
    print(
        f"skipped {len(skipped_orphans)} orphaned empty rows (title=NULL, no "
        f"published_at -- pre-existing junk in articles_by_id, not migrated): "
        f"{skipped_orphans}",
        flush=True,
    )

    hist_written = 0
    for v in versions:
        s.execute(
            insert_history,
            (
                uuid.UUID(v["article_id"]),
                v["version"],
                v.get("title"),
                v.get("summary"),
                v.get("body"),
                v.get("edit_reason"),
                v.get("editor"),
                _parse_ts(v.get("edited_at")),
            ),
        )
        hist_written += 1
    print(f"article_history written: {hist_written}", flush=True)

    # Row-count reconciliation against the backup.
    total = 0
    for st in ("draft", "on_hold", "backlog", "published", "deleted"):
        rows = list(
            s.execute(f"SELECT article_id FROM {KS}.articles WHERE status = %s ALLOW FILTERING", (st,))
        )
        total += len(rows)
        print(f"  verify status={st}: {len(rows)} rows in articles", flush=True)
    expected = len(articles) - len(skipped_orphans)
    print(
        f"total in articles: {total} (expected {expected} = "
        f"{len(articles)} source rows - {len(skipped_orphans)} skipped orphans)",
        flush=True,
    )
    assert total == expected, "row count mismatch -- STOP, do not proceed"

    hist_total = len(list(s.execute(f"SELECT article_id FROM {KS}.article_history")))
    print(f"total in article_history: {hist_total} (expected {len(versions)})", flush=True)
    assert hist_total == len(versions), "article_history row count mismatch -- STOP"

    print("\nMIGRATION_DONE", flush=True)


if __name__ == "__main__":
    main()
