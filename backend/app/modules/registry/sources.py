"""Service layer: map N sources (web domains, youtube channels, mail senders)
onto one service, so multi-domain entities (algorand.co + algorand.com + the
forum) feed a single weekly service-watch aggregate instead of composing
near-duplicate articles per domain. Backend twin of the workers'
``app.modules.newspaper.service_sources`` — keep behaviours in sync."""
from __future__ import annotations

from datetime import UTC, datetime


def domain_from_url(url: str) -> str:
    from app.modules.admin.stores.cassandra import AdminCassandraStore

    return AdminCassandraStore._domain_from_url(url)


def add_web_source(service_id: str, *, domain: str, url: str) -> None:
    """Attach a web source to a service and claim the domain's reverse lookup
    (idempotent). source_id keys on the URL's HOST so one entity's subdomains
    stay distinct sources of ONE service — mirrors the workers' twin."""
    from urllib.parse import urlparse

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceSourceStmts

    if not service_id or not domain:
        return
    resolved_url = url or f"https://{domain}"
    host = (urlparse(resolved_url).netloc or "").lower() or domain
    session = get_cassandra_session()
    session.execute(
        ServiceSourceStmts.UPSERT,
        (
            service_id,
            f"web:{host}",
            "web",
            resolved_url,
            domain,
            True,
            datetime.now(tz=UTC),
        ),
    )
    session.execute(ServiceSourceStmts.UPSERT_BY_DOMAIN, (domain, service_id))


def service_for_domain(domain: str) -> str:
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceSourceStmts

    if not domain:
        return ""
    row = get_cassandra_session().execute(
        ServiceSourceStmts.GET_BY_DOMAIN, (domain,)
    ).one()
    return str(row.service_id) if row and row.service_id else ""


def list_sources(service_id: str) -> list[dict]:
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceSourceStmts

    rows = get_cassandra_session().execute(
        ServiceSourceStmts.LIST_FOR_SERVICE, (service_id,)
    )
    return [
        {
            "source_id": r.source_id,
            "source_type": r.source_type or "",
            "url": r.url or "",
            "domain": r.domain or "",
            "enabled": bool(r.enabled) if r.enabled is not None else True,
        }
        for r in rows
    ]


def merge_services(*, target_service_id: str, source_service_ids: list[str]) -> dict:
    """Fold whole services into ``target_service_id``: their sources move over,
    their domains re-point, and the emptied services are DISABLED in the
    registry (not deleted — audit trail + snapshots keep their history). The
    target service's next weekly poll then aggregates across all moved domains.
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceRegistryStmts, ServiceSourceStmts

    session = get_cassandra_session()
    now = datetime.now(tz=UTC)
    moved: list[str] = []
    for source_service in source_service_ids:
        if not source_service or source_service == target_service_id:
            continue
        rows = list(
            session.execute(ServiceSourceStmts.LIST_FOR_SERVICE, (source_service,))
        )
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
                session.execute(
                    ServiceSourceStmts.UPSERT_BY_DOMAIN, (r.domain, target_service_id)
                )
            moved.append(r.source_id)
        # A merged-away service with NO recorded sources still owns its legacy
        # scrape_url domain implicitly — claim it for the target so the old
        # service can't be resurrected by the next crawl of that domain.
        if not rows:
            reg = session.execute(
                ServiceRegistryStmts.GET_SCRAPE_URL, (source_service,)
            ).one()
            url = (reg.scrape_url or "") if reg else ""
            domain = domain_from_url(url) if url else ""
            if domain:
                add_web_source(target_service_id, domain=domain, url=url)
                moved.append(f"web:{domain}")
        session.execute(ServiceSourceStmts.DELETE_FOR_SERVICE, (source_service,))
        session.execute(
            ServiceRegistryStmts.SET_ENABLED, (False, now, source_service)
        )
    return {"target": target_service_id, "moved_sources": moved}
