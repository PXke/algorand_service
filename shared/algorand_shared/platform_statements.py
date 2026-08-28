"""Prepared CQL shared between backend and workers for misc platform tables.

Both deployables read and write the SAME physical Cassandra tables
(`service_sources`, `service_by_domain`, `page_snapshots`, `glossary_terms`,
`domain_tracking`, `classifier_feedback_by_time`, `service_registry`) via
independently hand-maintained statement classes in each service's own
`app/core/statements.py`. Every statement here was found BYTE-IDENTICAL in
both services' copies; each local `statements.py` now assigns its class
attribute from one of these constants instead of defining its own copy, so a
future edit to one of these queries can no longer silently stop matching the
other service's copy (the exact drift `ChainStmts.TXNS_BY_ROUND` had already
suffered -- see `chain_statements.py`).

`service_registry`'s `DELETE` is deliberately NOT here -- it's backend-admin-
only (workers never deletes a registry row), so it stays a local-only
statement on backend's `ServiceRegistryStmts`.

Names are flat module-level constants, NOT nested in a class, for the same
reason as `article_statements.py` (see that module's docstring for the full
explanation): `_Stmt` is a data descriptor, and only module-level attribute
access skips the descriptor protocol needed to keep preparation lazy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cassandra.query import PreparedStatement


class _Stmt:
    """Descriptor holding CQL; resolves to the (cached) PreparedStatement on access.

    Preparation is delegated to `app.core.cassandra.prepare_cached` -- resolved
    per-process, so this works identically whether accessed from backend or
    workers, each of which has its own `app.core.cassandra` module.
    """

    def __init__(self, cql: str) -> None:
        self.cql = cql

    def __get__(self, obj: object | None, owner: type | None) -> PreparedStatement:
        from app.core.cassandra import prepare_cached

        return prepare_cached(self.cql)


# --------------------------------------------------------------------------- #
# service_sources / service_by_domain (service layer -- one service, N sources)
# --------------------------------------------------------------------------- #
SERVICE_SOURCE_UPSERT = _Stmt(
    "INSERT INTO algorand_platform.service_sources ("
    "service_id, source_id, source_type, url, domain, enabled, added_at"
    ") VALUES (?, ?, ?, ?, ?, ?, ?)"
)
SERVICE_SOURCE_LIST_FOR_SERVICE = _Stmt(
    "SELECT source_id, source_type, url, domain, enabled "
    "FROM algorand_platform.service_sources WHERE service_id = ?"
)
SERVICE_SOURCE_DELETE_FOR_SERVICE = _Stmt(
    "DELETE FROM algorand_platform.service_sources WHERE service_id = ?"
)
SERVICE_SOURCE_UPSERT_BY_DOMAIN = _Stmt(
    "INSERT INTO algorand_platform.service_by_domain (domain, service_id) VALUES (?, ?)"
)
SERVICE_SOURCE_GET_BY_DOMAIN = _Stmt(
    "SELECT service_id FROM algorand_platform.service_by_domain WHERE domain = ?"
)

# --------------------------------------------------------------------------- #
# page_snapshots
# --------------------------------------------------------------------------- #
PAGE_SNAPSHOT_GET_LATEST = _Stmt(
    "SELECT content_hash, title, body FROM algorand_platform.page_snapshots "
    "WHERE source_id = ? LIMIT 1"
)
PAGE_SNAPSHOT_INSERT = _Stmt(
    "INSERT INTO algorand_platform.page_snapshots "
    "(source_id, captured_at, content_hash, title, body) "
    "VALUES (?, ?, ?, ?, ?) USING TTL 3888000"
)

# --------------------------------------------------------------------------- #
# glossary_terms
# --------------------------------------------------------------------------- #
GLOSSARY_UPDATE_TRANSLATIONS = _Stmt(
    "UPDATE algorand_platform.glossary_terms SET translations = translations + ? "
    "WHERE slug = ? IF EXISTS"
)

# --------------------------------------------------------------------------- #
# domain_tracking
# --------------------------------------------------------------------------- #
DOMAIN_TRACKING_INSERT = _Stmt(
    "INSERT INTO algorand_platform.domain_tracking ("
    "domain, last_crawled_at, last_online_at, relevance_score, "
    "category, is_relevant, metadata, frontier_status"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

# Byte-identical to workers' own DomainTrackingStmts.LIST (that class now
# assigns its LIST attribute from this constant, same dedup as INSERT above).
# Added 2026-08-26 for algorand_shared.ecosystem_directory's read-only
# ecosystem_listed_domains() -- the one query
# algorand_shared.artifact_priority.ecosystem_listed_score needs from
# workers' domain_tracking-backed registry, extracted so backend (which has
# no app.modules.crawler.ecosystem_sync at all) can run the identical read
# itself instead of always failing open to 0.0.
DOMAIN_TRACKING_LIST = _Stmt(
    "SELECT domain, frontier_status, is_relevant, metadata "
    "FROM algorand_platform.domain_tracking LIMIT ?"
)

# --------------------------------------------------------------------------- #
# classifier_feedback_by_time
# --------------------------------------------------------------------------- #
CLASSIFIER_FEEDBACK_INSERT_BY_TIME = _Stmt(
    "INSERT INTO algorand_platform.classifier_feedback_by_time ("
    "bucket, created_at, feedback_id, url, approved"
    ") VALUES (?, ?, ?, ?, ?)"
)

# --------------------------------------------------------------------------- #
# service_registry
# --------------------------------------------------------------------------- #
SERVICE_REGISTRY_LIST_ALL = _Stmt(
    "SELECT service_id, display_name, match_kind, match_value, scrape_url, enabled, origin "
    "FROM algorand_platform.service_registry"
)
SERVICE_REGISTRY_GET_ID = _Stmt(
    "SELECT service_id FROM algorand_platform.service_registry WHERE service_id = ?"
)
SERVICE_REGISTRY_UPSERT = _Stmt(
    "INSERT INTO algorand_platform.service_registry ("
    "service_id, display_name, match_kind, match_value, scrape_url, enabled, "
    "updated_at, origin"
    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
SERVICE_REGISTRY_SET_ENABLED = _Stmt(
    "UPDATE algorand_platform.service_registry SET enabled = ?, updated_at = ? WHERE service_id = ?"
)
SERVICE_REGISTRY_GET_SCRAPE_URL = _Stmt(
    "SELECT scrape_url FROM algorand_platform.service_registry WHERE service_id = ?"
)
