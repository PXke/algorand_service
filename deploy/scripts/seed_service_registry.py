#!/usr/bin/env python3
"""Insert service_registry rows from deploy/seeds/testnet_services.toml."""

from __future__ import annotations

import os
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED = REPO_ROOT / "deploy/seeds/testnet_services.toml"


def resolve_seed_path() -> Path:
    raw = os.getenv("SEED_FILE", "").strip()
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else REPO_ROOT / path
    return DEFAULT_SEED


def main() -> int:
    seed_path = resolve_seed_path()
    if not seed_path.is_file():
        print(f"error: seed file not found: {seed_path}", file=sys.stderr)
        return 1

    try:
        from cassandra.auth import PlainTextAuthProvider
        from cassandra.cluster import Cluster
        from cassandra.policies import DCAwareRoundRobinPolicy
    except ImportError:
        print("error: pip install cassandra-driver", file=sys.stderr)
        return 1

    hosts = [h.strip() for h in os.getenv("CASSANDRA_HOSTS", "127.0.0.1").split(",") if h.strip()]
    keyspace = os.getenv("CASSANDRA_KEYSPACE", "algorand_platform")
    raw = tomllib.loads(seed_path.read_text(encoding="utf-8"))
    services = raw.get("services", [])

    local_dc = os.getenv("CASSANDRA_LOCAL_DC", "datacenter1")
    username = os.getenv("CASSANDRA_USERNAME", "")
    auth_provider = (
        PlainTextAuthProvider(username=username, password=os.getenv("CASSANDRA_PASSWORD", ""))
        if username
        else None
    )
    cluster = Cluster(
        hosts,
        load_balancing_policy=DCAwareRoundRobinPolicy(local_dc=local_dc),
        auth_provider=auth_provider,
    )
    session = cluster.connect(keyspace)
    now = datetime.now(tz=UTC)

    for entry in services:
        session.execute(
            """
            INSERT INTO service_registry (
              service_id, display_name, match_kind, match_value, scrape_url, enabled, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                entry["service_id"],
                entry["display_name"],
                entry["match_kind"],
                entry["match_value"],
                entry.get("scrape_url"),
                bool(entry.get("enabled", True)),
                now,
            ),
        )
        print(f"upserted service_id={entry['service_id']}")
        scrape_url = entry.get("scrape_url") or ""
        if scrape_url.startswith(("http://", "https://")):
            _maybe_enqueue_seed_url(scrape_url, entry["service_id"])

    print(f"Done ({len(services)} services).")
    return 0


def _maybe_enqueue_seed_url(url: str, service_id: str) -> None:
    if os.getenv("URL_QUEUE_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        sys.path.insert(0, str(REPO_ROOT / "workers"))
        from app.modules.crawler.url_queue import enqueue_url

        enqueue_url(url, source="seed", priority=40, metadata={"service_id": service_id})
        print(f"enqueued url_queue url={url}")
    except Exception as exc:
        print(f"warn: url_queue enqueue failed for {service_id}: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
