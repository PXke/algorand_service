"""Weekly sweep of X (Twitter) recent-search results for tracked services.

Redesigned 2026-08-25 from a live per-compose writer tool call (real X-API
money on every article's on-demand query, results never persisted) into a
bounded weekly sweep, mirroring llm_diff_check.py's shape (a Celery-beat
poll over the same service registry).

Scope: every enabled entry in service_registry -- the same list
llm_diff_check.py polls weekly for content diffs (see
chain_tail/registry_cache.py's ``load_enabled_services``) -- one X search
per service, queried on its display_name. This turns X's real per-resource
billing from "unbounded, whatever free-text query the writer decides to
issue mid-compose" into a known, fixed weekly headcount --
config.X_SEARCH_WEEKLY_SWEEP_MAX_SERVICES caps it further as a defensive
ceiling, not a routine truncation (see that config's comment).

Results are stored one row per service_id (x_search_weekly, see
x_search_store.py), superseding the previous week's row -- a rolling cache
of "recent posts about this service", not a history.

DORMANT since 2026-08-28: search_x was reverted back to live per-compose
calls (owner decision -- a fixed tracked-service list left real stories
about untracked/newly-registered projects, e.g. Lumi Rogue, with nothing
to read; see config.X_SEARCH_ENABLED's comment for the full picture). This
module and its beat entry are no longer wired into celery_app.py's
schedule, so nothing calls run_x_search_weekly_sweep automatically any
more -- left in place, not deleted, in case the sweep design is ever
reinstated. A manual/admin trigger of the task still works.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.modules.chain_tail.registry_cache import (
    ServiceEntry,
    clear_registry_cache,
    load_enabled_services,
)

SearchFn = Callable[[str], dict[str, Any]]
StoreFn = Callable[..., None]


def _default_search(query: str) -> dict[str, Any]:
    from app.modules.ai.research_tools import _x_search_live

    return _x_search_live(query)


def _default_store(**kwargs: Any) -> None:  # noqa: ANN401 -- forwards save_snapshot's kwargs
    from app.modules.newspaper.x_search_store import save_snapshot

    save_snapshot(**kwargs)


def run_x_search_weekly_sweep(
    *,
    load_services: Callable[[], tuple[ServiceEntry, ...]] = load_enabled_services,
    clear_cache: Callable[[], None] = clear_registry_cache,
    search: SearchFn = _default_search,
    store: StoreFn = _default_store,
) -> dict[str, object]:
    """Sweep every tracked service's X activity once and persist it. See module docstring."""
    from app.core import config

    if not config.X_SEARCH_ENABLED or not config.X_BEARER_TOKEN:
        return {"status": "skipped", "reason": "x_search_not_configured", "swept": 0}

    clear_cache()
    entries = [e for e in load_services() if (e.display_name or e.service_id).strip()]
    cap = max(0, config.X_SEARCH_WEEKLY_SWEEP_MAX_SERVICES)
    truncated = len(entries) > cap
    entries = entries[:cap]

    swept = 0
    errors = 0
    for entry in entries:
        query = entry.display_name or entry.service_id
        try:
            result = search(query)
        except Exception as exc:
            result = {"error": str(exc)[:200], "posts": []}
        error = str(result.get("error") or "")
        posts = result.get("posts")
        store(
            service_id=entry.service_id,
            display_name=entry.display_name,
            query=query,
            posts=posts if isinstance(posts, list) else [],
            error=error,
        )
        if error:
            errors += 1
        else:
            swept += 1

    return {
        "status": "ok",
        "swept": swept,
        "errors": errors,
        "tracked": len(entries),
        "truncated": truncated,
    }
