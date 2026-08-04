"""HTTP routes for the admin dashboard."""

from __future__ import annotations

import logging
from dataclasses import asdict

from app.core import serialization
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.core.query_params import query_param
from app.modules.admin.auth import require_admin_wallet, verified_admin_wallet
from app.modules.admin.schemas import (
    ArticlePatchRequest,
    ClassifierFeedbackCreate,
    DomainSetRequest,
    EditorialBriefCreate,
    GatekeeperAnchorCreate,
    GlossaryUpsertRequest,
    ScraperRunRequest,
    ServiceMergeRequest,
    SourceUpsertRequest,
)
from app.modules.admin.stores.cassandra import AdminCassandraStore

logger = logging.getLogger(__name__)

# Stateless (no __init__), safe as a module-level singleton shared by every route.
store = AdminCassandraStore()

# Cache keys for the domains list (one per status filter + the unfiltered view).
_DOMAIN_CACHE_KEYS = (
    "admin:domains:all",
    "admin:domains:pending",
    "admin:domains:approved",
    "admin:domains:dead_end",
)


def _invalidate_domains_cache() -> None:
    from app.core.cache import invalidate

    invalidate(*_DOMAIN_CACHE_KEYS)


async def admin_analytics(request: Request) -> Response | dict:
    """Site-wide pageview/referrer analytics for the given day window, cached briefly."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    days_param = request.query_params.get("days", "")
    days = max(1, min(int(days_param) if days_param.isdigit() else 14, 90))

    def _compute() -> dict:
        from app.modules.seo.analytics_store import read_analytics

        return read_analytics(days=days)

    import asyncio

    from app.core.cache import cached_json

    # This aggregate does a full sequential pass over `days` day-partitions
    # plus several site-wide aggregates (~1s) — the original motivation for
    # running Robyn multi-process, but the handler itself was never offloaded.
    # 30s TTL: an admin reloading the tab a few times in a row shouldn't
    # re-run the whole thing every time.
    return await asyncio.to_thread(cached_json, f"admin:analytics:{days}", 30, _compute)


async def admin_patch_article(request: Request) -> Response:
    """Patch an article's title/summary/body as an admin edit."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")
    try:
        payload = serialization.decode(request.body or b"{}", ArticlePatchRequest)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))
    wallet = verified_admin_wallet(request)
    import asyncio

    updated = await asyncio.to_thread(
        store.update_article,
        article_id,
        title=payload.title,
        summary=payload.summary,
        body=payload.body,
        editor=f"admin:{wallet}",
    )
    if updated is None:
        return json_error_response(404, "not_found", "Article not found")
    return asdict(updated)


async def admin_delete_article(request: Request) -> Response:
    """Delete an article, optionally blocking its source domain too."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")
    block_source = (request.query_params.get("block_source", "") or "").strip().lower() in (
        "1",
        "true",
    )
    wallet = verified_admin_wallet(request)
    import asyncio

    source_url = ""
    if block_source:
        current = await asyncio.to_thread(store.get_article, article_id)
        source_url = (current.source_url or "") if current is not None else ""

    deleted = await asyncio.to_thread(store.delete_article, article_id)
    if not deleted:
        return json_error_response(404, "not_found", "Article not found")

    blocked = False
    if block_source and source_url:
        from app.modules.registry.sources import domain_from_url

        domain = domain_from_url(source_url)
        if domain:
            await asyncio.to_thread(
                store.reject_domain_source,
                domain=domain,
                wallet=wallet,
                source_url_hint=source_url,
            )
            blocked = True
    return {"deleted": True, "article_id": article_id, "source_blocked": blocked}


async def admin_list_briefs(request: Request) -> dict:
    """List editorial briefs."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    items = await asyncio.to_thread(store.list_briefs)
    return {"items": items}


async def admin_create_brief(request: Request) -> Response:
    """Create an editorial brief and eagerly kick off its assignment."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        payload = serialization.decode(request.body, EditorialBriefCreate)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))
    wallet = verified_admin_wallet(request)
    import asyncio

    item = await asyncio.to_thread(
        store.create_brief,
        title=payload.title,
        body_markdown=payload.body_markdown,
        keywords=payload.keywords,
        status=payload.status,
        wallet_address=wallet,
        refresh_every_days=payload.refresh_every_days,
        is_special_edition=payload.is_special_edition,
    )
    try:
        from celery import Celery

        from app.core.config import settings

        Celery(broker=settings.celery_broker_url).send_task(
            "app.tasks.newspaper.assign_editorial_brief",
            kwargs={"brief_id": item["brief_id"]},
            queue="pipeline",
        )
    except Exception:
        # best-effort — the hourly scheduler picks up unlinked briefs anyway
        logger.debug("failed to assign editorial brief eagerly", exc_info=True)
    return item


async def admin_assign_brief_now(request: Request) -> Response:
    """Queue a brief for (re)assignment now instead of waiting for its refresh schedule."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    brief_id = request.path_params.get("brief_id", "")
    import asyncio

    item = await asyncio.to_thread(store.get_brief, brief_id)
    if item is None:
        return json_error_response(404, "not_found", "Brief not found")
    # Already has an article -> refresh it in place; otherwise this is the
    # (first, or retried) initial assignment.
    task_name = (
        "app.tasks.newspaper.refresh_editorial_brief"
        if item.get("linked_article_id")
        else "app.tasks.newspaper.assign_editorial_brief"
    )
    try:
        from celery import Celery

        from app.core.config import settings

        Celery(broker=settings.celery_broker_url).send_task(
            task_name, kwargs={"brief_id": brief_id}, queue="pipeline"
        )
        return {"status": "queued", "brief_id": brief_id}
    except Exception as exc:
        return json_error_response(500, "assign_failed", str(exc))


async def admin_list_classifier_reviews(request: Request) -> Response:
    """List pending classifier review candidates."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    # Offload the (blocking) Cassandra work to a thread so it doesn't stall
    # Robyn's event loop — lets other dashboard tabs load concurrently.
    items = await asyncio.to_thread(store.list_classifier_reviews)
    return {"items": items}


async def admin_list_publish_queue(request: Request) -> Response | dict:
    """Queue rows with status + last drain/compose decision (last_reason) — the persisted answer to "why was this row skipped/held/resolved" that previously vanished with the Celery task return."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    limit_param = request.query_params.get("limit", "")
    limit = max(1, min(int(limit_param) if limit_param.isdigit() else 200, 1000))
    items = await asyncio.to_thread(store.list_publish_queue, limit=limit)
    return {"items": items}


