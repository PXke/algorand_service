"""Admin review queue: list, approve, reject, edit, delete, and the one-off seed action (design doc sections 3.3, 6.3)."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime

from algorand_shared.slugs import unique_slug

from app.modules.ecosystem.models.domain import (
    DEFAULT_CATEGORY,
    REJECT_REASONS,
    SOURCE_SEEDED,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    EcosystemError,
    StoredProject,
    SubmissionQueueItem,
)
from app.modules.ecosystem.services.submission_service import (
    normalize_description,
    normalize_name,
    validate_category,
    validate_stage,
    validate_tags,
)
from app.modules.ecosystem.stores.factory import get_project_store

logger = logging.getLogger(__name__)


def list_queue(status: str, *, limit: int) -> list[SubmissionQueueItem]:
    """The admin queue read for one status partition, bounded."""
    return get_project_store().list_by_submission_status(status, limit=limit)


def get_entry(slug: str) -> StoredProject | None:
    """One entry by slug, any status (admin detail view)."""
    return get_project_store().get(slug)


def _ensure_monitored_service(domain: str, url: str) -> None:
    """Put an approved entry's domain under the crawler's existing daily watch (design doc section 6.2 "Workers: nothing in v1" -- reuses the SAME service_registry bridge the crawl-frontier admin approve flow already writes through, never a second copy of that write).

    Best-effort: a failure here must never block the approve decision itself
    (the registry entry is the durable artifact; the crawl watch is a
    bonus). Import is local because admin.api.routes is a large module this
    one function is reached into, not a package-level dependency of this
    service.
    """
    try:
        from app.core.cassandra import get_cassandra_session
        from app.modules.admin.api.routes import _register_domain_as_service

        _register_domain_as_service(
            get_cassandra_session(), domain, url, enqueued=True, now=datetime.now(tz=UTC)
        )
    except Exception:
        logger.warning(
            "ecosystem approve: failed to register monitored service for %s", domain, exc_info=True
        )


def approve(
    slug: str,
    *,
    wallet: str,
    name: str | None = None,
    description: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
) -> StoredProject:
    """Approve a pending (or previously rejected) entry, with optional inline edits (design doc section 3.3: the reviewer fixes marketing language rather than bouncing)."""
    store = get_project_store()
    existing = store.get(slug)
    if existing is None:
        raise EcosystemError("not_found", "No submission for that slug", http_status=404)

    updated = replace(
        existing,
        name=normalize_name(name) if name is not None else existing.name,
        description=normalize_description(description)
        if description is not None
        else existing.description,
        category=validate_category(category) if category is not None else existing.category,
        tags=validate_tags(tags) if tags is not None else existing.tags,
        status=STATUS_APPROVED,
        reviewed_at_epoch=int(datetime.now(tz=UTC).timestamp()),
        reviewed_by=wallet,
        reject_reason="",
    )
    store.upsert(updated)
    _ensure_monitored_service(updated.domain, updated.url)
    return updated


def reject(slug: str, *, wallet: str, reason: str) -> StoredProject:
    """Reject a pending entry with a reason from the closed list (design doc section 3.3)."""
    if reason not in REJECT_REASONS:
        raise EcosystemError(
            "invalid_request", f"reason must be one of: {', '.join(REJECT_REASONS)}"
        )
    store = get_project_store()
    existing = store.get(slug)
    if existing is None:
        raise EcosystemError("not_found", "No submission for that slug", http_status=404)

    updated = replace(
        existing,
        status=STATUS_REJECTED,
        reviewed_at_epoch=int(datetime.now(tz=UTC).timestamp()),
        reviewed_by=wallet,
        reject_reason=reason,
    )
    store.upsert(updated)
    return updated


def update_entry(
    slug: str,
    *,
    name: str | None = None,
    description: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    stage: str | None = None,
    open_source: bool | None = None,
    editor_pick: bool | None = None,
    repo_url: str | None = None,
    x402_url: str | None = None,
) -> StoredProject:
    """Admin edit of any entry, regardless of status. Rewrites category/tag projections if the entry is (still) approved."""
    from app.modules.ecosystem.services.submission_service import normalize_url

    store = get_project_store()
    existing = store.get(slug)
    if existing is None:
        raise EcosystemError("not_found", "No entry for that slug", http_status=404)

    updated = replace(
        existing,
        name=normalize_name(name) if name is not None else existing.name,
        description=normalize_description(description)
        if description is not None
        else existing.description,
        category=validate_category(category) if category is not None else existing.category,
        tags=validate_tags(tags) if tags is not None else existing.tags,
        stage=validate_stage(stage) if stage is not None else existing.stage,
        open_source=open_source if open_source is not None else existing.open_source,
        editor_pick=editor_pick if editor_pick is not None else existing.editor_pick,
        repo_url=normalize_url(repo_url, field_name="repo_url")
        if repo_url is not None
        else existing.repo_url,
        x402_url=normalize_url(x402_url, field_name="x402_url")
        if x402_url is not None
        else existing.x402_url,
    )
    store.upsert(updated)
    return updated


def delete_entry(slug: str) -> bool:
    """Remove an entry outright (abuse report / owner request), projections included."""
    return get_project_store().delete(slug)


def seed_from_ecosystem_listed(*, limit: int) -> dict:
    """Create pending, source=seeded entries for every domain the crawler already flags ecosystem_listed, one entry per domain, idempotent (design doc section 6.3).

    Reads the SAME two tables the crawler's own
    ecosystem_sync.ecosystem_listed_domains() reads (domain_tracking's
    metadata.ecosystem_listed flag, service_registry for a real
    display_name/scrape_url when one already exists) rather than a third
    copy of that filter. No submit-time liveness re-check here (that would
    be up to a few hundred synchronous outbound requests inside one admin
    HTTP call) -- last_online_at, when the crawler already has one, seeds
    the liveness fields instead; the periodic re-check beat
    (workers/app/modules/ecosystem_probe/) confirms it for real on its own
    schedule. Never overwrites an existing entry for a domain (idempotent,
    safe to re-run).
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import DomainTrackingStmts, ServiceRegistryStmts

    session = get_cassandra_session()
    store = get_project_store()

    services_by_domain: dict[str, object] = {}
    for row in session.execute(ServiceRegistryStmts.LIST_ALL):
        if getattr(row, "match_kind", None) == "domain" and row.match_value:
            services_by_domain[row.match_value] = row

    stats = {"scanned": 0, "created": 0, "skipped_existing": 0, "skipped_not_listed": 0}
    now_epoch = int(datetime.now(tz=UTC).timestamp())
    for row in session.execute(DomainTrackingStmts.LIST_ALL):
        if stats["created"] >= limit:
            break
        stats["scanned"] += 1
        meta = row.metadata or {}
        if meta.get("ecosystem_listed") != "true" or row.is_relevant is False:
            stats["skipped_not_listed"] += 1
            continue
        domain = row.domain
        if store.domain_status(domain) is not None:
            stats["skipped_existing"] += 1
            continue

        service_row = services_by_domain.get(domain)
        display_name = getattr(service_row, "display_name", None) or domain
        scrape_url = getattr(service_row, "scrape_url", None) or f"https://{domain}"
        last_online_epoch = int(row.last_online_at.timestamp()) if row.last_online_at else 0

        slug = unique_slug(
            display_name, fallback=domain, is_taken=lambda s: store.get(s) is not None
        )
        item = StoredProject(
            slug=slug,
            name=display_name,
            domain=domain,
            url=scrape_url,
            description="",
            category=DEFAULT_CATEGORY,
            source=SOURCE_SEEDED,
            status=STATUS_PENDING,
            submitted_at_epoch=now_epoch,
            last_probed_at_epoch=last_online_epoch,
            reachable=True if last_online_epoch else None,
        )
        if store.insert_if_domain_absent(item):
            stats["created"] += 1
        else:
            stats["skipped_existing"] += 1
    return stats


__all__ = [
    "approve",
    "delete_entry",
    "get_entry",
    "list_queue",
    "reject",
    "seed_from_ecosystem_listed",
    "update_entry",
]
