"""Cross-service "has this service ever had a real published article" lookup.

Extracted from workers' `app.modules.newspaper.article_matching` (2026-08-26)
so `algorand_shared.to_compose_selection`'s `_artifact_pool` can call
`service_has_article` directly from either service -- specifically, so
backend's admin to-compose preview/selection/reset routes can compute the
new-service-vs-update pool split without a Celery round-trip into workers.

Workers' own `article_matching.py` re-exports `service_has_article` (and
`published_rows_for_service`) from here for its existing callers, and still
owns everything else in that module (edit-window / publish-mode resolution),
which depends on workers-only `article_store.get_article` and has no reason
to run from backend.
"""

from __future__ import annotations


def published_rows_for_service(sid: str) -> list:
    """Raw `articles` rows for this service_id, filtered to status='published' in Python (see ArticlesStmts.FIND_BY_SERVICE_ID's comment for why the filter isn't in the query itself). Shared by service_has_article/find_latest_service_article, which are both really asking the same underlying question at different granularity."""
    from app.core.cassandra import get_cassandra_session

    from algorand_shared.article_statements import ArticlesStmts

    rows = get_cassandra_session().execute(ArticlesStmts.FIND_BY_SERVICE_ID, (sid,))
    return [row for row in rows if row.status == "published"]


def service_has_article(service_id: str) -> bool:
    """Whether this service has EVER had a real published article. Queries `articles` directly (2026-08-24, replacing the article_match_keys "service_id" key-type lookup now that service_id lives on `articles` itself), filtered to status='published' to preserve the original "publish and edit paths only, never held/review drafts" semantics. Fails open (True) on store errors: the safe default is the normal update framing, not re-introducing a service we may already have covered."""
    sid = (service_id or "").strip().lower()
    if not sid:
        return True
    try:
        return bool(published_rows_for_service(sid))
    except Exception:
        return True