async def admin_pending_feed_backlog(request: Request) -> Response | dict:
    """Approved articles waiting in pending_feed_queue for paced release (capped by PENDING_FEED_MAX_DEPTH) — distinct from the in-flight composing work publish-queue shows."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    items = await asyncio.to_thread(store.list_pending_feed_backlog)
    return {"items": items}


async def admin_publish_queue_breakdown(request: Request) -> Response | dict:
    """One row's enqueue-time priority_breakdown + content signals."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    queue_id = request.path_params.get("queue_id", "")
    detail = await asyncio.to_thread(store.publish_queue_breakdown, queue_id)
    if detail is None:
        return json_error_response(404, "not_found", "unknown queue_id")
    return detail


async def admin_bump_queue_priority(request: Request) -> Response | dict:
    """Pin a pending queue row to the front so the next drain composes it next — never touches the daily cap or pacing interval that gate when the drain runs at all."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    queue_id = request.path_params.get("queue_id", "")
    result = await asyncio.to_thread(store.bump_queue_priority, queue_id)
    if result is None:
        return json_error_response(404, "not_found", "unknown queue_id or not pending")
    return result


async def admin_dead_end_queue_row_domain(request: Request) -> Response | dict:
    """Permanently reject the source domain behind one publish_queue row, straight from the Queue tab — the one-click alternative to hunting the same domain down in the paginated Domains tab."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    queue_id = request.path_params.get("queue_id", "")
    wallet = verified_admin_wallet(request)
    result = await asyncio.to_thread(store.dead_end_queue_row_domain, queue_id, wallet=wallet)
    if result is None:
        return json_error_response(404, "not_found", "unknown queue_id or no resolvable domain")
    _invalidate_domains_cache()
    return result


async def admin_training_stats(request: Request) -> Response:
    """Labelled-data volume + balance + grader readiness for the Training tab."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    from app.core.cache import cached_json

    # Short TTL: scans up to 5000 rows + a concurrent detail batch; doesn't
    # need to be real-time. Invalidated on each feedback write below.
    # to_thread keeps the cache-miss recompute off the event loop.
    return await asyncio.to_thread(cached_json, "admin:training_stats", 30, store.training_stats)


def admin_retrain(request: Request) -> Response:
    """Trigger a retrain of the publish classifier + gatekeeper quality head now."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        from celery import Celery

        from app.core.config import settings

        client = Celery(broker=settings.celery_broker_url)
        client.send_task("app.tasks.crawler.retrain_publish_classifier", queue="scrape")
        # Separate queue: a BERT fine-tune is a much heavier CPU job than the
        # RandomForest retrain above, and gatekeeper tasks already route here.
        client.send_task("app.tasks.gatekeeper.train_quality_head", queue="pipeline")
        return {"status": "queued"}
    except Exception as exc:
        return json_error_response(500, "retrain_failed", str(exc))


async def admin_classifier_feedback(request: Request) -> Response:
    """Record a human correction to a classifier verdict and invalidate the training-stats cache."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        payload = serialization.decode(request.body, ClassifierFeedbackCreate)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))
    wallet = verified_admin_wallet(request)
    import asyncio

    result = await asyncio.to_thread(
        store.record_classifier_feedback,
        url=payload.url,
        text_sample=payload.text_sample,
        category=payload.category,
        predicted_category=payload.predicted_category,
        quality=payload.quality,
        predicted_publish=payload.predicted_publish,
        approved=payload.approved,
        admin_wallet=wallet,
        review_id=payload.review_id,
        article_id=payload.article_id,
        source_relevant=payload.source_relevant,
        categories=payload.categories,
        training_only=payload.training_only,
        corrected_scores=payload.corrected_scores,
        anchor=payload.anchor,
        factuality_fail=payload.factuality_fail,
        tone_fail=payload.tone_fail,
        error_types=payload.error_types,
    )
    # Labeling changes the Training-tab aggregate — drop its cache so the
    # next load reflects this decision immediately (don't wait for the TTL).
    from app.core.cache import invalidate

    invalidate("admin:training_stats")
    return result


async def admin_list_gatekeeper_anchors(request: Request) -> Response:
    """Validation anchor set: count + list (for the X/40 progress view)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    return await asyncio.to_thread(store.list_gatekeeper_anchors)


async def admin_add_gatekeeper_anchor(request: Request) -> Response:
    """Tag an already-published article into the anchor set (curate diverse anchors without waiting for the review queue)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        payload = serialization.decode(request.body, GatekeeperAnchorCreate)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))
    wallet = verified_admin_wallet(request)
    import asyncio

    try:
        anchor_id = await asyncio.to_thread(
            store.record_gatekeeper_anchor,
            article_id=payload.article_id,
            url="",
            source_text="",
            article_text="",  # store snapshots it from the article
            factuality_fail=payload.factuality_fail,
            tone_fail=payload.tone_fail,
            error_types=payload.error_types,
            admin_wallet=wallet,
        )
        return {"status": "ok", "anchor_id": anchor_id}
    except Exception as exc:
        return json_error_response(500, "anchor_failed", str(exc))


def admin_run_gatekeeper_validation(request: Request) -> Response:
    """Trigger the annotator-validation task (runs in a worker)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        from celery import Celery

        from app.core.config import settings

        Celery(broker=settings.celery_broker_url).send_task(
            "app.tasks.gatekeeper.run_annotator_validation", queue="pipeline"
        )
        return {"status": "queued"}
    except Exception as exc:
        return json_error_response(500, "validate_failed", str(exc))


