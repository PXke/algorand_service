from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class EditorialBrief:
    brief_id: str
    title: str
    body_markdown: str
    keywords: str
    status: str
    refresh_every_days: int
    last_run_at: datetime | None
    linked_article_id: str


def get_brief(brief_id: str) -> EditorialBrief | None:
    from uuid import UUID

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import EditorialBriefStmts

    try:
        bid = UUID(brief_id)
    except ValueError:
        return None
    row = get_cassandra_session().execute(EditorialBriefStmts.GET, (bid,)).one()
    if row is None:
        return None
    return EditorialBrief(
        brief_id=str(row.brief_id),
        title=row.title or "",
        body_markdown=row.body_markdown or "",
        keywords=row.keywords or "",
        status=(row.status or "").strip().lower(),
        refresh_every_days=int(row.refresh_every_days or 0),
        last_run_at=row.last_run_at,
        linked_article_id=str(row.linked_article_id) if row.linked_article_id else "",
    )


def list_active_briefs(*, limit: int = 200) -> list[EditorialBrief]:
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import EditorialBriefStmts

    try:
        rows = get_cassandra_session().execute(EditorialBriefStmts.LIST, (limit,))
    except Exception:
        return []
    out = []
    for row in rows:
        status = (row.status or "").strip().lower()
        if status != "active":
            continue
        out.append(
            EditorialBrief(
                brief_id=str(row.brief_id),
                title=row.title or "",
                body_markdown=row.body_markdown or "",
                keywords=row.keywords or "",
                status=status,
                refresh_every_days=int(row.refresh_every_days or 0),
                last_run_at=row.last_run_at,
                linked_article_id=str(row.linked_article_id) if row.linked_article_id else "",
            )
        )
    return out


def mark_brief_run(*, brief_id: str, article_id: str = "") -> None:
    """Record that a brief just fired: bumps ``last_run_at`` always, and sets
    ``linked_article_id`` when a new article id is known (first assignment, or
    a compose that produced/held an article). A refresh that only re-enqueues
    (article id not known yet at enqueue time) still bumps last_run_at so the
    scheduler doesn't fire again before the next cadence period."""
    from uuid import UUID

    from app.core.cassandra import get_cassandra_session
    from app.core.statements import EditorialBriefStmts

    try:
        bid = UUID(brief_id)
    except ValueError:
        return
    now = datetime.now(tz=UTC)
    session = get_cassandra_session()
    if article_id:
        try:
            aid = UUID(article_id)
        except ValueError:
            session.execute(EditorialBriefStmts.UPDATE_LAST_RUN, (now, bid))
            return
        session.execute(EditorialBriefStmts.UPDATE_LINK, (now, aid, bid))
    else:
        session.execute(EditorialBriefStmts.UPDATE_LAST_RUN, (now, bid))


def _build_assignment_payload(brief: EditorialBrief) -> dict[str, Any]:
    return {
        "source_kind": "editorial_assignment",
        "brief_id": brief.brief_id,
        "page_title": brief.title,
        "page_text": brief.body_markdown,
        "keywords": brief.keywords,
        "is_first_snapshot": True,
        "diff": None,
        "txid": "",
        "round_num": 0,
    }


def assign_editorial_brief(brief_id: str) -> dict[str, Any]:
    """First-run: enqueue a brand-new article for this brief's topic. Relevance
    is forced to 1.0 — an editor already decided this is worth covering, so the
    content-relevance classifier shouldn't gate it (novelty/timeliness still
    apply normally: an editor-picked topic that duplicates something just
    published shouldn't rank artificially high)."""
    from app.core.config import WRITER_EDITORIAL_BRIEFS_ENABLED
    from app.modules.newspaper.publish_policy import PublishKind, PublishTopic
    from app.modules.newspaper.publish_queue_store import enqueue_publish
    from app.modules.newspaper.publish_score import compute_priority

    if not WRITER_EDITORIAL_BRIEFS_ENABLED:
        return {"status": "disabled", "brief_id": brief_id}

    brief = get_brief(brief_id)
    if brief is None:
        return {"status": "skipped", "reason": "brief_not_found", "brief_id": brief_id}

    scrape_url = f"editorial://brief/{brief.brief_id}"
    payload = _build_assignment_payload(brief)
    payload["publish_mode"] = "create"

    priority = compute_priority(
        topic=PublishTopic.EDITORIAL_ASSIGNMENT,
        publish_kind=PublishKind.EDITORIAL_ASSIGNMENT,
        page_text=brief.body_markdown,
        diff=None,
        source_kind="editorial_assignment",
        source_url=scrape_url,
        page_title=brief.title,
        relevance=1.0,
    ).total

    queue_id, created = enqueue_publish(
        service_id=f"editorial-brief:{brief.brief_id}",
        display_name=brief.title,
        scrape_url=scrape_url,
        publish_kind=PublishKind.EDITORIAL_ASSIGNMENT,
        topic=PublishTopic.EDITORIAL_ASSIGNMENT,
        priority=priority,
        dedupe_key=f"editorial-assignment:{brief.brief_id}:initial",
        payload=payload,
    )
    if created:
        # An assignment is a deliberate, one-off admin action — unlike the
        # passive scrape/mail pipeline, it should compose right away rather
        # than wait for the next hourly standard-queue beat.
        from app.modules.newspaper.tasks.queue_drain_tasks import drain_standard_publish_queue

        drain_standard_publish_queue.delay()
    return {
        "status": "enqueued" if created else "duplicate",
        "brief_id": brief.brief_id,
        "queue_id": queue_id,
    }


