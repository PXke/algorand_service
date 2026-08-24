"""Full backup of the algorand_platform keyspace (every table), as JSON.

Supersedes the earlier article-tables-only backup (backup_article_tables.py) --
step 1 of the article-data-model consolidation plan, done as "every keyspace
table, not just the 11 article ones" per explicit request, since further
architecture changes are planned after the article migration lands.

Scope: the algorand_platform keyspace only. Two things are deliberately
excluded, not silently forgotten:

1. Other apps' keyspaces on this shared Cassandra cluster (backend_api,
   basos, eatilla, oak) -- unrelated projects, not this platform's data.
2. The chain-indexer tables within algorand_platform itself (blocks,
   conduit_meta, transactions_by_id/_sender/_receiver/_round) -- these mirror
   on-chain data written by conduit and are fully regenerable by re-indexing
   from the chain; they're not touched by anything in the article migration
   and aren't "the app's data" in the sense that matters for this backup.

A plain unscoped `SELECT * FROM table` (no WHERE clause) triggers Cassandra's
normal token-range scan and the driver's ResultSet auto-pages through the
entire ring correctly -- this is different from the bucketed-WHERE-clause bug
hit earlier (a query that filtered on one bucket value and never enumerated
the others). No per-table bucket enumeration needed here.
"""

import json
import os
import uuid
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from app.core.cassandra import get_cassandra_session

OUT_DIR = "/home/guillaume/full_keyspace_backup"
KEYSPACE = "algorand_platform"

EXCLUDED_TABLES = {
    "blocks",
    "conduit_meta",
    "transactions_by_id",
    "transactions_by_sender",
    "transactions_by_receiver",
    "transactions_by_round",
}


def _jsonable(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (set, frozenset)):
        return sorted(_jsonable(v) for v in value)
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    s = get_cassandra_session()
    s.default_fetch_size = 2000

    table_rows = s.execute(
        "SELECT table_name FROM system_schema.tables WHERE keyspace_name=%s", (KEYSPACE,)
    )
    all_tables = sorted(r.table_name for r in table_rows)
    tables = [t for t in all_tables if t not in EXCLUDED_TABLES]

    print(f"{len(all_tables)} tables total, {len(tables)} to back up, "
          f"{len(EXCLUDED_TABLES)} excluded (chain-indexer)", flush=True)

    manifest = {"keyspace": KEYSPACE, "excluded": sorted(EXCLUDED_TABLES), "tables": {}}

    for t in tables:
        rows = list(s.execute(f"SELECT * FROM {KEYSPACE}.{t}"))
        out = [{col: _jsonable(getattr(r, col)) for col in r._fields} for r in rows]  # noqa: SLF001
        path = f"{OUT_DIR}/{t}.json"
        with open(path, "w") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        manifest["tables"][t] = len(out)
        print(f"  {t}: {len(out)} rows -> {path}", flush=True)

    with open(f"{OUT_DIR}/_manifest.json", "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print("\nBACKUP_DONE", flush=True)


if __name__ == "__main__":
    main()