async def admin_gatekeeper_validation_report(request: Request) -> Response:
    """Latest annotator-validation report (trusted types + precision/recall)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    report = await asyncio.to_thread(store.get_gatekeeper_validation_report)
    return report if report is not None else {"report": None}


def admin_upsert_source(request: Request) -> Response:
    """Create or update a service-registry entry and claim its domain as a web source."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        payload = serialization.decode(request.body, SourceUpsertRequest)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))
    from app.modules.registry.models import ServiceEntry
    from app.modules.registry.repository import get_service_registry_repository

    get_service_registry_repository().upsert(
        ServiceEntry(
            service_id=payload.service_id,
            display_name=payload.display_name,
            match_kind=payload.match_kind,
            match_value=payload.match_value,
            scrape_url=payload.scrape_url.strip(),
            enabled=payload.enabled,
            origin="admin",
        )
    )
    # Service layer: record the web source + claim the domain so future
    # discovery of the same registrable domain attaches here instead of
    # spawning a parallel service. Skip bsky.app profile URLs — every
    # monitored Bluesky account shares that one host, so claiming it would
    # just have each new account overwrite the last one's domain ownership.
    url = payload.scrape_url.strip()
    is_bluesky = "bsky.app/profile/" in url.lower()
    if url.lower().startswith(("http://", "https://")) and not is_bluesky:
        from app.modules.registry.sources import add_web_source, domain_from_url

        domain = domain_from_url(url)
        if domain:
            add_web_source(payload.service_id, domain=domain, url=url)
    return {"saved": True, "service_id": payload.service_id}


async def admin_merge_services(request: Request) -> Response:
    """Fold services into one (multi-domain entities like algorand.co + algorand.com): sources move to the target, domains re-point, merged services are disabled. The target's next weekly poll aggregates across all of its domains."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        payload = serialization.decode(request.body, ServiceMergeRequest)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))
    import asyncio

    from app.modules.registry.sources import merge_services

    return await asyncio.to_thread(
        merge_services,
        target_service_id=payload.target_service_id,
        source_service_ids=payload.source_service_ids,
    )


def admin_delete_source(request: Request) -> Response:
    """Delete a service-registry entry."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    service_id = request.path_params.get("service_id", "")
    if not service_id:
        return json_error_response(400, "invalid_request", "service_id required")
    from app.modules.registry.repository import get_service_registry_repository

    get_service_registry_repository().delete(service_id)
    return {"deleted": True, "service_id": service_id}


def admin_list_glossary(request: Request) -> Response:
    """List every glossary entry (draft and published) for the admin table."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    from app.modules.glossary.store import list_terms

    terms = list_terms()
    return {"items": [asdict(t) for t in terms]}


def admin_upsert_glossary_term(request: Request) -> Response:
    """Create or fully replace a glossary entry's own-language fields."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        payload = serialization.decode(request.body, GlossaryUpsertRequest)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))
    from app.modules.glossary.store import (
        STATUS_PUBLISHED,
        enqueue_glossary_term_translations,
        upsert_term,
    )

    wallet = verified_admin_wallet(request)
    term = upsert_term(
        slug=payload.slug,
        term=payload.term,
        definition=payload.definition,
        aliases=payload.aliases,
        status=payload.status,
        created_by=wallet or "",
    )
    if payload.status == STATUS_PUBLISHED:
        enqueue_glossary_term_translations(payload.slug)
    return asdict(term)


def admin_delete_glossary_term(request: Request) -> Response:
    """Delete a glossary entry."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    slug = request.path_params.get("slug", "")
    if not slug:
        return json_error_response(400, "invalid_request", "slug required")
    from app.modules.glossary.store import delete_term

    deleted = delete_term(slug)
    if not deleted:
        return json_error_response(404, "not_found", "Glossary entry not found")
    return {"deleted": True, "slug": slug}


def admin_list_scrapers(request: Request) -> Response:
    """List the available manual scraper actions."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    from dataclasses import asdict as dc_asdict

    from app.modules.admin.scrapers import SCRAPER_ACTIONS

    return {"items": [dc_asdict(a) for a in SCRAPER_ACTIONS.values()]}


def admin_run_scraper(request: Request) -> Response:
    """Trigger a manual scraper action by name."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        payload = serialization.decode(request.body, ScraperRunRequest)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))
    from app.modules.admin.scrapers import SCRAPER_ACTIONS, trigger_scraper

    if payload.action not in SCRAPER_ACTIONS:
        return json_error_response(400, "unknown_action", f"unknown action: {payload.action}")
    try:
        task_id = trigger_scraper(payload.action)
    except Exception as exc:
        return json_error_response(502, "broker_unavailable", str(exc))
    return {"queued": True, "action": payload.action, "task_id": task_id}


async def admin_celery_overview(request: Request) -> Response:
    """Broker/worker overview for the admin dashboard, cached briefly."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    from app.core.cache import cached_json
    from app.modules.admin.scrapers import celery_overview

    try:
        # Broker inspect is slow-ish; 10s cache smooths dashboard polling.
        return await asyncio.to_thread(cached_json, "admin:celery", 10, celery_overview)
    except Exception as exc:
        return json_error_response(502, "broker_unavailable", str(exc))


async def admin_reset_articles(request: Request) -> Response:
    """Beta convenience: wipe all article/publish state so the pipeline starts fresh. Keeps sources, classifier feedback and pending reviews."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    from app.core.cassandra import get_cassandra_session
    from app.core.typesense_client import clear_search_index

    tables = (
        "articles_by_id",
        "articles_feed",
        "article_versions",
        "article_match_keys",
        "article_match_keys_by_article",
        "publish_queue",
        "publish_queue_dedupe",
        "publish_queue_pending",
        "page_snapshots",
        "url_queue",
        "url_queue_by_url",
        "url_queue_pending",
        "service_events",
        # Writer introspection — orphaned once articles are wiped, so reset too.
        "tool_suggestions",
        "compose_feedback",
        "investigation_findings",
        "compose_sessions",
    )

    def _truncate_all() -> None:
        session = get_cassandra_session()
        for table in tables:
            session.execute(f"TRUNCATE {table}")

    import asyncio

    try:
        await asyncio.to_thread(_truncate_all)
    except Exception as exc:
        return json_error_response(500, "reset_failed", str(exc))
    typesense = clear_search_index()
    return {"reset": True, "tables": list(tables), "typesense": typesense}


async def admin_clear_classifier_reviews(request: Request) -> Response:
    """Discard all pending review items without recording any feedback (stored classifier_feedback labels are untouched)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    from app.core.cassandra import get_cassandra_session

    def _truncate() -> None:
        session = get_cassandra_session()
        session.execute("TRUNCATE classifier_review_pending")
        session.execute("TRUNCATE classifier_review_queue")

    import asyncio

    await asyncio.to_thread(_truncate)
    return {"cleared": True}