def refresh_editorial_brief(brief_id: str) -> dict[str, Any]:
    """Cadence refresh: re-enqueue as an in-place edit of the brief's existing
    article. Falls back to ``assign_editorial_brief`` if the brief has no
    linked article yet (e.g. an admin hits "publish now" before the first
    assignment ever landed)."""
    from app.core.config import WRITER_EDITORIAL_BRIEFS_ENABLED

    if not WRITER_EDITORIAL_BRIEFS_ENABLED:
        return {"status": "disabled", "brief_id": brief_id}

    brief = get_brief(brief_id)
    if brief is None:
        return {"status": "skipped", "reason": "brief_not_found", "brief_id": brief_id}
    if not brief.linked_article_id:
        return assign_editorial_brief(brief_id)

    from app.modules.newspaper.publish_policy import PublishKind, PublishTopic
    from app.modules.newspaper.publish_queue_store import enqueue_publish
    from app.modules.newspaper.publish_score import compute_priority

    scrape_url = f"editorial://brief/{brief.brief_id}"
    payload = _build_assignment_payload(brief)
    payload["publish_mode"] = "edit"
    payload["linked_article_id"] = brief.linked_article_id

    priority = compute_priority(
        topic=PublishTopic.EDITORIAL_ASSIGNMENT,
        publish_kind=PublishKind.EDITORIAL_ASSIGNMENT,
        page_text=brief.body_markdown,
        diff=None,
        source_kind="editorial_assignment",
        source_url=scrape_url,
        page_title=brief.title,
        relevance=1.0,
    ).total

    run_marker = datetime.now(tz=UTC).date().isoformat()
    queue_id, created = enqueue_publish(
        service_id=f"editorial-brief:{brief.brief_id}",
        display_name=brief.title,
        scrape_url=scrape_url,
        publish_kind=PublishKind.EDITORIAL_ASSIGNMENT,
        topic=PublishTopic.EDITORIAL_ASSIGNMENT,
        priority=priority,
        dedupe_key=f"editorial-assignment:{brief.brief_id}:{run_marker}",
        payload=payload,
    )
    # Bump last_run_at at enqueue time (not compose completion) — a failed
    # compose just delays the next attempt by one cadence period, which is
    # simpler than threading a completion callback back through the drain.
    mark_brief_run(brief_id=brief.brief_id)
    if created:
        from app.modules.newspaper.tasks.queue_drain_tasks import drain_standard_publish_queue

        drain_standard_publish_queue.delay()
    return {
        "status": "enqueued" if created else "duplicate",
        "brief_id": brief.brief_id,
        "queue_id": queue_id,
    }


def scan_editorial_brief_schedule() -> dict[str, Any]:
    """Safety-net beat: fire the first assignment for any active brief that
    hasn't produced an article yet, and refresh any brief whose cadence has
    elapsed. The immediate on-create trigger (admin API) covers the common
    case; this catches missed sends and drives the recurring refresh."""
    assigned = 0
    refreshed = 0
    now = datetime.now(tz=UTC)
    for brief in list_active_briefs():
        if not brief.linked_article_id:
            assign_editorial_brief(brief.brief_id)
            assigned += 1
            continue
        if brief.refresh_every_days <= 0:
            continue
        if brief.last_run_at is None:
            due = True
        else:
            last_run = brief.last_run_at
            if last_run.tzinfo is None:
                last_run = last_run.replace(tzinfo=UTC)
            due = (now - last_run).days >= brief.refresh_every_days
        if due:
            refresh_editorial_brief(brief.brief_id)
            refreshed += 1
    return {"status": "ok", "assigned": assigned, "refreshed": refreshed}
