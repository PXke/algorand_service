"""One-time reconciliation for the 2026-08-26 gray-zone-approval audit: domains sitting at `frontier_status="approved"` whose `content_relevance` score never actually cleared the bar for a confident verdict.

The audit found 665 such domains. Every one of them got to `approved` off a
shallow classification pass -- either a single-page discovery-time preview
(`link_extractor._process_external_link`'s own auto-approve) or a
`classify_pending_domains` same-domain-link sample (see `url_queue_tasks.
_sample_domain_pages`, capped at `FRONTIER_CLASSIFY_SAMPLE_PAGES` pages) --
and landed with `content_relevance` in `[FRONTIER_CONTENT_REJECT_SCORE,
FRONTIER_CONTENT_PROMOTE_SCORE)`. That range is not a verdict at all: it's
the SAME gray zone `reevaluate_pending_domains`'s own promote check treats as
"not confident enough to act on" for domains still sitting `pending` --
these 665 just happen to be on the `approved` side of the fence instead,
where nothing ever re-visits them. `reevaluate_pending_domains` only scans
`frontier_status="pending"` rows; `classify_pending_domains` likewise only
ever looks at `pending` rows. An `approved` domain, gray-zone score or not,
is invisible to both, so absent this module it would sit there forever,
crawled into the research corpus on the strength of a verdict its own
architecture calls "not resolved yet" everywhere else in the pipeline.

The fix is NOT to re-decide these domains' relevance here -- that decision
already has a well-tested home in `deep_classify_domain` / `_classify_and_store_
domain` (samples up to `FRONTIER_DEEP_CLASSIFY_MAX_PAGES` pages in random
order, stops at the first relevant hit, falls back to SearXNG external
corroboration, and as of 2026-08-25 also calls `ensure_monitored_service` on
approval -- see that task's own docstring in `tasks/url_queue_tasks.py`).
This module's only job is to get gray-zone domains INTO that machinery,
without ever running the crawl itself and without ever dispatching more than
a handful at once.

Two functions, cleanly split by side effect:

  - `find_gray_zone_domains` is read-only reporting: what's out there, right
    now, with no Cassandra writes and no Celery dispatch. Safe to call as
    often as wanted, including directly from a shell, to check the real
    count or eyeball a sample before touching anything.

  - `dispatch_gray_zone_deep_classify` is the one function that acts, and it
    acts in exactly the same shape `classify_pending_domains(limit=N)` and
    `reevaluate_pending_domains(limit=N)` already do: a small, explicit
    `limit` per call (default 5), `dry_run=True` by default so a real
    Cassandra write / Celery dispatch only happens when a caller opts in
    deliberately, and -- critically -- it never runs `deep_classify_domain`
    itself. It only writes the same "escalated, in flight" bookkeeping
    `_classify_and_store_domain` already writes for its own shallow-sample
    escalations (`deep_classify_queued="true"`, `frontier_status="pending"`)
    and hands the real work to Celery's `send_task` on the `scrape` queue --
    the identical dispatch call site `_classify_and_store_domain` uses, so a
    gray-zone-triggered deep classify is indistinguishable, at the worker
    pool, from an ordinary one. `limit` bounds concurrency the same way it
    always has there; this module adds no new bulk-dispatch path for the
    2026-08-2x resource-contention incident (a big batch of
    `classify_pending_domains` chunks fired at once, saturating the
    concurrency=4 scrape pool and starving unrelated admin/routine tasks) to
    recur through.

Flipping a domain's `frontier_status` to "pending" the moment it's dispatched
is also what makes repeat calls safe without any extra bookkeeping table:
a dispatched domain no longer matches this module's own
`frontier_status="approved"` scan, so the NEXT call naturally picks up a
different slice of the backlog, and the existing `deep_classify_queued`
dedup guard (shared with the ordinary escalation path) means a domain
already in flight -- whether queued by this module or by an ordinary
`classify_pending_domains` run -- is never double-dispatched.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from cassandra.cluster import Session as CassandraSession


def _gray_zone_rows(session: CassandraSession, scan_limit: int) -> list[tuple[str, dict, float]]:
    """(domain, metadata, content_relevance) for every domain in the scan window that is `frontier_status="approved"` with a `content_relevance` score inside `[FRONTIER_CONTENT_REJECT_SCORE, FRONTIER_CONTENT_PROMOTE_SCORE)` -- the exact gray-zone definition from the audit this module answers. Domain-sorted (not score- or token-order) purely so successive calls with the same `scan_limit` see a stable, deterministic ordering.

    Excludes any row already carrying `deep_classify_queued="true"` in
    metadata -- 2026-08-26 fix: `frontier_status` is its own real Cassandra
    COLUMN (see `DomainTrackingStmts.LIST`'s own SELECT), but
    `dispatch_gray_zone_deep_classify` only ever writes via
    `UPDATE_METADATA` (metadata map only, never the column). Stuffing
    `"frontier_status": "pending"` into metadata therefore never changes
    what this function's own `row.frontier_status or meta.get(...)` check
    sees -- the real column stays "approved" -- so a dispatched domain
    would otherwise keep reappearing in every future scan forever, forever
    reporting all ~700 as "still gray-zone" even as they get worked
    through. `deep_classify_queued` IS a metadata-only flag by design
    (mirrors `_classify_and_store_domain`'s own escalation bookkeeping) and
    reads back correctly, so filtering on it here is what actually makes
    the backlog visibly shrink across repeat calls -- not the inert
    frontier_status write, which callers should not rely on.

    A row with no parseable `content_relevance` at all (never scored, or a
    stray non-numeric value) is skipped, not treated as gray-zone -- this
    module only targets domains that DO have a shallow verdict sitting in
    the ambiguous range, not domains missing a verdict entirely (those are
    a different, pre-existing gap: an approved domain with literally no
    content_relevance was never content-scored in the first place, most
    likely a curated ecosystem-directory or admin approval that bypassed
    `classify_pending_domains` altogether -- out of scope here).
    """
    from app.core.config import FRONTIER_CONTENT_PROMOTE_SCORE, FRONTIER_CONTENT_REJECT_SCORE
    from app.core.statements import DomainTrackingStmts

    rows: list[tuple[str, dict, float]] = []
    for row in session.execute(DomainTrackingStmts.LIST, (scan_limit,)):
        meta = dict(row.metadata or {})
        status = row.frontier_status or meta.get("frontier_status")
        if status != "approved" or row.is_relevant is False:
            continue
        if meta.get("deep_classify_queued") == "true":
            continue
        try:
            score = float(meta.get("content_relevance", ""))
        except (TypeError, ValueError):
            continue
        if not (FRONTIER_CONTENT_REJECT_SCORE <= score < FRONTIER_CONTENT_PROMOTE_SCORE):
            continue
        rows.append((row.domain, meta, score))
    rows.sort(key=lambda item: item[0])
    return rows


def find_gray_zone_domains(
    limit: int | None = None, *, scan_limit: int = 5000
) -> list[dict[str, object]]:
    """Read-only report: every `frontier_status="approved"` domain whose `content_relevance` sits in the genuine gray zone AND isn't already queued for a real deep-classify. Makes NO Cassandra writes and dispatches NOTHING -- safe to call as often as wanted, including to confirm the real current count before ever calling `dispatch_gray_zone_deep_classify`.

    Already-`deep_classify_queued` domains are excluded by `_gray_zone_rows`
    itself (see that function's own docstring for why) -- this report and
    `dispatch_gray_zone_deep_classify` therefore always agree on what's
    genuinely still outstanding, and the count returned here visibly shrinks
    as domains get worked through rather than staying pinned at the
    original backlog size forever.

    `scan_limit` bounds the underlying `domain_tracking` table scan (mirrors
    the 5000-row scan `reevaluate_pending_domains` already runs for its own
    promote pass) -- raise it if the real table is larger than that and a
    caller suspects gray-zone rows are being missed past the scan window.
    `limit` (default None = no cap) only trims the RETURNED list after the
    full scan, e.g. for a "give me 10 examples" sanity check.
    """
    from app.core.cassandra import get_cassandra_session

    session = get_cassandra_session()
    rows = _gray_zone_rows(session, scan_limit)
    findings = [
        {
            "domain": domain,
            "content_relevance": score,
            "pending_url": meta.get("pending_url") or meta.get("content_relevance_url") or "",
        }
        for domain, meta, score in rows
    ]
    return findings[:limit] if limit is not None else findings


def dispatch_gray_zone_deep_classify(
    *, limit: int = 5, dry_run: bool = True, scan_limit: int = 5000
) -> dict[str, object]:
    """Dispatch up to `limit` gray-zone domains to the real `deep_classify_domain` Celery task, on the same `scrape` queue and via the same `send_task` call site `_classify_and_store_domain` already uses for its own escalations -- never runs the crawl itself, never touches more than `limit` domains per call.

    `limit` defaults to 5, deliberately small: unlike the read-mostly scans
    in `service_reconciliation.py`, every domain dispatched here fires a
    REAL up-to-`FRONTIER_DEEP_CLASSIFY_MAX_PAGES`-page crawl once a worker
    picks it up -- unbounded batching here is exactly the shape of the
    2026-08-2x resource-contention incident (a large batch of
    `classify_pending_domains` chunks dispatched at once saturated the
    concurrency=4 scrape worker pool and starved unrelated admin/routine
    tasks). A coordinating caller runs this repeatedly with a small `limit`
    over time, exactly like `classify_pending_domains(limit=N)` and
    `reevaluate_pending_domains(limit=N)` are already run.

    `dry_run` defaults to True, mirroring `classify_pending_domains`'s own
    default: reports which domains WOULD be dispatched (and their would-be
    seed URL) without writing to Cassandra or calling `send_task`, so a
    caller can inspect the exact batch a real run would touch first.

    When `dry_run=False`, each dispatched domain's metadata is updated
    BEFORE the Celery call with `deep_classify_queued="true"` -- identical
    to the bookkeeping `_classify_and_store_domain` writes for its own
    shallow-sample escalations. `_gray_zone_rows` excludes any domain
    already carrying that flag (see its own docstring), so a repeat call
    naturally advances to a different slice of the backlog rather than
    re-dispatching the same handful forever, and a domain already queued --
    by this function or by an ordinary `classify_pending_domains`
    escalation -- is never double-dispatched. (2026-08-26: this used to
    also write `"frontier_status": "pending"` into metadata, but
    `frontier_status` is a real Cassandra COLUMN this function only ever
    updates via `UPDATE_METADATA` -- metadata-only -- so that write was
    silently inert and has been removed; `deep_classify_queued` is the one
    flag that actually does the exclusion work.)

    `seed_url` for each dispatch is the domain's own `pending_url` if one
    was recorded, else the page that produced its shallow verdict
    (`content_relevance_url`), else a bare `https://{domain}` guess --
    same fallback order `classify_pending_domains` itself uses for `url`.
    """
    from app.celery_app import celery_app as _celery_app
    from app.core.cassandra import get_cassandra_session
    from app.core.config import FRONTIER_DEEP_CLASSIFY_MAX_PAGES
    from app.core.statements import DomainTrackingStmts

    session = get_cassandra_session()
    candidates = _gray_zone_rows(session, scan_limit)
    batch = candidates[:limit]

    dispatched: list[dict[str, object]] = []
    for domain, meta, score in batch:
        seed_url = (
            meta.get("pending_url") or meta.get("content_relevance_url") or f"https://{domain}"
        )
        if not dry_run:
            new_meta = {**meta, "deep_classify_queued": "true"}
            session.execute(DomainTrackingStmts.UPDATE_METADATA, (new_meta, domain))
            _celery_app.send_task(
                "app.tasks.crawler.deep_classify_domain",
                kwargs={
                    "domain": domain,
                    "seed_url": seed_url,
                    "max_pages": FRONTIER_DEEP_CLASSIFY_MAX_PAGES,
                },
                queue="scrape",
            )
        dispatched.append({"domain": domain, "content_relevance": score, "seed_url": seed_url})

    return {
        "dry_run": dry_run,
        "dispatched": dispatched,
        "dispatched_count": len(dispatched),
        "remaining_candidates": max(len(candidates) - len(batch), 0),
    }