def _recency_epoch(iso: str) -> float:
    if not iso:
        return 0.0
    try:
        from datetime import datetime

        return datetime.fromisoformat(iso).timestamp()
    except ValueError:
        return 0.0


def _domain_sort_key(it: dict) -> tuple:
    order = {"pending": 0, "dead_end": 1, "approved": 2}
    content_rel = it.get("content_relevance")
    if content_rel is None:
        tier, tie_break = 0, -_recency_epoch(it.get("last_crawled_at") or "")
    else:
        tier, tie_break = 1, -content_rel
    return (order.get(it["frontier_status"], 2), tier, tie_break)


def _admin_domains_full_list(status: str) -> list[dict]:
    """The metadata scan+sort — one query, cheap. Cached under a FIXED key (no page/page_size in it) so the existing exact-match _invalidate_domains_cache() still busts it correctly after a domain is set/cleared; pagination and the per-domain page-count queries happen fresh on every request, outside this cache, since they're now scoped to a single page instead of the whole ~500-row set and are cheap enough not to need caching."""
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import DomainTrackingStmts

    session = get_cassandra_session()
    if status in ("pending", "approved", "dead_end"):
        # SAI-indexed filter — no full-table scan.
        rows = session.execute(DomainTrackingStmts.LIST_BY_STATUS, (status,))
    else:
        rows = session.execute(DomainTrackingStmts.LIST_BRIEF)
    items = []
    for row in rows:
        meta = dict(row.metadata or {})
        try:
            content_rel = (
                float(meta["content_relevance"]) if meta.get("content_relevance") else None
            )
        except (ValueError, TypeError):
            content_rel = None
        is_relevant = bool(row.is_relevant) if row.is_relevant is not None else True
        category = row.category or ""
        relevance_score = float(row.relevance_score or 0)
        # Nudge for the reviewer: the category classifier has almost no
        # training signal outside "news" (67% of its admin-corrected
        # samples are "news" corrections, vs 6 "service" — see
        # content_categorizer.py), so a decent-scoring domain tagged
        # news/generic is worth a second look before "Crawl Once" —
        # root-caused 2026-07-25 (algofile.io, gramo.io: real Algorand
        # products sitting mislabeled "news" and never registered as a
        # monitored service). Deliberately simple (score + category only) —
        # a domain-shape denylist sounded smart but would have hidden real
        # cases (e.g. an SDK's readthedocs page is worth a periodic diff-
        # watch same as any product). Advisory only, human judges the rest.
        possible_service = category in ("news", "generic", "") and relevance_score >= 3
        items.append(
            {
                "domain": row.domain,
                "last_crawled_at": (
                    row.last_crawled_at.isoformat() if row.last_crawled_at else None
                ),
                "relevance_score": relevance_score,
                "category": category,
                "possible_service": possible_service,
                "is_relevant": is_relevant,
                "quality": meta.get("quality", ""),
                "category_admin": meta.get("category_admin", ""),
                "frontier_status": (
                    getattr(row, "frontier_status", None)
                    or ("dead_end" if not is_relevant else meta.get("frontier_status", "approved"))
                ),
                # "auto_approved" when the frontier approved it without a
                # human (score-gated); "" for admin/legacy approvals.
                "frontier_status_source": meta.get("frontier_status_source", ""),
                "pending_url": meta.get("pending_url", ""),
                "link_text": meta.get("link_text", ""),
                "found_on": meta.get("found_on", ""),
                "preview_title": meta.get("preview_title", ""),
                "preview_description": meta.get("preview_description", ""),
                "preview_keywords": meta.get("preview_keywords", ""),
                # Content-based relevance (classify_pending_domains): real page
                # text scored 0-1. Used to sort the review queue, NOT to decide.
                "content_relevance": content_rel,
                # Why it scored that way (score_page()'s own reasons, e.g.
                # "known_domain:algorand.foundation", "keywords:4",
                # "reject_noise:2") — shown next to the score in the admin
                # UI instead of a bare unexplained number (owner feedback
                # 2026-07-12). Empty for domains scored before this existed.
                "content_relevance_reasons": meta.get("content_relevance_reasons", ""),
                # Full Site / Single Page reviewer nudge (suggest_full_site in
                # domain_tracker.py) — advisory only, never decides. None
                # (not False) when this domain predates the feature and hasn't
                # been through classify_pending_domains since.
                "suggested_full_site": (
                    meta["suggested_full_site"] == "true" if "suggested_full_site" in meta else None
                ),
                "same_domain_link_count": (
                    int(meta["same_domain_link_count"])
                    if meta.get("same_domain_link_count", "").isdigit()
                    else None
                ),
            }
        )
    # Assist the reviewer: within each status, an unscored domain (not
    # yet content-classified — i.e. a NEW arrival classify_pending_domains
    # hasn't reached) sorts first, newest last_crawled_at first, so a
    # fresh discovery is immediately visible instead of sinking under
    # everything the classifier already scored. A scored domain sorts
    # by content score, highest first. (Previously unscored sorted
    # LAST via `or -1.0`, which combined with the old LIMIT 500 above
    # meant new domains could go effectively invisible once the
    # pending pool grew past a page or two — owner report 2026-07-22.)
    items.sort(key=_domain_sort_key)
    return items


