"""One-off: collapse subdomain rows in domain_tracking into their registrable domain (eTLD+1), so e.g. blog./docs./explorer.perawallet.app merge into a single perawallet.app entry. Mirrors the acceptance-path fix in AdminCassandraStore.

Run on a host with the app env loaded:
    python -m app.modules.admin.domain_cleanup            # dry-run (default)
    python -m app.modules.admin.domain_cleanup --apply
"""

from __future__ import annotations

import logging
import sys

from app.modules.admin.stores.cassandra import AdminCassandraStore

logger = logging.getLogger(__name__)

# Higher rank wins when merging a group's frontier_status.
_STATUS_RANK = {"approved": 3, "pending": 2, "dead_end": 1, "": 0, None: 0}


def _registrable(host: str) -> str:
    return AdminCassandraStore._domain_from_url(f"http://{host}")


def _merge_status(statuses: list[str | None]) -> str | None:
    best = max(statuses, key=lambda s: _STATUS_RANK.get(s, 0), default=None)
    return best or None


def cleanup(*, apply: bool = False) -> dict:
    """Merge subdomain rows into their registrable-domain row, deleting the rest when apply is True."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import DomainTrackingStmts

    session = get_cassandra_session()
    rows = list(session.execute(DomainTrackingStmts.LIST_ALL))
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(_registrable(r.domain or ""), []).append(r)

    merged_groups = deleted = 0
    for reg, members in sorted(groups.items()):
        if not reg:
            continue
        # Nothing to do when the group is already the single registrable row.
        if len(members) == 1 and members[0].domain == reg:
            continue
        merged_groups += 1
        status = _merge_status([m.frontier_status for m in members])
        relevance = max(
            (m.relevance_score for m in members if m.relevance_score is not None), default=None
        )
        is_relevant = (
            True
            if status == "approved"
            else False
            if status == "dead_end"
            else any(m.is_relevant for m in members)
        )
        category = next((m.category for m in members if m.category), None)
        last_crawled = max((m.last_crawled_at for m in members if m.last_crawled_at), default=None)
        last_online = max((m.last_online_at for m in members if m.last_online_at), default=None)
        meta: dict = {}
        for m in members:
            meta.update(dict(m.metadata or {}))
        if status:
            meta["frontier_status"] = status

        victims = [m.domain for m in members if m.domain != reg]
        logger.info("%s  <- status=%s  (%d rows)", reg, status, len(members))
        for m in members:
            mark = "KEEP/UPGRADE" if m.domain == reg else "delete"
            logger.info("    %-12s %s  [%s]", mark, m.domain, m.frontier_status)

        if apply:
            session.execute(
                DomainTrackingStmts.INSERT,
                (reg, last_crawled, last_online, relevance, category, is_relevant, meta, status),
            )
            for victim in victims:
                session.execute(DomainTrackingStmts.DELETE, (victim,))
        deleted += len(victims)

    result = {"groups_merged": merged_groups, "rows_deleted": deleted, "applied": apply}
    logger.info(result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cleanup(apply="--apply" in sys.argv)
