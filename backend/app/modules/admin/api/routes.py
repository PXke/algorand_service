from __future__ import annotations

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
    SourceUpsertRequest,
)
from app.modules.admin.stores.cassandra import AdminCassandraStore

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
        from app.modules.seo.analytics_store import read_analytics

        days_param = request.query_params.get("days", "")
        days = int(days_param) if days_param.isdigit() else 14
        return read_analytics(days=max(1, min(days, 90)))

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
        import asyncio

        deleted = await asyncio.to_thread(store.delete_article, article_id)
        if not deleted:
            return json_error_response(404, "not_found", "Article not found")
        return {"deleted": True, "article_id": article_id}

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
            pass  # best-effort — the hourly scheduler picks up unlinked briefs anyway
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
        """Trigger a retrain of the publish classifier + learned grader now."""
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        try:
            from celery import Celery

            from app.core.config import settings

            Celery(broker=settings.celery_broker_url).send_task(
                "app.tasks.crawler.retrain_publish_classifier", queue="scrape"
            )
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
        return {"saved": True, "service_id": payload.service_id}

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

        def _compute() -> dict:
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
                    }
                )
            # Assist the reviewer: surface the most relevant pending domains first
            # (highest content score). Unscored (None) sort last.
            items.sort(key=lambda it: it.get("content_relevance") or -1.0, reverse=True)
            # Pages harvested per domain: one single-partition COUNT each, fired
            # concurrently so the admin view stays responsive even with ~500 domains.
            from app.core.statements import CrawledPageStmts

            count_futures = {
                item["domain"]: session.execute_async(
                    CrawledPageStmts.COUNT_BY_DOMAIN, (item["domain"],)
                )
                for item in items
                if item["domain"]
            }
            for item in items:
                future = count_futures.get(item["domain"])
                try:
                    row = future.result().one() if future is not None else None
                    item["pages_crawled"] = int(row.c) if row and row.c is not None else 0
                except Exception:
                    item["pages_crawled"] = 0

            order = {"pending": 0, "dead_end": 1, "approved": 2}
            items.sort(key=lambda d: (order.get(d["frontier_status"], 2), -(d["relevance_score"])))

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
                "items": items,
                "auto_approved_today": len(auto_today),
                "auto_approved_domains": auto_today,
            }

        # ~500 concurrent COUNT(*) per load; a short cache smooths repeated views
        # and tab switches. Invalidated on domain set/clear below. to_thread keeps
        # the recompute (and its blocking COUNT waits) off the event loop.
        import asyncio

        return await asyncio.to_thread(
            cached_json, f"admin:domains:{status or 'all'}", 15, _compute
        )

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
            from uuid import UUID

            sid = UUID(session_id)
            created_at = _datetime.fromisoformat(created_at_raw)
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
            from app.core.statements import DomainTrackingStmts

            session = get_cassandra_session()
            row = session.execute(
                DomainTrackingStmts.GET_FOR_CORRECTION, (payload.domain,)
            ).one()
            from datetime import UTC, datetime

            now = datetime.now(tz=UTC)
            meta = dict(row.metadata or {}) if row is not None else {}
            meta["frontier_set_by_admin"] = "true"
            meta["frontier_status"] = "approved" if payload.is_relevant else "dead_end"
            pending_url = meta.pop("pending_url", "")
            session.execute(
                DomainTrackingStmts.INSERT,
                (
                    payload.domain,
                    row.last_crawled_at if row is not None else now,
                    row.last_online_at if row is not None else now,
                    float(row.relevance_score or 0) if row is not None else 0.0,
                    (row.category if row is not None else "") or "",
                    payload.is_relevant,
                    meta,
                    "approved" if payload.is_relevant else "dead_end",
                ),
            )
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
            if payload.is_relevant:
                # Domain-centric model: an approved domain becomes a monitored
                # source so its content is reported on going forward — we don't
                # re-judge individual pages for relevance.
                scrape_url = pending_url or f"https://{payload.domain}"
                service_id = payload.domain.replace(".", "-").lower()
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
                    pass
            # Close the learning loop: log this domain decision as classifier
            # feedback (preview text + approved flag) so the relevance model trains
            # on it and generalizes to similar new domains — not just this one.
            try:
                blob = " ".join(
                    x
                    for x in (
                        meta.get("preview_title", ""),
                        meta.get("preview_description", ""),
                        meta.get("preview_keywords", ""),
                        meta.get("link_text", ""),
                    )
                    if x
                ).strip()
                if blob:
                    store.record_classifier_feedback(
                        url=pending_url or f"https://{payload.domain}",
                        text_sample=blob[:2000],
                        category="news" if payload.is_relevant else "generic",
                        predicted_category=None,
                        quality="high" if payload.is_relevant else "spam",
                        predicted_publish=payload.is_relevant,
                        approved=payload.is_relevant,
                        admin_wallet=wallet,
                        source_relevant=payload.is_relevant,
                        training_only=True,
                    )
            except Exception:
                pass

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
        appears in the review queue once the worker finishes (a few seconds)."""
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        try:
            from celery import Celery

            from app.core.config import settings

            app_c = Celery(broker=settings.celery_broker_url)
            app_c.send_task(
                "app.tasks.newspaper.check_and_publish_mistral_on_diff", queue="pipeline"
            )
            app_c.send_task(
                "app.tasks.newspaper.drain_standard_publish_queue", queue="pipeline"
            )
        except Exception as exc:
            return json_error_response(502, "broker_unavailable", str(exc))
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

    @app.get("/api/v1/admin/investigations")
    async def admin_investigation_findings(request: Request) -> Response:
        """Evidence trail: tool calls the investigative agent made for a source
        URL (?url=...)."""
        denied = require_admin_wallet(request)
        if denied is not None:
            return denied
        url = request.query_params.get("url", "") or ""
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