def _admin_domains_page(status: str, page: int, page_size: int) -> dict:
    """The paginated, per-page-enriched domains response for one status filter."""
    from app.core.cache import _client as _redis_client
    from app.core.cache import cached_json
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import CrawledPageStmts

    # The full scan+sort is the part worth a short cache (smooths tab
    # switches/nearby page loads) — cached under the ORIGINAL fixed key
    # (status only, no page in it) so the existing exact-match
    # _invalidate_domains_cache() still works unchanged after a domain
    # is set/cleared. Pagination and the per-domain page-count queries
    # below run fresh every request, uncached — cheap enough at
    # page-size scale (<=100) not to need it (the real cost was ~500
    # concurrent COUNT(*) calls across the WHOLE set, now scoped to a
    # single page; owner feedback 2026-07-12).
    items = cached_json(
        f"admin:domains:{status or 'all'}", 15, lambda: _admin_domains_full_list(status)
    )
    session = get_cassandra_session()
    total = len(items)
    page_items = items[page * page_size : (page + 1) * page_size]

    # Pages harvested per domain: one single-partition COUNT each, fired
    # concurrently. Scoped to just this page (was ALL ~500 items before
    # pagination existed — the actual source of the page being slow to
    # load, not just the row count itself; owner feedback 2026-07-12).
    count_futures = {
        item["domain"]: session.execute_async(CrawledPageStmts.COUNT_BY_DOMAIN, (item["domain"],))
        for item in page_items
        if item["domain"]
    }
    for item in page_items:
        future = count_futures.get(item["domain"])
        try:
            row = future.result().one() if future is not None else None
            item["pages_crawled"] = int(row.c) if row and row.c is not None else 0
        except Exception:
            item["pages_crawled"] = 0

    # Frontier auto-approve tally for today (UTC), read from the shared
    # Redis set the worker writes on each score-gated auto-approve. Robust
    # to domain_tracking metadata being overwritten by later recrawls.
    auto_today: list[str] = []
    try:
        from datetime import UTC, datetime

        day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        auto_today = sorted(_redis_client().smembers(f"algorand:frontier:autoapproved:{day}"))
    except Exception:
        auto_today = []
    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "auto_approved_today": len(auto_today),
        "auto_approved_domains": auto_today,
    }


async def admin_list_domains(request: Request) -> Response:
    """Paginated, sorted crawl-frontier domain list with per-domain page counts and today's auto-approve tally."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    status = (request.query_params.get("status", "") or "").strip().lower()
    try:
        page = max(0, int(request.query_params.get("page", "0") or "0"))
    except ValueError:
        page = 0
    try:
        page_size = int(request.query_params.get("page_size", "25") or "25")
    except ValueError:
        page_size = 25
    page_size = max(1, min(page_size, 100))

    # to_thread keeps the recompute (and its blocking Cassandra calls) off
    # the event loop.
    import asyncio

    return await asyncio.to_thread(_admin_domains_page, status, page, page_size)


async def admin_list_tool_suggestions(request: Request) -> Response:
    """Capabilities the writer model wished it had (via the suggest_tool tool), newest first — input for which tools to build next. Resolved suggestions (tools that have since shipped) are hidden by default so the list only shows genuine gaps instead of growing forever; pass ?include_resolved=true to see the full history."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    include_resolved = (request.query_params.get("include_resolved", "") or "").strip().lower() in (
        "1",
        "true",
    )

    import asyncio

    items = await asyncio.to_thread(store.list_tool_suggestions, include_resolved=include_resolved)
    return {"items": items}


async def admin_list_compose_feedback(request: Request) -> Response:
    """Writer-reported prompt/data/tool/pipeline issues (report_compose_issue)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    def _compute() -> dict:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        rows = session.execute(ToolInsightStmts.LIST_COMPOSE_FEEDBACK, ("all",))
        items = [
            {
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "category": r.category or "",
                "severity": r.severity or "",
                "summary": r.summary or "",
                "detail": r.detail or "",
                "related_tool": r.related_tool or "",
                "service_id": r.service_id or "",
                "source_url": r.source_url or "",
                "model": r.model or "",
            }
            for r in rows
        ]
        return {"items": items}

    import asyncio

    return await asyncio.to_thread(_compute)


async def admin_list_compose_sessions(request: Request) -> Response:
    """Recent article-compose sessions, newest first — status/timing only.

    Polled every few seconds by the admin UI for live progress, so this is
    deliberately summary-only (no messages/final_output, which can be up to
    ~140KB per row); fetch a transcript on demand via GET .../:session_id.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    # Keyset cursor: the created_at of the oldest row the client already has.
    # Robyn's QueryParams.get REQUIRES the default argument — omitting it is a
    # TypeError at request time, not import time, so it 500s only in prod.
    before = query_param(request.query_params.get("before", ""))
    try:
        limit = max(1, min(int(query_param(request.query_params.get("limit", "")) or 20), 100))
    except (TypeError, ValueError):
        limit = 20

    def _compute() -> dict:
        from datetime import datetime

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        if before:
            cursor = datetime.fromisoformat(before)
            rows = session.execute(
                ToolInsightStmts.LIST_COMPOSE_SESSIONS_SUMMARY_BEFORE,
                ("all", cursor, limit),
            )
        else:
            rows = session.execute(
                ToolInsightStmts.LIST_COMPOSE_SESSIONS_SUMMARY, ("all", limit)
            )
        items = [
            {
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "session_id": str(r.session_id),
                "service_id": r.service_id or "",
                "source_url": r.source_url or "",
                "model": r.model or "",
                "status": r.status or "",
                "rounds": r.rounds or 0,
                "tool_calls": r.tool_calls or 0,
                "duration_ms": r.duration_ms or 0,
                "prompt_tokens": r.prompt_tokens or 0,
                "completion_tokens": r.completion_tokens or 0,
                "total_tokens": r.total_tokens or 0,
            }
            for r in rows
        ]
        return {"items": items}

    import asyncio

    from app.core.cache import cached_json

    # Short TTL: this is polled every ~8s from possibly several open admin
    # tabs, so a few seconds of staleness collapses concurrent polls into
    # one Cassandra read instead of one per request.
    # Cursor and limit are part of the key — otherwise page 2 would be served
    # the cached page 1.
    key = f"admin:compose-sessions:{before or 'head'}:{limit}"
    return await asyncio.to_thread(cached_json, key, 5, _compute)


