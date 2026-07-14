from __future__ import annotations

import logging
from dataclasses import asdict

from robyn import Request, Response

from app.core import serialization
from app.core.http_errors import json_error_response
from app.modules.admin.auth import require_admin_wallet, verified_admin_wallet
from app.modules.admin.schemas import (
    ArticlePatchRequest,
    ClassifierFeedbackCreate,
    DomainSetRequest,
    EditorialBriefCreate,
    GatekeeperAnchorCreate,
    OfficialChannelCreate,
    ScraperRunRequest,
    ServiceMergeRequest,
    SourceUpsertRequest,
)
from app.modules.admin.stores.cassandra import AdminCassandraStore

logger = logging.getLogger(__name__)

# Cache keys for the domains list (one per status filter + the unfiltered view).
_DOMAIN_CACHE_KEYS = ("admin:domains:all", "admin:domains:pending",
                      "admin:domains:approved", "admin:domains:dead_end")


def _invalidate_domains_cache() -> None:
    from app.core.cache import invalidate

    invalidate(*_DOMAIN_CACHE_KEYS)


def register_admin_routes(app) -> None:
    store = AdminCassandraStore()

    @app.get("/api/v1/admin/analytics")
    async def admin_analytics(request: Request) -> Response | dict:
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

    @app.patch("/api/v1/admin/articles/:article_id")
    async def admin_patch_article(request: Request) -> Response:
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

    @app.delete("/api/v1/admin/articles/:article_id")
    async def admin_delete_article(request: Request) -> Response:
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

    @app.get("/api/v1/admin/articles/:article_id/versions")
    async def admin_article_versions(request: Request) -> Response:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        article_id = request.path_params.get("article_id", "")
        import asyncio

        versions = await asyncio.to_thread(store.list_versions, article_id)
        return {"article_id": article_id, "versions": versions}

    @app.get("/api/v1/admin/briefs")
    async def admin_list_briefs(request: Request) -> dict:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        import asyncio

        items = await asyncio.to_thread(store.list_briefs)
        return {"items": items}

    @app.post("/api/v1/admin/briefs")
    async def admin_create_brief(request: Request) -> Response:
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

    @app.get("/api/v1/admin/briefs/:brief_id")
    async def admin_get_brief(request: Request) -> Response:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        brief_id = request.path_params.get("brief_id", "")
        import asyncio

        item = await asyncio.to_thread(store.get_brief, brief_id)
        if item is None:
            return json_error_response(404, "not_found", "Brief not found")
        return item

    @app.post("/api/v1/admin/briefs/:brief_id/assign-now")
    async def admin_assign_brief_now(request: Request) -> Response:
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

    @app.get("/api/v1/admin/official-channels")
    async def admin_list_official_channels(request: Request) -> Response:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        kind = request.query_params.get("kind", "") or None
        import asyncio

        items = await asyncio.to_thread(store.list_official_channels, kind=kind)
        return {"items": items}

    @app.post("/api/v1/admin/official-channels")
    async def admin_add_official_channel(request: Request) -> Response:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        try:
            payload = serialization.decode(request.body, OfficialChannelCreate)
        except Exception as exc:
            return json_error_response(400, "invalid_request", str(exc))
        wallet = verified_admin_wallet(request)
        import asyncio

        return await asyncio.to_thread(
            store.upsert_official_channel,
            kind=payload.kind,
            channel_id=payload.channel_id.strip(),
            label=payload.label,
            added_by=wallet,
        )

    @app.delete("/api/v1/admin/official-channels/:kind/:channel_id")
    async def admin_delete_official_channel(request: Request) -> Response:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        kind = request.path_params.get("kind", "")
        channel_id = request.path_params.get("channel_id", "")
        if not kind or not channel_id:
            return json_error_response(400, "invalid_request", "kind and channel_id required")
        import asyncio

        await asyncio.to_thread(store.delete_official_channel, kind=kind, channel_id=channel_id)
        return {"deleted": True, "kind": kind, "channel_id": channel_id}

    @app.get("/api/v1/admin/classifier-reviews")
    async def admin_list_classifier_reviews(request: Request) -> Response:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        import asyncio

        # Offload the (blocking) Cassandra work to a thread so it doesn't stall
        # Robyn's event loop — lets other dashboard tabs load concurrently.
        items = await asyncio.to_thread(store.list_classifier_reviews)
        return {"items": items}

    @app.get("/api/v1/admin/publish-queue")
    async def admin_list_publish_queue(request: Request) -> Response | dict:
        """Queue rows with status + last drain/compose decision (last_reason) —
        the persisted answer to "why was this row skipped/held/resolved" that
        previously vanished with the Celery task return."""
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        import asyncio

        limit_param = request.query_params.get("limit", "")
        limit = max(1, min(int(limit_param) if limit_param.isdigit() else 200, 1000))
        items = await asyncio.to_thread(store.list_publish_queue, limit=limit)
        return {"items": items}

    @app.get("/api/v1/admin/publish-queue/:queue_id/breakdown")
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

    @app.get("/api/v1/admin/training-stats")
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
        return await asyncio.to_thread(
            cached_json, "admin:training_stats", 30, store.training_stats
        )

    @app.post("/api/v1/admin/retrain")
    async def admin_retrain(request: Request) -> Response:
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

    @app.post("/api/v1/admin/classifier-feedback")
    async def admin_classifier_feedback(request: Request) -> Response:
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

    @app.get("/api/v1/admin/gatekeeper/anchors")
    async def admin_list_gatekeeper_anchors(request: Request) -> Response:
        """Validation anchor set: count + list (for the X/40 progress view)."""
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        import asyncio

        return await asyncio.to_thread(store.list_gatekeeper_anchors)

    @app.post("/api/v1/admin/gatekeeper/anchor")
    async def admin_add_gatekeeper_anchor(request: Request) -> Response:
        """Tag an already-published article into the anchor set (curate diverse
        anchors without waiting for the review queue)."""
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

    @app.post("/api/v1/admin/gatekeeper/validate")
    async def admin_run_gatekeeper_validation(request: Request) -> Response:
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

    @app.get("/api/v1/admin/gatekeeper/validation-report")
    async def admin_gatekeeper_validation_report(request: Request) -> Response:
        """Latest annotator-validation report (trusted types + precision/recall)."""
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        import asyncio

        report = await asyncio.to_thread(store.get_gatekeeper_validation_report)
        return report if report is not None else {"report": None}

    @app.post("/api/v1/admin/sources")
    async def admin_upsert_source(request: Request) -> Response:
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

    @app.post("/api/v1/admin/sources/merge")
    async def admin_merge_services(request: Request) -> Response:
        """Fold services into one (multi-domain entities like algorand.co +
        algorand.com): sources move to the target, domains re-point, merged
        services are disabled. The target's next weekly poll aggregates across
        all of its domains."""
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        try:
            payload = serialization.decode(request.body, ServiceMergeRequest)
        except Exception as exc:
            return json_error_response(400, "invalid_request", str(exc))
        import asyncio

        from app.modules.registry.sources import merge_services

        result = await asyncio.to_thread(
            merge_services,
            target_service_id=payload.target_service_id,
            source_service_ids=payload.source_service_ids,
        )
        return result

    @app.get("/api/v1/admin/sources/:service_id/sources")
    async def admin_list_service_sources(request: Request) -> Response:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        service_id = request.path_params.get("service_id", "")
        if not service_id:
            return json_error_response(400, "invalid_request", "service_id required")
        import asyncio

        from app.modules.registry.sources import list_sources

        items = await asyncio.to_thread(list_sources, service_id)
        return {"service_id": service_id, "items": items}

    @app.delete("/api/v1/admin/sources/:service_id")
    async def admin_delete_source(request: Request) -> Response:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        service_id = request.path_params.get("service_id", "")
        if not service_id:
            return json_error_response(400, "invalid_request", "service_id required")
        from app.modules.registry.repository import get_service_registry_repository

        get_service_registry_repository().delete(service_id)
        return {"deleted": True, "service_id": service_id}

    @app.get("/api/v1/admin/scrapers")
    async def admin_list_scrapers(request: Request) -> Response:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        from dataclasses import asdict as dc_asdict

        from app.modules.admin.scrapers import SCRAPER_ACTIONS

        return {"items": [dc_asdict(a) for a in SCRAPER_ACTIONS.values()]}

    @app.post("/api/v1/admin/scrapers/run")
    async def admin_run_scraper(request: Request) -> Response:
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

    @app.get("/api/v1/admin/celery")
    async def admin_celery_overview(request: Request) -> Response:
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

    @app.post("/api/v1/admin/articles/reset")
    async def admin_reset_articles(request: Request) -> Response:
        """Beta convenience: wipe all article/publish state so the pipeline
        starts fresh. Keeps sources, classifier feedback and pending reviews."""
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

    @app.post("/api/v1/admin/classifier-reviews/clear")
    async def admin_clear_classifier_reviews(request: Request) -> Response:
        """Discard all pending review items without recording any feedback
        (stored classifier_feedback labels are untouched)."""
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

    @app.get("/api/v1/admin/domains")
    async def admin_list_domains(request: Request) -> Response:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        from app.core.cache import cached_json
        from app.core.cassandra import get_cassandra_session

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

        def _compute_full_list() -> list[dict]:
            """The metadata scan+sort — one query, cheap. Cached under a FIXED
            key (no page/page_size in it) so the existing exact-match
            _invalidate_domains_cache() still busts it correctly after a
            domain is set/cleared; pagination and the per-domain page-count
            queries happen fresh on every request, outside this cache, since
            they're now scoped to a single page instead of the whole ~500-row
            set and are cheap enough not to need caching."""
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
                items.append(
                    {
                        "domain": row.domain,
                        "last_crawled_at": (
                            row.last_crawled_at.isoformat() if row.last_crawled_at else None
                        ),
                        "relevance_score": float(row.relevance_score or 0),
                        "category": row.category or "",
                        "is_relevant": is_relevant,
                        "quality": meta.get("quality", ""),
                        "category_admin": meta.get("category_admin", ""),
                        "frontier_status": (
                            getattr(row, "frontier_status", None)
                            or (
                                "dead_end"
                                if not is_relevant
                                else meta.get("frontier_status", "approved")
                            )
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
                    }
                )
            # Assist the reviewer: surface the most relevant pending domains first
            # (highest content score). Unscored (None) sort last.
            items.sort(key=lambda it: it.get("content_relevance") or -1.0, reverse=True)
            order = {"pending": 0, "dead_end": 1, "approved": 2}
            items.sort(key=lambda d: (order.get(d["frontier_status"], 2), -(d["relevance_score"])))
            return items

        def _compute() -> dict:
            # The full scan+sort is the part worth a short cache (smooths tab
            # switches/nearby page loads) — cached under the ORIGINAL fixed key
            # (status only, no page in it) so the existing exact-match
            # _invalidate_domains_cache() still works unchanged after a domain
            # is set/cleared. Pagination and the per-domain page-count queries
            # below run fresh every request, uncached — cheap enough at
            # page-size scale (<=100) not to need it (the real cost was ~500
            # concurrent COUNT(*) calls across the WHOLE set, now scoped to a
            # single page; owner feedback 2026-07-12).
            items = cached_json(f"admin:domains:{status or 'all'}", 15, _compute_full_list)
            session = get_cassandra_session()
            total = len(items)
            page_items = items[page * page_size : (page + 1) * page_size]

            # Pages harvested per domain: one single-partition COUNT each, fired
            # concurrently. Scoped to just this page (was ALL ~500 items before
            # pagination existed — the actual source of the page being slow to
            # load, not just the row count itself; owner feedback 2026-07-12).
            from app.core.statements import CrawledPageStmts

            count_futures = {
                item["domain"]: session.execute_async(
                    CrawledPageStmts.COUNT_BY_DOMAIN, (item["domain"],)
                )
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

                from app.core.cache import _client as _redis_client

                day = datetime.now(tz=UTC).strftime("%Y-%m-%d")
                auto_today = sorted(
                    _redis_client().smembers(f"algorand:frontier:autoapproved:{day}")
                )
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

        # to_thread keeps the recompute (and its blocking Cassandra calls) off
        # the event loop.
        import asyncio

        return await asyncio.to_thread(_compute)

    @app.get("/api/v1/admin/tool-suggestions")
    async def admin_list_tool_suggestions(request: Request) -> Response:
        """Capabilities the writer model wished it had (via the suggest_tool tool),
        newest first — input for which tools to build next."""
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied

        def _compute() -> dict:
            from app.core.cassandra import get_cassandra_session
            from app.core.statements import ToolInsightStmts

            session = get_cassandra_session()
            rows = session.execute(ToolInsightStmts.LIST_SUGGESTIONS, ("all",))
            items = [
                {
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "capability": r.capability or "",
                    "reason": r.reason or "",
                    "service_id": r.service_id or "",
                    "source_url": r.source_url or "",
                    "model": r.model or "",
                }
                for r in rows
            ]
            return {"items": items}

        import asyncio

        return await asyncio.to_thread(_compute)

    @app.get("/api/v1/admin/compose-feedback")
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

    @app.get("/api/v1/admin/compose-sessions")
    async def admin_list_compose_sessions(request: Request) -> Response:
        """Recent article-compose sessions, newest first — status/timing only.
        Polled every few seconds by the admin UI for live progress, so this is
        deliberately summary-only (no messages/final_output, which can be up to
        ~140KB per row); fetch a transcript on demand via GET .../:session_id."""
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied

        def _compute() -> dict:
            from app.core.cassandra import get_cassandra_session
            from app.core.statements import ToolInsightStmts

            session = get_cassandra_session()
            rows = session.execute(ToolInsightStmts.LIST_COMPOSE_SESSIONS_SUMMARY, ("all",))
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
        return await asyncio.to_thread(cached_json, "admin:compose-sessions", 5, _compute)

    @app.get("/api/v1/admin/compose-sessions/:session_id")
    async def admin_get_compose_session(request: Request) -> Response:
        """Full transcript (messages + final_output) for one compose session —
        fetched on demand when the admin expands a session, not on every poll."""
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

    @app.post("/api/v1/admin/domains/set")
    async def admin_set_domain(request: Request) -> Response:
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        try:
            payload = serialization.decode(request.body, DomainSetRequest)
        except Exception as exc:
            return json_error_response(400, "invalid_request", str(exc))
        wallet = verified_admin_wallet(request)

        def _compute() -> dict:
            from app.core.cassandra import get_cassandra_session

            session = get_cassandra_session()
            meta, pending_url = store._write_domain_relevance(
                payload.domain, is_relevant=payload.is_relevant
            )
            from datetime import UTC, datetime

            now = datetime.now(tz=UTC)
            enqueued = False
            if payload.is_relevant and pending_url:
                # Approving a held domain starts its exploration right away.
                import uuid as uuid_mod

                queue_id = uuid_mod.uuid4()
                # High priority so a newly approved domain starts crawling promptly
                # (front of the frontier queue) — kicks off the initial harvest;
                # matches CRAWL_INITIAL_HARVEST_PRIORITY on the worker side.
                seed_priority = 50
                from app.core.statements import UrlQueueStmts

                session.execute(
                    UrlQueueStmts.INSERT,
                    (queue_id, pending_url, "frontier_approval", seed_priority, now, "pending", {}),
                )
                session.execute(
                    UrlQueueStmts.INSERT_BY_URL,
                    (pending_url, queue_id, now, "pending"),
                )
                session.execute(
                    UrlQueueStmts.INSERT_PENDING,
                    ("pending", seed_priority, now, queue_id, pending_url, "frontier_approval"),
                )
                enqueued = True
            source_created = False
            if payload.is_relevant and payload.as_seed:
                # Domain-centric model: an approved domain becomes a monitored
                # source so its content is reported on going forward — we don't
                # re-judge individual pages for relevance. If another service
                # already OWNS this domain (admin merge), attach there instead
                # of spawning a parallel service.
                from app.modules.registry.sources import add_web_source, service_for_domain

                scrape_url = pending_url or f"https://{payload.domain}"
                owner = service_for_domain(payload.domain)
                service_id = owner or payload.domain.replace(".", "-").lower()
                from app.core.statements import ServiceRegistryStmts

                existing = session.execute(
                    ServiceRegistryStmts.GET_ID, (service_id,)
                ).one()
                if existing is None:
                    session.execute(
                        ServiceRegistryStmts.UPSERT,
                        (
                            service_id,
                            payload.domain,
                            "domain",
                            payload.domain,
                            scrape_url,
                            True,
                            now,
                            "domain",
                        ),
                    )
                    source_created = True
                if not owner:
                    add_web_source(service_id, domain=payload.domain, url=scrape_url)
                # Event-driven: kick the crawl + scrape now so an approved domain is
                # explored immediately instead of waiting for the (now hourly) beat.
                try:
                    from celery import Celery

                    from app.core.config import settings

                    app = Celery(broker=settings.celery_broker_url)
                    if enqueued:
                        app.send_task("app.tasks.crawler.drain_url_queue", queue="scrape")
                    app.send_task(
                        "app.tasks.scrape.fetch_source",
                        args=[service_id, scrape_url],
                        queue="scrape",
                    )
                except Exception:
                    logger.warning(
                        "failed to trigger crawl/scrape for approved domain %s",
                        payload.domain,
                        exc_info=True,
                    )
            elif payload.is_relevant and enqueued:
                # Frontier-only approval (as_seed=False): no service_registry
                # row, so there's nothing for fetch_source to operate on — just
                # drain the queue now instead of waiting for the crawl beat, so
                # the domain still gets explored promptly for this one pass.
                try:
                    from celery import Celery

                    from app.core.config import settings

                    app = Celery(broker=settings.celery_broker_url)
                    app.send_task("app.tasks.crawler.drain_url_queue", queue="scrape")
                except Exception:
                    logger.warning(
                        "failed to trigger frontier-only crawl for domain %s",
                        payload.domain,
                        exc_info=True,
                    )
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

        import asyncio

        result = await asyncio.to_thread(_compute)
        _invalidate_domains_cache()
        return result

    @app.post("/api/v1/admin/domains/clear")
    async def admin_clear_domains(request: Request) -> Response:
        """Forget the whole crawl frontier: every explored/pending/dead-end
        domain record. The blocklist (config) is unaffected."""
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        import asyncio

        from app.core.cassandra import get_cassandra_session

        await asyncio.to_thread(get_cassandra_session().execute, "TRUNCATE domain_tracking")
        _invalidate_domains_cache()
        return {"cleared": True}

    @app.post("/api/v1/admin/classifier-reviews/compose-next")
    async def admin_compose_next(request: Request) -> Response:
        """Force the pipeline to compose the highest-interest pending candidate
        now (instead of waiting for the next verdict/drain). The new proposal
        appears in the review queue once the worker finishes (a few seconds).

        The worker task this triggers (run_mistral_diff_check) silently no-ops
        if a review is already pending or the approved-feed backlog is paused
        for intake — from the admin's side that used to look identical to a
        genuine failure ("nothing happened"). Both gates are cheap reads, so
        check them here first and report the real reason instead of always
        claiming success."""
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
                    "message": "The approved-feed backlog is paused for new intake "
                    "until it drains.",
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

    @app.post("/api/v1/admin/classifier-reviews/recompose")
    async def admin_recompose_review(request: Request) -> Response:
        """Re-run composition on a pending review's source and replace it with a
        fresh proposal — lets an admin watch a bad article improve as the writer
        evolves. The new draft lands in the review queue once the worker finishes."""
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

    @app.post("/api/v1/admin/translations/backfill")
    async def admin_backfill_translations(request: Request) -> Response:
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

    @app.get("/api/v1/admin/investigations")
    async def admin_investigation_findings(request: Request) -> Response:
        """Evidence trail: tool calls the investigative agent made for a source
        URL (?url=...)."""
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
