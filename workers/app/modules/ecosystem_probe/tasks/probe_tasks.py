"""Celery tasks for the Algorand Open Registry: the periodic liveness re-check beat and the admin-triggered blurb-draft helper.

Registered as `app.tasks.ecosystem_probe.*` so this module's own beat can be
scheduled independently of every `app.tasks.newspaper.*`/`app.tasks.crawler.*`
glob in celery_app.py's task_routes -- both fall to the "default" queue,
which the worker already consumes (deploy/scripts/run_celery.sh
`-Q default,scrape,pipeline,chain,security`), same as x402_probe's own
unrouted tasks.
"""

from __future__ import annotations

import logging

from app.celery_app import celery_app
from app.core.config import ECOSYSTEM_PROBE_ENABLED
from app.core.redis_lock import single_flight
from app.modules.ecosystem_probe.blurb import draft_blurb
from app.modules.ecosystem_probe.service import run_liveness_sweep

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.ecosystem_probe.probe_registry_entries")
# A sweep can outrun its own tick (up to ECOSYSTEM_PROBE_MAX_ENTRIES
# sequential timeout-bounded fetches); overlapping ticks must no-op rather
# than probe the same entries twice. Lock TTL pinned to the celery-wide hard
# time limit (>= the soft limit, CLAUDE.md invariant 5); the beat entry sets
# `expires=` to the interval so a stale tick is dropped, not run late --
# same pairing as x402_probe's own probe_listed_endpoints.
@single_flight(lambda *_a, **_kw: "ecosystem_probe:sweep", ttl=celery_app.conf.task_time_limit)
def probe_registry_entries() -> dict[str, object]:
    """Run one liveness sweep over pending+approved registry entries. Re-checks ECOSYSTEM_PROBE_ENABLED so a manual trigger honours the flag too."""
    if not ECOSYSTEM_PROBE_ENABLED:
        return {"status": "skipped", "reason": "ecosystem_probe_disabled"}
    return run_liveness_sweep()


@celery_app.task(name="app.tasks.ecosystem_probe.draft_ecosystem_blurb")
def draft_ecosystem_blurb(slug: str, *, url: str, domain: str) -> dict[str, object]:
    """Admin-triggered, one-off: draft a grounded blurb suggestion for one registry entry and store it in `draft_description`. Never auto-publishes -- see blurb.py's own docstring. Never raises: a failure here is logged and reported in the return value, not surfaced to whoever fired the fire-and-forget Celery call."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import EcosystemStmts

    try:
        draft = draft_blurb(slug=slug, url=url, domain=domain)
    except Exception:
        logger.warning("ecosystem blurb draft failed for slug=%s", slug, exc_info=True)
        return {"status": "error", "slug": slug}
    if not draft:
        return {"status": "no_grounding", "slug": slug}
    get_cassandra_session().execute(EcosystemStmts.SET_DRAFT_DESCRIPTION, (draft, slug))
    return {"status": "ok", "slug": slug}
