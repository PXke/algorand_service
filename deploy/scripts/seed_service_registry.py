#!/usr/bin/env python3
"""Insert service_registry rows from deploy/seeds/testnet_services.toml."""

from __future__ import annotations

import logging
import os
import sys
import tomllib
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEED = REPO_ROOT / "deploy/seeds/testnet_services.toml"


def resolve_seed_path() -> Path:
    """Seed file to load: $SEED_FILE if set, else the bundled TestNet default."""
    raw = os.getenv("SEED_FILE", "").strip()
    if raw:
        path = Path(raw)
        return path if path.is_absolute() else REPO_ROOT / path
    return DEFAULT_SEED


def main() -> int:
    """Upsert every service_registry row from the seed file, enqueueing each scrape_url."""
    seed_path = resolve_seed_path()
    if not seed_path.is_file():
        logger.error("seed file not found: %s", seed_path)
        return 1

    try:
        from cassandra.auth import PlainTextAuthProvider
        from cassandra.cluster import Cluster
        from cassandra.policies import DCAwareRoundRobinPolicy
    except ImportError:
        logger.error("pip install cassandra-driver")
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
        logger.info("upserted service_id=%s", entry["service_id"])
        scrape_url = entry.get("scrape_url") or ""
        _maybe_seed_web_source(entry)
        if scrape_url.startswith(("http://", "https://")):
            _maybe_enqueue_seed_url(scrape_url, entry["service_id"])

    logger.info("Done (%d services).", len(services))
    return 0


def _maybe_seed_web_source(entry: dict) -> None:
    """Best-effort: populate the service_sources by-domain reverse index for a freshly-seeded ``match_kind == "domain"`` entry.

    This closes the exact gap that caused the literal domain-registry
    duplicate bug: this script inserts service_registry rows via raw CQL and
    historically never called add_web_source (the same claim-the-domain step
    domain_tracker.ensure_monitored_service and the backend admin's
    admin_upsert_source both already perform), so a seeded/legacy service
    was permanently invisible to service_sources.service_for_domain — the
    NEXT time the platform met that domain on its own (e.g. via
    ensure_monitored_service), it found no owner and spawned a second,
    duplicate service_registry row for the same real-world domain instead
    of recognizing the seeded one. Only fires for domain-kind entries — a
    reddit/subreddit/address-matched entry has no registrable domain to
    claim. Never raises: a seed run must still finish (and the other
    entries still get inserted) even if Cassandra or the workers import
    path isn't reachable from wherever this script is invoked.
    """
    if (entry.get("match_kind") or "") != "domain":
        return
    domain = (entry.get("match_value") or "").strip().lower()
    if not domain:
        return
    service_id = entry["service_id"]
    url = entry.get("scrape_url") or f"https://{domain}"
    try:
        sys.path.insert(0, str(REPO_ROOT / "workers"))
        from app.modules.newspaper.service_sources import add_web_source

        add_web_source(service_id, domain=domain, url=url)
        logger.info("indexed web source domain=%s service_id=%s", domain, service_id)
    except Exception as exc:
        logger.warning("add_web_source failed for %s (domain=%s): %s", service_id, domain, exc)


def _maybe_enqueue_seed_url(url: str, service_id: str) -> None:
    """Best-effort url_queue enqueue for a freshly-seeded service's scrape_url."""
    if os.getenv("URL_QUEUE_ENABLED", "1").strip().lower() in ("0", "false", "no"):
        return
    try:
        sys.path.insert(0, str(REPO_ROOT / "workers"))
        from app.modules.crawler.url_queue import enqueue_url

        enqueue_url(url, source="seed", priority=40, metadata={"service_id": service_id})
        logger.info("enqueued url_queue url=%s", url)
    except Exception as exc:
        logger.warning("url_queue enqueue failed for %s: %s", service_id, exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
