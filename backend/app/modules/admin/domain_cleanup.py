"""One-off: collapse subdomain rows in domain_tracking into their registrable
domain (eTLD+1), so e.g. blog./docs./explorer.perawallet.app merge into a single
perawallet.app entry. Mirrors the acceptance-path fix in AdminCassandraStore.

Run on a host with the app env loaded:
    python -m app.modules.admin.domain_cleanup            # dry-run (default)
    python -m app.modules.admin.domain_cleanup --apply
"""

from __future__ import annotations

import sys

from app.modules.admin.stores.cassandra import AdminCassandraStore

# Higher rank wins when merging a group's frontier_status.
_STATUS_RANK = {"approved": 3, "pending": 2, "dead_end": 1, "": 0, None: 0}


def _registrable(host: str) -> str:
    return AdminCassandraStore._domain_from_url(f"http://{host}")


def _merge_status(statuses: list[str | None]) -> str | None:
    best = max(statuses, key=lambda s: _STATUS_RANK.get(s, 0), default=None)
    return best or None


def cleanup(*, apply: bool = False) -> dict:
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    rows = list(
        session.execute(
            "SELECT domain, last_crawled_at, last_online_at, relevance_score, "
            "category, is_relevant, metadata, frontier_status FROM domain_tracking"
        )
    )
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
        relevance = max((m.relevance_score for m in members if m.relevance_score is not None),
                        default=None)
        is_relevant = (
            True if status == "approved" else False if status == "dead_end"
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
        print(f"{reg}  <- status={status}  ({len(members)} rows)")
        for m in members:
            mark = "KEEP/UPGRADE" if m.domain == reg else "delete"
            print(f"    {mark:12} {m.domain}  [{m.frontier_status}]")

        if apply:
            session.execute(
                "INSERT INTO domain_tracking (domain, last_crawled_at, last_online_at, "
                "relevance_score, category, is_relevant, metadata, frontier_status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (reg, last_crawled, last_online, relevance, category, is_relevant, meta, status),
            )
            for victim in victims:
                session.execute("DELETE FROM domain_tracking WHERE domain = %s", (victim,))
        deleted += len(victims)

    result = {"groups_merged": merged_groups, "rows_deleted": deleted, "applied": apply}
    print(result)
    return result


if __name__ == "__main__":
    cleanup(apply="--apply" in sys.argv)
