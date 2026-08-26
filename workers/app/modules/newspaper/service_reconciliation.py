"""Ongoing automated detection for the two service-duplication bug classes found in the 2026-08-2x new-service-lane audit (see MEMORY.md / the platform owner's audit of the guaranteed-new-service platform lane).

Bug class 1 -- literal domain-registry duplicates: `domain_tracker.
ensure_monitored_service` never spawns a second `service_registry` row for a
domain another service already owns, but that guard only works when the
owning service's domain is actually present in the `service_sources`
by-domain reverse index (`service_for_domain`). A legacy/seeded row that
never called `add_web_source` (the exact gap `deploy/scripts/
seed_service_registry.py` had, fixed the same change as this module) is
invisible to that guard, so the platform's own crawl of that domain later
spawns a genuine duplicate `service_registry` row.

Bug class 2 -- per-item lanes with no venue concept: forum/xgov/youtube/
bluesky mint a synthetic service_id PER ITEM, so an artifact's own
`service_id` never literal-matches a prior published article's service_id
even when the underlying VENUE is well covered (fixed by the
`venue_service_id` column + the four lanes setting it at insert time -- see
`artifact_store.Artifact.venue_service_id` and `to_compose_selection.
_artifact_pool`). This module's `backfill_missing_venue_service_ids` is the
safety net for anything that still lands without it (an artifact inserted
before that fix deployed, or a future lane that forgets to pass it).

Both scans are read-mostly and cheap (bounded by the enabled service
registry / the pending-artifact index, mirroring `artifact_priority.
sweep_artifact_priorities`'s own scope), and every auto-action they take is
deterministic and conservative by design -- see each function's own
docstring for exactly what counts as "clear-cut" versus "flag, don't act".
Anything the least bit ambiguous is logged via `logger.warning`, never
acted on, so this is safe to run unattended in prod on a periodic beat (see
`tasks/service_reconciliation_tasks.py`).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Every monitored Bluesky account resolves to this one shared platform host —
# never a real single-owner domain. domain_tracker.ensure_monitored_service
# already refuses to claim it for exactly this reason; mirrored here so a
# stray service_registry row someone hand-entered with match_value=bsky.app
# can never be treated as a domain-ownership duplicate of another one.
_SHARED_PLATFORM_DOMAINS = frozenset({"bsky.app"})

# Lanes that mint a synthetic "<venue>:<item>" service_id, keyed by
# artifacts.channel — see backfill_missing_venue_service_ids. Only lanes
# where the venue is recoverable purely by SPLITTING the per-item id (and
# then verifying that prefix against the real service registry) belong
# here; forum/xgov use a fixed constant instead (their venue isn't encoded
# in the id at all) and are handled as their own explicit branches.
_COMPOSITE_ID_CHANNELS = frozenset({"youtube", "bluesky"})


# --------------------------------------------------------------------------- #
# Bug class 1 -- domain-registry duplicates
# --------------------------------------------------------------------------- #


def find_domain_registry_duplicates() -> list[dict[str, str]]:
    """Enabled, domain-matched `service_registry` rows whose OWN claimed domain is owned, in the `service_sources` by-domain reverse index, by a DIFFERENT service_id.

    This is exactly the bug-class-1 shape: two `service_registry` rows for
    the same real-world domain, because the second row's creation path
    never checked (or the first row's domain was never indexed for the
    check to find). A row with NO reverse-index owner yet is NOT a
    duplicate by this function -- that's an unindexed-but-unclaimed domain,
    handled separately by the self-heal branch of
    `reconcile_domain_duplicates` (claims it, doesn't merge anything).
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceRegistryStmts
    from app.modules.newspaper.service_sources import service_for_domain

    findings: list[dict[str, str]] = []
    rows = get_cassandra_session().execute(ServiceRegistryStmts.LIST_ALL)
    for row in rows:
        if not row.enabled or (row.match_kind or "") != "domain":
            continue
        domain = (row.match_value or "").strip().lower()
        if not domain or domain in _SHARED_PLATFORM_DOMAINS:
            continue
        owner = service_for_domain(domain)
        if owner and owner != row.service_id:
            findings.append(
                {
                    "service_id": row.service_id,
                    "domain": domain,
                    "owner_service_id": owner,
                    "origin": getattr(row, "origin", "") or "",
                }
            )
    return findings


def reconcile_domain_duplicates() -> dict[str, object]:
    """Scan every enabled domain-matched `service_registry` row and act, conservatively, on what it finds.

    Three outcomes per row:

      1. INDEXED (self-heal, never a merge): the row's own domain has no
         reverse-index owner yet -- a legacy/seeded row that predates
         `add_web_source`. Claims it via `add_web_source`, exactly what
         `seed_service_registry.py` now does at seed time. Nothing is lost
         or moved; this only makes the row visible to future duplicate
         checks (its own and everyone else's).

      2. MERGED (clear-cut duplicate): the row's domain is already owned by
         a DIFFERENT, enabled, non-admin-curated service. Same-registrable-
         domain-means-same-real-world-entity is the exact assumption
         `add_web_source`'s own docstring and `domain_tracker.
         ensure_monitored_service` already rely on everywhere else, so
         acting on it here is consistent with established behavior, not a
         new risk. `merge_services` folds the row's own service away
         (its sources move to the owner, it's disabled, never deleted).

      3. FLAGGED (ambiguous, no action): the domain is owned by a different
         service, but either (a) this row's own `origin == "admin"` -- a
         human explicitly curated it, so an automatic merge could erase a
         deliberate choice -- or (b) the owner service_id isn't itself
         currently enabled (a stale/inconsistent reverse-index entry, not
         something to blindly trust). Logged via `logger.warning` for
         manual review; no Cassandra write happens for these.
    """
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ServiceRegistryStmts
    from app.modules.newspaper.service_sources import (
        add_web_source,
        merge_services,
        service_for_domain,
    )

    session = get_cassandra_session()
    rows = list(session.execute(ServiceRegistryStmts.LIST_ALL))
    enabled_ids = {row.service_id for row in rows if row.enabled}

    indexed: list[str] = []
    merged: list[dict[str, str]] = []
    flagged: list[dict[str, str]] = []

    for row in rows:
        if not row.enabled or (row.match_kind or "") != "domain":
            continue
        domain = (row.match_value or "").strip().lower()
        if not domain or domain in _SHARED_PLATFORM_DOMAINS:
            continue

        owner = service_for_domain(domain)
        if not owner:
            add_web_source(row.service_id, domain=domain, url=row.scrape_url or f"https://{domain}")
            indexed.append(row.service_id)
            continue
        if owner == row.service_id:
            continue  # already correctly owned -- nothing to do

        origin = getattr(row, "origin", "") or ""
        finding = {
            "service_id": row.service_id,
            "domain": domain,
            "owner_service_id": owner,
            "origin": origin,
        }
        if origin == "admin" or owner not in enabled_ids:
            logger.warning("service_reconcile: ambiguous domain duplicate, flagged not merged: %s", finding)
            flagged.append(finding)
            continue

        merge_services(target_service_id=owner, source_service_ids=[row.service_id])
        merged.append(finding)

    return {"indexed": indexed, "merged": merged, "flagged": flagged}


# --------------------------------------------------------------------------- #
# Bug class 2 -- per-item artifacts missing venue_service_id
# --------------------------------------------------------------------------- #


def backfill_missing_venue_service_ids() -> dict[str, object]:
    """Scan PENDING artifacts (same scope as the priority sweep -- non-pending artifacts never reach `_artifact_pool` again) for the per-item lanes and backfill `venue_service_id` on the DETERMINISTIC, unambiguous cases only:

      - channel == "forum" -> config.FORUM_VENUE_SERVICE_ID (a fixed
        constant; forum.algorand.co has exactly one venue).
      - channel == "crawler" AND url starts with the xGov proposals path ->
        config.XGOV_VENUE_SERVICE_ID (xgov_watch's artifacts land on the
        generic "crawler" channel -- see ingest_signal._CHANNEL_BY_SOURCE_KIND
        -- so the URL shape, not the channel, is what identifies them).
      - channel in {"youtube", "bluesky"} AND the service_id splits as
        "<venue>:<item>" AND <venue> is itself a real, ENABLED
        service_registry row -> that <venue>.

    Anything else that still looks like it might need one (a service_id
    containing ":" on a channel not covered above) is left untouched and
    logged via `logger.warning` for manual review -- never guessed at, per
    this module's own conservative-by-design rule.
    """
    from app.core import config
    from app.modules.chain_tail.registry_cache import load_enabled_services
    from app.modules.newspaper.artifact_store import (
        list_pending_artifacts,
        set_artifact_venue_service_id,
    )

    enabled_ids = {entry.service_id for entry in load_enabled_services()}

    backfilled: list[dict[str, str]] = []
    flagged: list[dict[str, str]] = []

    for artifact in list_pending_artifacts():
        if artifact.venue_service_id:
            continue
        service_id = artifact.service_id or ""
        channel = artifact.channel or ""

        venue: str | None = None
        if channel == "forum":
            venue = config.FORUM_VENUE_SERVICE_ID
        elif channel == "crawler" and (artifact.url or "").startswith(
            "https://xgov.algorand.co/proposals/"
        ):
            venue = config.XGOV_VENUE_SERVICE_ID
        elif channel in _COMPOSITE_ID_CHANNELS and ":" in service_id:
            candidate = service_id.rsplit(":", 1)[0]
            if candidate in enabled_ids:
                venue = candidate

        if venue:
            set_artifact_venue_service_id(artifact.artifact_id, venue)
            backfilled.append(
                {"artifact_id": artifact.artifact_id, "service_id": service_id, "venue_service_id": venue}
            )
        elif ":" in service_id:
            finding = {"artifact_id": artifact.artifact_id, "service_id": service_id, "channel": channel}
            logger.warning(
                "service_reconcile: per-item-shaped artifact missing venue_service_id, flagged: %s",
                finding,
            )
            flagged.append(finding)

    return {"backfilled": backfilled, "flagged": flagged}


# --------------------------------------------------------------------------- #
# Bug class 3 -- duplicate PENDING artifacts for the same service_id
# --------------------------------------------------------------------------- #


def find_duplicate_pending_artifacts() -> dict[str, list[str]]:
    """service_id -> artifact_ids, for every service_id with more than one PENDING artifact -- a violation of insert_artifact's own dedup invariant ("at most one PENDING artifact per service_id").

    Structurally impossible via the normal insert_artifact path (it always
    finds and folds any existing pending row for the same service_id before
    creating a new one) -- this only happens when something writes
    `artifacts.service_id` directly, bypassing that check (e.g. a one-off
    Cassandra repoint of an artifact's service_id during a service merge,
    root-caused 2026-08-26: pera-wallet/perawallet-app).
    """
    from collections import defaultdict

    from app.modules.newspaper.artifact_store import list_pending_artifacts

    grouped: dict[str, list[str]] = defaultdict(list)
    for artifact in list_pending_artifacts():
        if artifact.service_id:
            grouped[artifact.service_id].append(artifact.artifact_id)
    return {sid: ids for sid, ids in grouped.items() if len(ids) > 1}


def reconcile_duplicate_pending_artifacts() -> dict[str, object]:
    """Fold every service_id's duplicate PENDING artifacts down to one, via the SAME concatenate-on-repeat-insert path `insert_artifact` already runs on a normal repeat crawl -- never a bespoke merge routine of this function's own.

    Oldest-first fold: keeps the oldest duplicate as the running survivor;
    for each newer duplicate, discards it and re-inserts its own content via
    `insert_artifact(service_id=...)`, which finds the current survivor
    (the only pending row left for that service_id at that moment),
    concatenates the two via the exact same code path a real second crawl
    of that service would take, discards the survivor, and returns a fresh
    merged artifact_id -- which becomes the survivor for the next fold.
    Always converges to exactly one pending artifact per service_id,
    regardless of how many duplicates exist.
    """
    from app.modules.newspaper.artifact_store import (
        DISCARDED,
        get_artifact_content,
        insert_artifact,
        list_pending_artifacts,
        mark_artifact_status,
    )

    dupes = find_duplicate_pending_artifacts()
    merged: list[dict[str, object]] = []
    for service_id, artifact_ids in dupes.items():
        by_id = {a.artifact_id: a for a in list_pending_artifacts() if a.artifact_id in artifact_ids}
        ordered = sorted(by_id.values(), key=lambda a: a.created_at)

        survivor_id = ordered[0].artifact_id
        for row in ordered[1:]:
            content = get_artifact_content(row.artifact_id)
            mark_artifact_status(row.artifact_id, DISCARDED)
            if content is None:
                continue
            survivor_id, _created = insert_artifact(
                service_id=service_id,
                url=row.url,
                channel=row.channel,
                content=content.content,
                title=content.title,
                metadata=content.metadata,
                event_date=row.event_date,
                venue_service_id=row.venue_service_id,
            )
        merged.append({"service_id": service_id, "artifact_ids": artifact_ids, "survivor": survivor_id})

    return {"merged": merged}