async def admin_get_compose_session(request: Request) -> Response:
    """Full transcript (messages + final_output) for one compose session — fetched on demand when the admin expands a session, not on every poll."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    session_id = request.path_params.get("session_id", "")
    created_at_raw = request.query_params.get("created_at", "")
    try:
        from datetime import datetime as _datetime
        from urllib.parse import unquote
        from uuid import UUID

        sid = UUID(session_id)
        created_at = _datetime.fromisoformat(unquote(created_at_raw))
    except ValueError:
        return json_error_response(400, "invalid_request", "bad session_id/created_at")

    def _compute() -> dict:
        import json as _json

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        row = session.execute(
            ToolInsightStmts.GET_COMPOSE_SESSION_DETAIL, ("all", created_at, sid)
        ).one()
        if row is None:
            return {"messages": [], "final_output": ""}
        try:
            msgs = _json.loads(row.messages) if row.messages else []
        except Exception:
            msgs = []
        return {"messages": msgs, "final_output": row.final_output or ""}

    import asyncio

    return await asyncio.to_thread(_compute)


def _seed_domain_crawl(
    session: object, domain: str, pending_url: str, *, single_page_only: bool, now: object
) -> tuple[bool, str]:
    """Enqueue a one-time crawl seed for a newly approved domain (front of the frontier queue). Returns (enqueued, seed_url).

    pending_url only exists when this domain came from an organic crawler
    discovery that stashed the triggering URL in metadata. A domain an admin
    adds and approves directly (no prior domain_tracking row) has no
    pending_url — fall back to the domain root so approval always seeds a
    crawl instead of silently enqueuing nothing (root-caused 2026-07-21: 71
    admin-approved domains sat at frontier_status=approved with zero crawled
    pages forever, dark-coin.com among them).
    """
    import uuid as uuid_mod

    from app.core.statements import UrlQueueStmts

    seed_url = pending_url or f"https://{domain}"
    queue_id = uuid_mod.uuid4()
    # High priority so a newly approved domain starts crawling promptly
    # (front of the frontier queue) — kicks off the initial harvest;
    # matches CRAWL_INITIAL_HARVEST_PRIORITY on the worker side.
    seed_priority = 50
    # single_page_only: tell the crawler not to follow this page's outbound
    # links — fetch exactly the one URL and stop, never spider the rest of
    # the site (see web_crawler.py's no_follow check).
    queue_metadata = {"no_follow_links": "true"} if single_page_only else {}
    session.execute(
        UrlQueueStmts.INSERT,
        (queue_id, seed_url, "frontier_approval", seed_priority, now, "pending", queue_metadata),
    )
    session.execute(UrlQueueStmts.INSERT_BY_URL, (seed_url, queue_id, now, "pending"))
    session.execute(
        UrlQueueStmts.INSERT_PENDING,
        ("pending", seed_priority, now, queue_id, seed_url, "frontier_approval"),
    )
    return True, seed_url


def _register_domain_as_service(
    session: object, domain: str, scrape_url: str, *, enqueued: bool, now: object
) -> bool:
    """Register an approved domain as a monitored service (or attach it to its existing owner if one already claims the domain) and kick off an immediate crawl+scrape. Returns whether a new service_registry row was created."""
    from app.core.statements import ServiceRegistryStmts
    from app.modules.registry.sources import add_web_source, service_for_domain

    # If another service already OWNS this domain (admin merge), attach
    # there instead of spawning a parallel service.
    owner = service_for_domain(domain)
    service_id = owner or domain.replace(".", "-").lower()
    existing = session.execute(ServiceRegistryStmts.GET_ID, (service_id,)).one()
    source_created = existing is None
    if existing is None:
        session.execute(
            ServiceRegistryStmts.UPSERT,
            (service_id, domain, "domain", domain, scrape_url, True, now, "domain"),
        )
    if not owner:
        add_web_source(service_id, domain=domain, url=scrape_url)
    # Event-driven: kick the crawl + scrape now so an approved domain is
    # explored immediately instead of waiting for the (now hourly) beat.
    try:
        from celery import Celery

        from app.core.config import settings

        celery_app = Celery(broker=settings.celery_broker_url)
        if enqueued:
            celery_app.send_task("app.tasks.crawler.drain_url_queue", queue="scrape")
        celery_app.send_task(
            "app.tasks.scrape.fetch_source", args=[service_id, scrape_url], queue="scrape"
        )
    except Exception:
        logger.warning(
            "failed to trigger crawl/scrape for approved domain %s", domain, exc_info=True
        )
    return source_created


def _trigger_frontier_only_crawl(domain: str) -> None:
    """Drain the URL queue now for a single-page approval (full_site=False) — there's no service_registry row for fetch_source to operate on, so this is the only way the domain still gets explored promptly for this pass."""
    try:
        from celery import Celery

        from app.core.config import settings

        Celery(broker=settings.celery_broker_url).send_task(
            "app.tasks.crawler.drain_url_queue", queue="scrape"
        )
    except Exception:
        logger.warning("failed to trigger frontier-only crawl for domain %s", domain, exc_info=True)


def _admin_set_domain_compute(payload: DomainSetRequest, wallet: str) -> dict:
    """Apply a domain approve/reject decision: seed its crawl, optionally register it as a monitored service, and log training feedback."""
    from datetime import UTC, datetime

    from app.core.cassandra import get_cassandra_session

    # Internal crawl-seeding behavior still keys on single_page_only (no-follow
    # links, frontier_status="reference") — only the PUBLIC decision collapsed
    # to one field. See DomainSetRequest.full_site.
    single_page_only = not payload.full_site
    session = get_cassandra_session()
    meta, pending_url = store._write_domain_relevance(
        payload.domain, is_relevant=payload.is_relevant, single_page_only=single_page_only
    )
    now = datetime.now(tz=UTC)

    enqueued = False
    seed_url = pending_url or f"https://{payload.domain}"
    if payload.is_relevant:
        # Approving a held domain starts its exploration right away.
        enqueued, seed_url = _seed_domain_crawl(
            session,
            payload.domain,
            pending_url,
            single_page_only=single_page_only,
            now=now,
        )

    source_created = False
    if payload.is_relevant and payload.full_site:
        # Domain-centric model: an approved domain becomes a monitored source
        # so its content is reported on going forward — we don't re-judge
        # individual pages for relevance.
        source_created = _register_domain_as_service(
            session, payload.domain, seed_url, enqueued=enqueued, now=now
        )
    elif payload.is_relevant and enqueued:
        # Single-page mode: no service_registry row, so there's nothing for
        # fetch_source to watch — but the crawl (and, once it lands, the
        # one-shot compose in scrape_from_queue_item) still needs kicking off.
        _trigger_frontier_only_crawl(payload.domain)

    # Close the learning loop: log this domain decision as classifier
    # feedback (preview text + approved flag) so the relevance model trains
    # on it and generalizes to similar new domains — not just this one.
    store._record_domain_relevance_feedback(
        domain=payload.domain,
        meta=meta,
        pending_url=pending_url,
        is_relevant=payload.is_relevant,
        wallet=wallet,
    )

    return {
        "saved": True,
        "domain": payload.domain,
        "is_relevant": payload.is_relevant,
        "exploration_started": enqueued,
        "source_created": source_created,
    }


