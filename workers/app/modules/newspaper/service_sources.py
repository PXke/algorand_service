"""Track a service's known web sources and merge duplicate services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class ServiceSource:
    """One known web/social/mail source for a service."""

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
    (algorand.com → the algorand-co service) stops spawning its own service.
    """
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
    """List the sources attached to a service."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceSourceStmts

    if not service_id:
        return []
    rows = get_cassandra_session().execute(ServiceSourceStmts.LIST_FOR_SERVICE, (service_id,))
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


def service_for_domain(domain: str) -> str:
    """service_id owning this registrable domain, or "" when unclaimed."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceSourceStmts

    if not domain:
        return ""
    row = get_cassandra_session().execute(ServiceSourceStmts.GET_BY_DOMAIN, (domain,)).one()
    return str(row.service_id) if row and row.service_id else ""


def venue_owner_for_url(url: str, *, own_service_id: str) -> str:
    """The service_id that actually OWNS this URL's registrable domain per the by-domain reverse index -- but ONLY when that owner is a DIFFERENT service than `own_service_id` itself. Returns "" when the domain is unresolvable/unclaimed, or already owned by `own_service_id` (nothing to correct either way).

    This is the artifact-level counterpart to `domain_tracker.
    ensure_monitored_service`'s own registry-level dedup guard: that function
    stops a SECOND `service_registry` row from being spawned for a domain
    another service already owns, but only at the moment a domain is first
    approved/registered. It can't retroactively fix an artifact whose own
    `service_id` was already minted before the reverse index knew better (a
    race, or a legacy/seeded service whose `add_web_source` claim never ran
    -- see `service_reconciliation`'s bug-class-1 docstring), which is
    exactly the shape of a plain "crawler"-channel artifact discovered
    against a domain that turns out to already be a well-covered, distinctly
    -named venue (root-caused 2026-08-2x: forum.algorand.co discovered
    fresh as "forum-algorand-co" despite forum.algorand.co already being the
    established "algorand-forum" venue).

    Shared by two call sites that both need this exact same "is this
    artifact's own domain secretly owned by someone else" check:
    `ingest_signal._insert_artifact_for_signal` (at artifact-creation time,
    for the generic "crawler" channel) and `service_reconciliation.
    backfill_missing_venue_service_ids` (the periodic safety net for
    anything that landed before that creation-time check existed, or before
    the reverse index itself was corrected).

    Checks the URL's EXACT host first, then its collapsed registrable
    domain (`domain_from_url`'s eTLD+1) as a fallback -- in that order,
    never the reverse. `domain_from_url`'s subdomain collapse is a generic
    heuristic (it has no way to know forum.algorand.co is deliberately its
    own distinct "algorand-forum" venue rather than part of "algorand.co"'s
    own site), while a `service_registry` domain claim -- seeded or
    admin-curated -- is keyed on whatever exact string that entry declared,
    which for such a deliberate subdomain-carve-out IS the full host, not
    the collapsed parent. Checking the exact host first means a real
    override like that resolves correctly without the parent domain's own
    (unrelated) owner ever being consulted; checking the collapsed domain
    second is what makes this fall back correctly for the ordinary
    (unclaimed-subdomain) case. See `domain_tracker.full_host_from_url`.
    """
    from app.modules.crawler.domain_tracker import domain_from_url, full_host_from_url

    if not url or not own_service_id:
        return ""
    for candidate in dict.fromkeys(d for d in (full_host_from_url(url), domain_from_url(url)) if d):
        owner = service_for_domain(candidate)
        if owner and owner != own_service_id:
            return owner
    return ""


def merge_services(*, target_service_id: str, source_service_ids: list[str]) -> dict:
    """Fold whole services into ``target_service_id``: their sources move over, their domains re-point, and the emptied services are DISABLED in the registry (not deleted — audit trail + snapshots keep their history). Mirrors the backend admin store's merge_services (kept in sync manually) — added here so the worker side can auto-merge a service whose scrape resolved to a domain a DIFFERENT service already owns (e.g. a rebrand redirect), not just the admin's manual "merge duplicates" action."""
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
            src_snap = session.execute(SnapshotStmts.GET_LATEST, (f"svc:{source_service}",)).one()
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
