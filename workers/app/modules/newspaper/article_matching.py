"""Resolve new-article-vs-edit publish mode, and look up a service's own coverage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from app.core import config


def edit_window_closes_at(*, from_time: datetime | None = None) -> datetime:
    """Return when the post-publish edit window closes, from now or a given start time."""
    hours = getattr(config, "ARTICLE_EDIT_WINDOW_HOURS", 24)
    start = from_time or datetime.now(tz=UTC)
    return start + timedelta(hours=hours)


def _published_rows_for_service(sid: str) -> list:
    """Raw `articles` rows for this service_id, filtered to status='published' in Python (see ArticlesStmts.FIND_BY_SERVICE_ID's comment for why the filter isn't in the query itself). Shared by service_has_article/find_latest_service_article, which are both really asking the same underlying question at different granularity."""
    from algorand_shared.article_statements import ArticlesStmts

    from app.core.cassandra import get_cassandra_session

    rows = get_cassandra_session().execute(ArticlesStmts.FIND_BY_SERVICE_ID, (sid,))
    return [row for row in rows if row.status == "published"]


def service_has_article(service_id: str) -> bool:
    """Whether this service has EVER had a real published article. Queries `articles` directly (2026-08-24, replacing the article_match_keys "service_id" key-type lookup now that service_id lives on `articles` itself), filtered to status='published' to preserve the original "publish and edit paths only, never held/review drafts" semantics. Fails open (True) on store errors: the safe default is the normal update framing, not re-introducing a service we may already have covered."""
    sid = (service_id or "").strip().lower()
    if not sid:
        return True
    try:
        return bool(_published_rows_for_service(sid))
    except Exception:
        return True


def find_latest_service_article(service_id: str) -> str | None:
    """The most recently published/edited article for this service, by articles.updated_at (falling back to published_at when never edited), or None. Ignores the edit window entirely — it answers "what did we last say about this service", not "is it still editable". 2026-08-24: queries `articles` directly instead of article_match_keys' "service_id" key-type rows, now that service_id lives on `articles` itself. Fails open (None) on store errors: the safe default is no comparison baseline, not a false duplicate block."""
    sid = (service_id or "").strip().lower()
    if not sid:
        return None
    try:
        rows = _published_rows_for_service(sid)
        best_id, best_recency = None, None
        for row in rows:
            recency = row.updated_at or row.published_at
            if best_recency is None or (recency and recency > best_recency):
                best_id, best_recency = str(row.article_id), recency
        return best_id
    except Exception:
        return None


def is_edit_window_open(article_id: str, *, now: datetime | None = None) -> bool:
    """Explicitly linked article is editable while within ARTICLE_EDIT_WINDOW_HOURS of publish."""
    from app.modules.newspaper.article_store import get_article

    try:
        article = get_article(article_id)
    except Exception:
        return False
    if article is None or not article.published_at_epoch:
        return False
    moment = now or datetime.now(tz=UTC)
    published = datetime.fromtimestamp(article.published_at_epoch, tz=UTC)
    return edit_window_closes_at(from_time=published) > moment


def resolve_publish_mode(
    *,
    requested_mode: str = "",
    requested_article_id: str = "",
) -> dict[str, Any]:
    """Decide new article vs edit follow-up.

    An explicitly requested edit (`requested_mode="edit"` + article id, e.g. an
    editorial-brief refresh) wins when the edit window is still open; every
    other candidate composes as a new article. 2026-08-24: dropped the
    crawl-based match-key follow-up lookup (domain/source_url/keyword/
    algo_address continuity) -- real prod data showed it fired legitimately
    twice ever, both over a month old by the time this was removed, against
    1,657 total candidates; today's much lower crawl volume and pervasive
    per-source cooldowns mean the scenario it existed for essentially doesn't
    happen anymore. Returns { publish_mode, linked_article_id?, edit_window_open? }.
    """
    if (
        requested_mode == "edit"
        and requested_article_id
        and is_edit_window_open(requested_article_id)
    ):
        return {
            "publish_mode": "edit",
            "linked_article_id": requested_article_id,
            "edit_window_open": True,
        }
    return {
        "publish_mode": "create",
        "linked_article_id": None,
        "edit_window_open": False,
    }
