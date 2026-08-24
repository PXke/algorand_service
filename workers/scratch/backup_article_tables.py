"""One-time full backup of every table that represents article state, as JSON.

First step of the article-data-model consolidation: back everything up before
touching schema. Each table gets its own JSON file (list of row dicts,
datetimes/UUIDs stringified). Uses the CONFIRMED partition-key value sets
(queried live via SELECT DISTINCT, not guessed from code) for the bucketed
tables, to avoid the exact silent-partial-scan bug found earlier tonight
(a plain unscoped SELECT on articles_feed only surfaced one partition).

Simple single-partition-key tables (articles_by_id, deleted_articles,
draft_articles, article_view_counts, publish_queue, editorial_briefs) get a
plain full-table SELECT * -- Cassandra's driver paginates a legitimate
unscoped token-range scan correctly; the earlier bug was specific to querying
a COMPOSITE/bucketed partition key without enumerating every bucket value.
"""

import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from app.core.cassandra import get_cassandra_session

OUT_DIR = "/home/guillaume/article_table_backup"


def _jsonable(value: object) -> object:
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    # Cassandra map/UDT columns deserialize as driver-specific mapping types
    # (e.g. OrderedMapSerializedKey), not plain dict -- Mapping catches those too.
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _dump(name: str, rows: list) -> None:
    out = [
        {col: _jsonable(getattr(r, col)) for col in r._fields} for r in rows  # noqa: SLF001
    ]
    path = f"{OUT_DIR}/{name}.json"
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"  {name}: {len(out)} rows -> {path}", flush=True)


def main() -> None:
    import os

    os.makedirs(OUT_DIR, exist_ok=True)
    s = get_cassandra_session()

    print("--- simple single-partition-key tables (plain full scan) ---", flush=True)
    _dump("articles_by_id", list(s.execute("SELECT * FROM algorand_platform.articles_by_id")))
    _dump("deleted_articles", list(s.execute("SELECT * FROM algorand_platform.deleted_articles")))
    _dump("draft_articles", list(s.execute("SELECT * FROM algorand_platform.draft_articles")))
    _dump(
        "article_view_counts",
        list(s.execute("SELECT * FROM algorand_platform.article_view_counts")),
    )
    _dump("publish_queue", list(s.execute("SELECT * FROM algorand_platform.publish_queue")))
    _dump("editorial_briefs", list(s.execute("SELECT * FROM algorand_platform.editorial_briefs")))

    print("\n--- bucketed tables (confirmed partition values via SELECT DISTINCT) ---", flush=True)
    feed_rows = []
    for bucket in ["2026-08"]:  # confirmed the ONLY bucket currently present
        feed_rows.extend(
            s.execute("SELECT * FROM algorand_platform.articles_feed WHERE bucket=%s", (bucket,))
        )
    _dump("articles_feed", feed_rows)

    pending_feed_rows = list(
        s.execute("SELECT * FROM algorand_platform.pending_feed_queue WHERE bucket=%s", ("main",))
    )
    _dump("pending_feed_queue", pending_feed_rows)

    pqp_rows = list(
        s.execute(
            "SELECT * FROM algorand_platform.publish_queue_pending WHERE status=%s", ("pending",)
        )
    )
    _dump("publish_queue_pending", pqp_rows)

    print("\n--- per-article tables (keyed off articles_by_id's known ids) ---", flush=True)
    article_ids = [r.article_id for r in s.execute("SELECT article_id FROM algorand_platform.articles_by_id")]
    print(f"  {len(article_ids)} known article_ids", flush=True)

    version_rows = []
    match_rows = []
    for aid in article_ids:
        version_rows.extend(
            s.execute(
                "SELECT * FROM algorand_platform.article_versions WHERE article_id=%s", (aid,)
            )
        )
        match_rows.extend(
            s.execute(
                "SELECT * FROM algorand_platform.article_match_keys_by_article WHERE article_id=%s",
                (aid,),
            )
        )
    _dump("article_versions", version_rows)
    _dump("article_match_keys_by_article", match_rows)

    print("\nBACKUP_DONE", flush=True)


if __name__ == "__main__":
    main()
