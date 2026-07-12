from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class ServiceSource:
    source_id: str
    source_type: str  # web | youtube | mail | bluesky
    url: str
    domain: str  # registrable domain (eTLD+1) for web sources, "" otherwise
    enabled: bool = True


def add_web_source(service_id: str, *, domain: str, url: str) -> None:
    """Attach a web source to a service and claim the domain's reverse lookup.
    Idempotent (PK upserts). source_id keys on the URL's HOST — one entity's
    subdomains (stake./app./docs.folks.finance) are distinct sources of ONE
    service, each contributing its pages to the aggregate. The reverse row is
    what lets a URL resolve to its OWNING service, so a merged domain
    (algorand.com → the algorand-co service) stops spawning its own service."""
    from urllib.parse import urlparse

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceSourceStmts

    if not service_id or not domain:
        return
    resolved_url = url or f"https://{domain}"
    host = (urlparse(resolved_url).netloc or "").lower() or domain
    session = get_cassandra_session()
    now = datetime.now(tz=UTC)
    session.execute(
        ServiceSourceStmts.UPSERT,
        (service_id, f"web:{host}", "web", resolved_url, domain, True, now),
    )
    session.execute(ServiceSourceStmts.UPSERT_BY_DOMAIN, (domain, service_id))


def list_sources(service_id: str) -> list[ServiceSource]:
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceSourceStmts

    if not service_id:
        return []
    rows = get_cassandra_session().execute(
        ServiceSourceStmts.LIST_FOR_SERVICE, (service_id,)
    )
    return [
        ServiceSource(
            source_id=r.source_id,
            source_type=r.source_type or "",
            url=r.url or "",
            domain=r.domain or "",
            enabled=bool(r.enabled) if r.enabled is not None else True,
        )
        for r in rows
    ]


def web_domains_for_service(service_id: str) -> list[str]:
    """Registrable domains of the service's enabled web sources (aggregation
    scope for the weekly service-watch context)."""
    return sorted(
        {
            s.domain
            for s in list_sources(service_id)
            if s.enabled and s.source_type == "web" and s.domain
        }
    )


def service_for_domain(domain: str) -> str:
    """service_id owning this registrable domain, or "" when unclaimed."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceSourceStmts

    if not domain:
        return ""
    row = get_cassandra_session().execute(
        ServiceSourceStmts.GET_BY_DOMAIN, (domain,)
    ).one()
    return str(row.service_id) if row and row.service_id else ""


def merge_services(*, target_service_id: str, source_service_ids: list[str]) -> dict:
    """Fold whole services into ``target_service_id``: their sources move over,
    their domains re-point, and the emptied services are DISABLED in the
    registry (not deleted — audit trail + snapshots keep their history). Mirrors
    the backend admin store's merge_services (kept in sync manually) — added
    here so the worker side can auto-merge a service whose scrape resolved to a
    domain a DIFFERENT service already owns (e.g. a rebrand redirect), not just
    the admin's manual "merge duplicates" action."""
    import contextlib

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceRegistryStmts, ServiceSourceStmts, SnapshotStmts

    session = get_cassandra_session()
    now = datetime.now(tz=UTC)
    moved: list[str] = []
    for source_service in source_service_ids:
        if not source_service or source_service == target_service_id:
            continue
        # Carry over the merged-away service's poll history — otherwise the
        # canonical id's snapshot lineage starts empty, its next poll looks
        # like the first time this content has ever been seen, and it
        # mistakenly re-fires as a brand-new SERVICE_DISCOVERY for a service
        # already covered under its pre-merge id (the nf.domains incident:
        # docs-nf-domains had 2 weeks of history the merge silently orphaned).
        # Only backfills when the target has no snapshot of its own yet —
        # never clobber fresher canonical history with a stale merged-in one.
        with contextlib.suppress(Exception):
            src_snap = session.execute(
                SnapshotStmts.GET_LATEST, (f"svc:{source_service}",)
            ).one()
            if src_snap is not None:
                tgt_source_id = f"svc:{target_service_id}"
                if session.execute(SnapshotStmts.GET_LATEST, (tgt_source_id,)).one() is None:
                    session.execute(
                        SnapshotStmts.INSERT,
                        (tgt_source_id, now, src_snap.content_hash, src_snap.title, src_snap.body),
                    )
        rows = list(session.execute(ServiceSourceStmts.LIST_FOR_SERVICE, (source_service,)))
        for r in rows:
            session.execute(
                ServiceSourceStmts.UPSERT,
                (
                    target_service_id,
                    r.source_id,
                    r.source_type or "",
                    r.url or "",
                    r.domain or "",
                    bool(r.enabled) if r.enabled is not None else True,
                    now,
                ),
            )
            if (r.source_type or "") == "web" and r.domain:
                session.execute(ServiceSourceStmts.UPSERT_BY_DOMAIN, (r.domain, target_service_id))
            moved.append(r.source_id)
        # A merged-away service with NO recorded sources still owns its legacy
        # scrape_url domain implicitly — claim it for the target so the old
        # service can't be resurrected by the next crawl of that domain.
        if not rows:
            from app.modules.crawler.domain_tracker import domain_from_url

            reg = session.execute(ServiceRegistryStmts.GET_SCRAPE_URL, (source_service,)).one()
            url = (reg.scrape_url or "") if reg else ""
            domain = domain_from_url(url) if url else ""
            if domain:
                add_web_source(target_service_id, domain=domain, url=url)
                moved.append(f"web:{domain}")
        session.execute(ServiceSourceStmts.DELETE_FOR_SERVICE, (source_service,))
        session.execute(ServiceRegistryStmts.SET_ENABLED, (False, now, source_service))
    return {"target": target_service_id, "moved_sources": moved}