async def admin_set_domain(request: Request) -> Response:
    """Approve or reject a crawl-frontier domain, seeding its crawl and optionally registering it as a monitored service."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        payload = serialization.decode(request.body, DomainSetRequest)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))
    wallet = verified_admin_wallet(request)
    # Normalize once, in place, so domain_tracking, service_registry, and
    # the seed-crawl URL all key on the SAME eTLD+1 the crawler's own
    # is_admin_approved_domain(domain_from_url(url)) check will derive —
    # a raw "www.example.com" written verbatim silently breaks the
    # admin-approved bypass for that domain (root-caused 2026-07-24,
    # urvote.ca). Only for is_relevant=True: a dead-end (reject) call
    # through this same endpoint must keep dead-ending the EXACT host
    # given, not collapse to the registrable domain and take out sibling
    # subdomains with it (matches _write_domain_relevance, which applies
    # the identical is_relevant gate).
    if payload.is_relevant:
        payload.domain = store._normalize_domain_input(payload.domain)

    import asyncio

    result = await asyncio.to_thread(_admin_set_domain_compute, payload, wallet)
    _invalidate_domains_cache()
    return result


async def admin_clear_domains(request: Request) -> Response:
    """Forget the whole crawl frontier: every explored/pending/dead-end domain record. The blocklist (config) is unaffected."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    from app.core.cassandra import get_cassandra_session

    await asyncio.to_thread(get_cassandra_session().execute, "TRUNCATE domain_tracking")
    _invalidate_domains_cache()
    return {"cleared": True}


async def admin_compose_next(request: Request) -> Response:
    """Force the pipeline to compose the highest-interest pending candidate now (instead of waiting for the next verdict/drain). The new proposal appears in the review queue once the worker finishes (a few seconds).

    The worker task this triggers (run_mistral_diff_check) silently no-ops
    if a review is already pending or the approved-feed backlog is paused
    for intake — from the admin's side that used to look identical to a
    genuine failure ("nothing happened"). Both gates are cheap reads, so
    check them here first and report the real reason instead of always
    claiming success.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import asyncio

    from app.core.cassandra import get_cassandra_session
    from app.core.config import settings
    from app.core.statements import ClassifierReviewStmts, PendingFeedStmts

    session = get_cassandra_session()
    pending_review = await asyncio.to_thread(
        session.execute, ClassifierReviewStmts.LIST_PENDING, ("pending", 1)
    )
    if pending_review.one() is not None:
        return {
            "triggered": False,
            "reason": "classifier_review_pending",
            "message": "A proposal is already waiting in the review queue — "
            "resolve it before pulling a new one.",
        }
    if settings.pause_intake_on_feed_backlog:
        pending_feed = await asyncio.to_thread(
            session.execute,
            PendingFeedStmts.PEEK_ID,
            (settings.news_feed_bucket,),
        )
        if pending_feed.one() is not None:
            return {
                "triggered": False,
                "reason": "approved_feed_pending_release",
                "message": "The approved-feed backlog is paused for new intake until it drains.",
            }

    def _fire_and_wait() -> dict:
        import contextlib

        from celery import Celery
        from celery.exceptions import TimeoutError as CeleryTimeoutError

        app_c = Celery(broker=settings.celery_broker_url, backend=settings.redis_result_url)
        diff_async = app_c.send_task(
            "app.tasks.newspaper.check_and_publish_mistral_on_diff", queue="pipeline"
        )
        drain_async = app_c.send_task(
            "app.tasks.newspaper.drain_standard_publish_queue", queue="pipeline"
        )
        # Both tasks return almost instantly when they find nothing to do
        # (Redis/Cassandra reads only) — real scraping/composing/publishing
        # takes far longer. A short wait doubles as "did it actually find
        # work?" without duplicating the tasks' own gating logic here.
        diff_result: dict | None = None
        drain_result: dict | None = None
        with contextlib.suppress(CeleryTimeoutError):
            diff_result = diff_async.get(timeout=4)
        with contextlib.suppress(CeleryTimeoutError):
            drain_result = drain_async.get(timeout=4)
        return {"diff": diff_result, "drain": drain_result}

    try:
        outcome = await asyncio.to_thread(_fire_and_wait)
    except Exception as exc:
        return json_error_response(502, "broker_unavailable", str(exc))

    diff_result = outcome["diff"] or {}
    drain_result = outcome["drain"] or {}
    cap_reached = drain_result.get("reason") == "standard_daily_cap_reached"
    all_cooling_down = diff_result.get("checked") == 0 and diff_result.get("throttled", 0) > 0
    if cap_reached and all_cooling_down:
        return {
            "triggered": False,
            "reason": "no_capacity",
            "message": "Every known source is still within its re-scrape cooldown, "
            "and today's publish cap is already reached — nothing to pull right now.",
        }
    if cap_reached:
        return {
            "triggered": False,
            "reason": "standard_daily_cap_reached",
            "message": "Today's publish cap is already reached — queued items won't "
            "release until tomorrow.",
        }
    if all_cooling_down:
        return {
            "triggered": False,
            "reason": "all_sources_on_cooldown",
            "message": "Every known source was checked within its re-scrape window — "
            "nothing fresh to pull right now.",
        }
    return {"triggered": True}


def admin_recompose_review(request: Request) -> Response:
    """Re-run composition on a pending review's source and replace it with a fresh proposal — lets an admin watch a bad article improve as the writer evolves. The new draft lands in the review queue once the worker finishes."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import json as _json

    try:
        body = _json.loads(request.body or "{}")
    except Exception:
        body = {}
    review_id = str(body.get("review_id", "")).strip()
    if not review_id:
        return json_error_response(400, "invalid_request", "review_id is required")
    try:
        from celery import Celery

        from app.core.config import settings

        Celery(broker=settings.celery_broker_url).send_task(
            "app.tasks.newspaper.recompose_review",
            args=[review_id],
            queue="pipeline",
        )
    except Exception as exc:
        return json_error_response(502, "broker_unavailable", str(exc))
    return {"triggered": True, "review_id": review_id}


def admin_backfill_translations(request: Request) -> Response:
    """Queue missing article translations (fa/ps/ru/…) for feed-visible stories."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    import json as _json

    try:
        body = _json.loads(request.body or "{}")
    except Exception:
        body = {}
    limit = body.get("limit", 500)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return json_error_response(400, "invalid_request", "limit must be an integer")
    limit = max(1, min(limit, 2000))
    try:
        from celery import Celery

        from app.core.config import settings

        Celery(broker=settings.celery_broker_url).send_task(
            "app.tasks.newspaper.backfill_article_translations",
            kwargs={"limit": limit},
            queue="pipeline",
        )
    except Exception as exc:
        return json_error_response(502, "broker_unavailable", str(exc))
    return {"triggered": True, "limit": limit}


async def admin_investigation_findings(request: Request) -> Response:
    """Evidence trail: tool calls the investigative agent made for a source URL (?url=...)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    from urllib.parse import unquote

    url = unquote(request.query_params.get("url", "") or "")
    if not url:
        return {"items": []}

    def _compute() -> dict:
        import json as _json

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import InvestigationStmts

        session = get_cassandra_session()
        try:
            rows = session.execute(InvestigationStmts.LIST, (url,))
        except Exception:
            return {"items": []}
        items = []
        for r in rows:
            try:
                result = _json.loads(r.result_json) if r.result_json else {}
            except Exception:
                result = {}
            items.append(
                {
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "tool": r.tool,
                    "arguments": r.arguments,
                    "result": result,
                }
            )
        return {"items": items}

    import asyncio

    return await asyncio.to_thread(_compute)


def register_admin_routes(app: Router) -> None:
    """Register all admin API endpoints on the given Robyn app."""
    app.get("/api/v1/admin/analytics")(admin_analytics)
    app.patch("/api/v1/admin/articles/:article_id")(admin_patch_article)
    app.delete("/api/v1/admin/articles/:article_id")(admin_delete_article)
    app.get("/api/v1/admin/briefs")(admin_list_briefs)
    app.post("/api/v1/admin/briefs")(admin_create_brief)
    app.post("/api/v1/admin/briefs/:brief_id/assign-now")(admin_assign_brief_now)
    app.get("/api/v1/admin/classifier-reviews")(admin_list_classifier_reviews)
    app.get("/api/v1/admin/publish-queue")(admin_list_publish_queue)
    app.get("/api/v1/admin/pending-feed-backlog")(admin_pending_feed_backlog)
    app.get("/api/v1/admin/publish-queue/:queue_id/breakdown")(admin_publish_queue_breakdown)
    app.post("/api/v1/admin/publish-queue/:queue_id/compose-next")(admin_bump_queue_priority)
    app.post("/api/v1/admin/publish-queue/:queue_id/dead-end")(admin_dead_end_queue_row_domain)
    app.get("/api/v1/admin/training-stats")(admin_training_stats)
    app.post("/api/v1/admin/retrain")(admin_retrain)
    app.post("/api/v1/admin/classifier-feedback")(admin_classifier_feedback)
    app.get("/api/v1/admin/gatekeeper/anchors")(admin_list_gatekeeper_anchors)
    app.post("/api/v1/admin/gatekeeper/anchor")(admin_add_gatekeeper_anchor)
    app.post("/api/v1/admin/gatekeeper/validate")(admin_run_gatekeeper_validation)
    app.get("/api/v1/admin/gatekeeper/validation-report")(admin_gatekeeper_validation_report)
    app.post("/api/v1/admin/sources")(admin_upsert_source)
    app.post("/api/v1/admin/sources/merge")(admin_merge_services)
    app.delete("/api/v1/admin/sources/:service_id")(admin_delete_source)
    app.get("/api/v1/admin/scrapers")(admin_list_scrapers)
    app.post("/api/v1/admin/scrapers/run")(admin_run_scraper)
    app.get("/api/v1/admin/celery")(admin_celery_overview)
    app.post("/api/v1/admin/articles/reset")(admin_reset_articles)
    app.post("/api/v1/admin/classifier-reviews/clear")(admin_clear_classifier_reviews)
    app.get("/api/v1/admin/domains")(admin_list_domains)
    app.get("/api/v1/admin/tool-suggestions")(admin_list_tool_suggestions)
    app.get("/api/v1/admin/compose-feedback")(admin_list_compose_feedback)
    app.get("/api/v1/admin/compose-sessions")(admin_list_compose_sessions)
    app.get("/api/v1/admin/compose-sessions/:session_id")(admin_get_compose_session)
    app.post("/api/v1/admin/domains/set")(admin_set_domain)
    app.post("/api/v1/admin/domains/clear")(admin_clear_domains)
    app.post("/api/v1/admin/classifier-reviews/compose-next")(admin_compose_next)
    app.post("/api/v1/admin/classifier-reviews/recompose")(admin_recompose_review)
    app.post("/api/v1/admin/translations/backfill")(admin_backfill_translations)
    app.get("/api/v1/admin/investigations")(admin_investigation_findings)
    app.get("/api/v1/admin/glossary")(admin_list_glossary)
    app.post("/api/v1/admin/glossary")(admin_upsert_glossary_term)
    app.delete("/api/v1/admin/glossary/:slug")(admin_delete_glossary_term)
