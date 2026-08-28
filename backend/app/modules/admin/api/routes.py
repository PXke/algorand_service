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
    ArticleDraftRequest,
    ArticlePatchRequest,
    ClassifierFeedbackCreate,
    DomainSetRequest,
    EditorialBriefCreate,
    GlossaryUpsertRequest,
    ScraperRunRequest,
    ServiceMergeRequest,
    ShareLinkCreateRequest,
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


def _truncate_confirmation_hash(tables: tuple[str, ...]) -> str:
    """Deterministic fingerprint of the table set a destructive TRUNCATE-all endpoint wipes."""
    import hashlib

    return hashlib.sha256("|".join(tables).encode()).hexdigest()


def _require_truncate_confirmation(request: Request, tables: tuple[str, ...]) -> Response | None:
    """Guard shared by the admin TRUNCATE-all endpoints (reset-articles, clear-domains).

    Allowed unconditionally outside prod, but in prod only when the request
    body carries `{"confirm": "<sha256 of the exact table set>"}`.
    `require_admin_wallet` already authenticates the caller; this is a
    second, deliberate-intent check so a stray/blind POST with an empty body
    can't wipe production data by accident. Returns an error Response to
    hand back as-is, or None when the caller may proceed.
    """
    from app.core.config import settings

    if settings.app_env != "prod":
        return None
    expected = _truncate_confirmation_hash(tables)
    try:
        body = serialization.loads(request.body or "{}")
    except Exception:
        body = {}
    confirm = body.get("confirm", "") if isinstance(body, dict) else ""
    if isinstance(confirm, str) and confirm == expected:
        return None
    return json_error_response(
        403,
        "confirmation_required",
        f'This truncates production data. Retry with {{"confirm": "{expected}"}} to proceed.',
    )


def admin_analytics(request: Request) -> Response | dict:
    """Site-wide pageview/referrer analytics for the given day window, cached briefly."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    days_param = request.query_params.get("days", "")
    days = max(1, min(int(days_param) if days_param.isdigit() else 14, 90))

    def _compute() -> dict:
        from app.modules.seo.analytics_store import read_analytics

        return read_analytics(days=days)


    from app.core.cache import cached_json

    # This aggregate does a full sequential pass over `days` day-partitions
    # plus several site-wide aggregates (~1s) — the original motivation for
    # running multi-process, but the handler itself was never offloaded.
    # 30s TTL: an admin reloading the tab a few times in a row shouldn't
    # re-run the whole thing every time.
    return cached_json(f"admin:analytics:{days}", 30, _compute)


def admin_patch_article(request: Request) -> Response:
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

    updated = store.update_article(article_id, title=payload.title, summary=payload.summary, body=payload.body, editor=f"admin:{wallet}", )
    if updated is None:
        return json_error_response(404, "not_found", "Article not found")
    return asdict(updated)


def admin_get_article(request: Request) -> Response:
    """Fetch one article's full detail for the admin editor. Bypasses the public draft gate (news_service.get_article, which treats a drafted article as not-found) — this is the only read path that can still see a currently-drafted article's content."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")
    article = store.get_article(article_id)
    if article is None:
        return json_error_response(404, "not_found", "Article not found")
    return asdict(article)


def admin_list_article_versions(request: Request) -> Response:
    """Version history for one article (title/editor/reason/date), newest first."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")
    return {"items": store.list_article_versions(article_id)}


def admin_get_article_version(request: Request) -> Response:
    """Full content (title/summary/body) of one prior version, for the diff view."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")
    try:
        version = int(request.path_params.get("version", ""))
    except ValueError:
        return json_error_response(400, "invalid_request", "version must be an integer")
    result = store.get_article_version(article_id, version)
    if result is None:
        return json_error_response(404, "not_found", "Version not found")
    return result


def admin_list_draft_articles(request: Request) -> Response:
    """Currently-drafted articles (absent from the public feed, so the normal article list can never find them again to restore)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    return {"items": store.list_draft_articles()}


def admin_set_article_draft(request: Request) -> Response:
    """Toggle an article's admin-only draft flag (unpublish/restore), reversibly."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")
    try:
        payload = serialization.decode(request.body or b"{}", ArticleDraftRequest)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))

    updated = store.set_article_draft(article_id, payload.draft)
    if updated is None:
        return json_error_response(404, "not_found", "Article not found")
    return asdict(updated)


def admin_delete_article(request: Request) -> Response:
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

    source_url = ""
    if block_source:
        current = store.get_article(article_id)
        source_url = (current.source_url or "") if current is not None else ""

    deleted = store.delete_article(article_id)
    if not deleted:
        return json_error_response(404, "not_found", "Article not found")

    blocked = False
    if block_source and source_url:
        from app.modules.registry.sources import domain_from_url

        domain = domain_from_url(source_url)
        if domain:
            store.reject_domain_source(domain=domain, wallet=wallet, source_url_hint=source_url, )
            blocked = True
    return {"deleted": True, "article_id": article_id, "source_blocked": blocked}


def admin_list_briefs(request: Request) -> dict:
    """List editorial briefs."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    items = store.list_briefs()
    return {"items": items}


def admin_create_brief(request: Request) -> Response:
    """Create an editorial brief and eagerly kick off its assignment."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        payload = serialization.decode(request.body, EditorialBriefCreate)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))
    wallet = verified_admin_wallet(request)

    item = store.create_brief(title=payload.title, body_markdown=payload.body_markdown, keywords=payload.keywords, status=payload.status, wallet_address=wallet, refresh_every_days=payload.refresh_every_days, is_special_edition=payload.is_special_edition, )
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


def admin_assign_brief_now(request: Request) -> Response:
    """Queue a brief for (re)assignment now instead of waiting for its refresh schedule."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    brief_id = request.path_params.get("brief_id", "")

    item = store.get_brief(brief_id)
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
    except Exception:
        logger.exception("failed to queue brief assignment for %s", brief_id)
        return json_error_response(500, "assign_failed", "Failed to queue brief assignment")


def admin_list_classifier_reviews(request: Request) -> Response:
    """List pending classifier review candidates."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    items = store.list_classifier_reviews()
    return {"items": items}


def admin_pending_feed_backlog(request: Request) -> Response | dict:
    """Approved articles waiting in pending_feed_queue for paced release (capped by PENDING_FEED_MAX_DEPTH) — distinct from the in-flight composing work the artifacts/to_compose Queue tab view shows."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    items = store.list_pending_feed_backlog()
    return {"items": items}


def admin_training_stats(request: Request) -> Response:
    """Labelled-data volume + balance + grader readiness for the Training tab."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    from app.core.cache import cached_json

    # Short TTL: scans up to 5000 rows + a concurrent detail batch; doesn't
    # need to be real-time. Invalidated on each feedback write below.
    return cached_json("admin:training_stats", 30, store.training_stats)


def admin_retrain(request: Request) -> Response:
    """Trigger a retrain of the publish classifier now."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        from celery import Celery

        from app.core.config import settings

        client = Celery(broker=settings.celery_broker_url)
        client.send_task("app.tasks.crawler.retrain_publish_classifier", queue="scrape")
        return {"status": "queued"}
    except Exception:
        logger.exception("failed to queue publish-classifier retrain")
        return json_error_response(500, "retrain_failed", "Failed to queue retrain")


def admin_classifier_feedback(request: Request) -> Response:
    """Record a human correction to a classifier verdict and invalidate the training-stats cache."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        payload = serialization.decode(request.body, ClassifierFeedbackCreate)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))
    wallet = verified_admin_wallet(request)

    result = store.record_classifier_feedback(url=payload.url, text_sample=payload.text_sample, category=payload.category, predicted_category=payload.predicted_category, quality=payload.quality, predicted_publish=payload.predicted_publish, approved=payload.approved, admin_wallet=wallet, review_id=payload.review_id, article_id=payload.article_id, source_relevant=payload.source_relevant, categories=payload.categories, training_only=payload.training_only, corrected_scores=payload.corrected_scores, )
    # Labeling changes the Training-tab aggregate — drop its cache so the
    # next load reflects this decision immediately (don't wait for the TTL).
    from app.core.cache import invalidate

    invalidate("admin:training_stats")
    return result


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


def admin_merge_services(request: Request) -> Response:
    """Fold services into one (multi-domain entities like algorand.co + algorand.com): sources move to the target, domains re-point, merged services are disabled. The target's next weekly poll aggregates across all of its domains."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    try:
        payload = serialization.decode(request.body, ServiceMergeRequest)
    except Exception as exc:
        return json_error_response(400, "invalid_request", str(exc))

    from app.modules.registry.sources import merge_services

    return merge_services(target_service_id=payload.target_service_id, source_service_ids=payload.source_service_ids, )


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


def _valid_uuid(value: str) -> bool:
    """True when `value` parses as a UUID -- guards sharing.store's unguarded UUID(...) calls so a malformed id 400s cleanly instead of 500ing."""
    from uuid import UUID

    try:
        UUID(value)
    except ValueError:
        return False
    return True


def admin_create_share_link(request: Request) -> Response:
    """Mint a new share link for one article -- the returned item is the only place a usable token is ever handed back."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")
    if not _valid_uuid(article_id):
        return json_error_response(400, "invalid_request", "article_id required")
    try:
        payload = serialization.decode(request.body or b"{}", ShareLinkCreateRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    from app.modules.sharing.store import create_link

    wallet = verified_admin_wallet(request)
    link = create_link(article_id, label=payload.label.strip(), created_by=wallet or "")
    return serialization.to_builtins(link)


def admin_list_share_links(request: Request) -> Response:
    """List every share link (active and revoked) ever minted for one article."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")
    if not _valid_uuid(article_id):
        return json_error_response(400, "invalid_request", "article_id required")
    from app.modules.sharing.store import list_links_for_article

    return {"items": serialization.to_builtins(list_links_for_article(article_id))}


def admin_revoke_share_link(request: Request) -> Response:
    """Revoke a share link -- it stops resolving for public readers but the row (and share history) persists."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")
    token = request.path_params.get("token", "")
    if not article_id or not token:
        return json_error_response(400, "invalid_request", "article_id and token required")
    from app.modules.sharing.store import revoke_link

    link = revoke_link(token)
    if link is None or link.article_id != article_id:
        return json_error_response(404, "not_found", "Share link not found")
    return serialization.to_builtins(link)


def admin_list_article_comments(request: Request) -> Response:
    """The full shared comment thread for one article -- the same thread every share-link holder sees."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")
    if not _valid_uuid(article_id):
        return json_error_response(400, "invalid_request", "article_id required")
    from app.modules.sharing.store import list_comments

    return {"items": serialization.to_builtins(list_comments(article_id))}


def admin_delete_article_comment(request: Request) -> Response:
    """Delete one comment (moderation against spam or an accidentally-shared link)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")
    comment_id = request.path_params.get("comment_id", "")
    if not _valid_uuid(article_id) or not _valid_uuid(comment_id):
        return json_error_response(400, "invalid_request", "article_id and comment_id required")
    from app.modules.sharing.store import delete_comment

    deleted = delete_comment(article_id, comment_id)
    if not deleted:
        return json_error_response(404, "not_found", "Comment not found")
    return {"deleted": True, "comment_id": comment_id}


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


def admin_celery_overview(request: Request) -> Response:
    """Broker/worker overview for the admin dashboard, cached briefly."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    from app.core.cache import cached_json
    from app.modules.admin.scrapers import celery_overview

    try:
        # Broker inspect is slow-ish; 10s cache smooths dashboard polling.
        return cached_json("admin:celery", 10, celery_overview)
    except Exception as exc:
        return json_error_response(502, "broker_unavailable", str(exc))


def admin_health_check(request: Request) -> Response:
    """Run one readiness check (redis/cassandra/typesense/conduit_index/celery_queues) by name.

    Split out of `/health/ready` so the System tab can fire one request per
    check in parallel instead of one combined call that blocks on the
    slowest dependency (Typesense and the Conduit chain-index query are
    usually the culprits). `/health/ready` itself is untouched — the deploy
    pipeline still gates on that single combined payload.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    from app.core.cache import cached_json
    from app.core.health import CHECKS

    name = request.path_params.get("name", "")
    check = CHECKS.get(name)
    if check is None:
        return json_error_response(404, "unknown_check", f"unknown check: {name}")

    # Short cache: a Cassandra/Typesense probe isn't free, and Refresh plus
    # multiple admins on the tab shouldn't each pay for it.
    return cached_json(f"admin:health-check:{name}", 5, lambda: asdict(check()))


_RESET_ARTICLES_TABLES = (
    "articles",
    "articles_by_tag",
    "article_versions",
    "artifacts",
    "artifacts_pending",
    "artifact_content",
    "to_compose",
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


def admin_reset_articles(request: Request) -> Response:
    """Beta convenience: wipe all article/publish state so the pipeline starts fresh. Keeps sources, classifier feedback and pending reviews."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    tables = _RESET_ARTICLES_TABLES
    guard = _require_truncate_confirmation(request, tables)
    if guard is not None:
        return guard

    from app.core.cassandra import get_cassandra_session
    from app.core.typesense_client import clear_search_index

    def _truncate_all() -> None:
        session = get_cassandra_session()
        for table in tables:
            session.execute(f"TRUNCATE {table}")


    try:
        _truncate_all()
    except Exception:
        logger.exception("failed to truncate article/publish tables during admin reset")
        return json_error_response(500, "reset_failed", "Failed to reset article state")
    typesense = clear_search_index()
    return {"reset": True, "tables": list(tables), "typesense": typesense}


_CLEAR_CLASSIFIER_REVIEWS_TABLES = ("classifier_review_pending", "classifier_review_queue")


def admin_clear_classifier_reviews(request: Request) -> Response:
    """Discard all pending review items without recording any feedback (stored classifier_feedback labels are untouched)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    guard = _require_truncate_confirmation(request, _CLEAR_CLASSIFIER_REVIEWS_TABLES)
    if guard is not None:
        return guard

    from app.core.cassandra import get_cassandra_session

    def _truncate() -> None:
        session = get_cassandra_session()
        for table in _CLEAR_CLASSIFIER_REVIEWS_TABLES:
            session.execute(f"TRUNCATE {table}")


    _truncate()
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
    recency = -_recency_epoch(it.get("last_crawled_at") or "")
    if content_rel is None:
        # No page-fetch relevance yet (classify_pending_domains hasn't reached
        # this domain) -- fall back to relevance_score, the cheap keyword-based
        # signal computed from the discovering link's own preview text at
        # discovery time (register_pending_domain -> preview_score), so it's
        # available immediately for every pending domain, not just ones the
        # periodic classify task has already sampled. Previously this tier sorted
        # by recency ALONE, so a busy discovery day dumped dozens of low-signal
        # domains ahead of a high-scoring one purely because it arrived a minute
        # later -- exactly the "need to go a few pages before finding something
        # relevant" complaint (owner feedback 2026-08-25). Recency stays as the
        # tiebreak beneath it so same-score domains still keep the old newest-
        # first behavior.
        tier, tie_break = 0, (-float(it.get("relevance_score") or 0.0), recency)
    else:
        tier, tie_break = 1, (-content_rel, recency)
    return (order.get(it["frontier_status"], 2), tier, tie_break)


def _parse_relevance_components(raw: str) -> dict[str, float]:
    """Best-effort JSON-decode of content_relevance_components -- {} for domains scored before this field existed, or any malformed/non-dict value, so a single bad row never 500s the whole Domains tab list."""
    if not raw:
        return {}
    try:
        import json

        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    result: dict[str, float] = {}
    for key, value in parsed.items():
        try:
            result[str(key)] = float(value)
        except (ValueError, TypeError):
            continue
    return result


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
                # Structured counterpart of content_relevance_reasons above —
                # score_page()'s actual per-signal numeric contribution (see
                # workers' ClassifierResult.components docstring), for the
                # admin Domains tab's relevance breakdown. JSON-decoded from
                # the metadata map<text,text> column; {} for domains scored
                # before this existed or on any malformed value, so an old
                # row never crashes this endpoint.
                "content_relevance_components": _parse_relevance_components(
                    meta.get("content_relevance_components", "")
                ),
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


def admin_list_domains(request: Request) -> Response:
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


    return _admin_domains_page(status, page, page_size)


def admin_list_tool_suggestions(request: Request) -> Response:
    """Capabilities the writer model wished it had (via the suggest_tool tool), newest first — input for which tools to build next. Resolved suggestions (tools that have since shipped) are hidden by default so the list only shows genuine gaps instead of growing forever; pass ?include_resolved=true to see the full history."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    include_resolved = (request.query_params.get("include_resolved", "") or "").strip().lower() in (
        "1",
        "true",
    )


    items = store.list_tool_suggestions(include_resolved=include_resolved)
    return {"items": items}


def admin_list_compose_feedback(request: Request) -> Response:
    """Writer-reported prompt/data/tool/pipeline issues (report_compose_issue)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    def _compute() -> dict:
        from datetime import UTC, datetime

        from algorand_shared.feed_bucket import months_back

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        # "all" is the legacy pre-2026-08-24 partition, kept in the scan
        # permanently -- see tool_insights_store's bucket-cutover comment.
        buckets = ["all", *months_back(datetime.now(tz=UTC), 3)]
        rows = [
            r
            for bucket in buckets
            for r in session.execute(ToolInsightStmts.LIST_COMPOSE_FEEDBACK, (bucket,))
        ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
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


    return _compute()


def admin_list_compose_sessions(request: Request) -> Response:
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
        from datetime import UTC, datetime

        from algorand_shared.feed_bucket import months_back

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        # "all" is the legacy pre-2026-08-24 partition, kept in the scan
        # permanently -- see tool_insights_store's bucket-cutover comment.
        # Each bucket can supply at most `limit` of the final merged top-N, so
        # fetching `limit` per bucket (already sorted created_at DESC within
        # its own partition) and re-sorting the union is a correct keyset page
        # across however many partitions the data is spread over.
        buckets = ["all", *months_back(datetime.now(tz=UTC), 3)]
        if before:
            cursor = datetime.fromisoformat(before)
            rows = [
                r
                for bucket in buckets
                for r in session.execute(
                    ToolInsightStmts.LIST_COMPOSE_SESSIONS_SUMMARY_BEFORE,
                    (bucket, cursor, limit),
                )
            ]
        else:
            rows = [
                r
                for bucket in buckets
                for r in session.execute(ToolInsightStmts.LIST_COMPOSE_SESSIONS_SUMMARY, (bucket, limit))
            ]
        rows.sort(key=lambda r: r.created_at, reverse=True)
        rows = rows[:limit]
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
                "cached_tokens": r.cached_tokens or 0,
            }
            for r in rows
        ]
        return {"items": items}


    from app.core.cache import cached_json

    # Short TTL: this is polled every ~8s from possibly several open admin
    # tabs, so a few seconds of staleness collapses concurrent polls into
    # one Cassandra read instead of one per request.
    # Cursor and limit are part of the key — otherwise page 2 would be served
    # the cached page 1.
    key = f"admin:compose-sessions:{before or 'head'}:{limit}"
    return cached_json(key, 5, _compute)


def admin_get_compose_session(request: Request) -> Response:
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
        from algorand_shared.feed_bucket import feed_month

        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ToolInsightStmts

        session = get_cassandra_session()
        # created_at deterministically identifies the bucket the row was
        # written under (feed_month at write time) -- try the real bucket
        # first, falling back to the legacy "all" partition for any row
        # written before the 2026-08-24 cutover.
        row = session.execute(
            ToolInsightStmts.GET_COMPOSE_SESSION_DETAIL, (feed_month(created_at), created_at, sid)
        ).one()
        if row is None:
            row = session.execute(
                ToolInsightStmts.GET_COMPOSE_SESSION_DETAIL, ("all", created_at, sid)
            ).one()
        if row is None:
            return {"messages": [], "final_output": ""}
        try:
            msgs = serialization.loads(row.messages) if row.messages else []
        except Exception:
            msgs = []
        return {"messages": msgs, "final_output": row.final_output or ""}


    return _compute()


def admin_interrogate_compose_session(request: Request) -> Response | dict:
    """Ask a revived compose session's writer one question — an interactive post-mortem on the same model/transcript that produced it, confronted with live ground truth (DNS + its own transcript's fetch failures) by default.

    Wraps interrogate_compose_session_task (workers/app/modules/ai/interrogate.py)
    via Celery. Unlike a compose trigger, this BLOCKS on the task's result —
    one interrogation turn is a single bounded Mistral call, not a multi-minute
    compose, so waiting for it (like admin_recompose_review's fire-and-forget
    sibling routes do NOT) is the right shape here: the UI needs the actual
    answer to render, not just an "it's running" acknowledgement.

    Caveat surfaced to the admin, not just documented here: an LLM cannot truly
    introspect its past reasoning — under pressure it rationalises or
    capitulates. Its answers are leads to verify against the trace and live
    sources, never verdicts on their own.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    session_id = request.path_params.get("session_id", "")
    if not session_id:
        return json_error_response(400, "invalid_request", "session_id is required")
    try:
        body = serialization.loads(request.body or "{}")
    except Exception:
        body = {}
    question = str(body.get("question", "")).strip()
    if not question:
        return json_error_response(400, "invalid_request", "question is required")
    ground_truth = bool(body.get("ground_truth", True))
    history = body.get("history")
    if not isinstance(history, list):
        history = []

    try:
        from celery import Celery
        from celery.exceptions import TimeoutError as CeleryTimeoutError

        from app.core.config import settings

        async_result = Celery(
            broker=settings.celery_broker_url, backend=settings.redis_result_url
        ).send_task(
            "app.tasks.newspaper.interrogate_compose_session",
            kwargs={
                "target": "",
                "question": question,
                "ground_truth": ground_truth,
                "history": history,
                "session_id": session_id,
            },
            queue="pipeline",
        )
        try:
            result = async_result.get(timeout=90)
        except CeleryTimeoutError:
            return json_error_response(
                504, "timeout", "the writer model took too long to respond"
            )
    except Exception as exc:
        return json_error_response(502, "broker_unavailable", str(exc))

    if not result.get("ok"):
        return json_error_response(
            502, "interrogation_failed", str(result.get("error") or "unknown error")
        )
    return result


def _dispatch_recompose(source_url: str) -> Response | dict:
    """Dispatch recompose_session_service(source_url) and wait briefly for an instant failure to surface.

    Shared by admin_recompose_session (source_url from the request body) and
    admin_recompose_article (source_url resolved from the article's own
    record) — same underlying trigger, just two different starting points an
    admin has in hand.

    Not purely fire-and-forget: root-caused live 2026-08-05 that a pure
    fire-and-forget dispatch left a resolution failure (no live article
    found for this source) completely invisible — the button showed
    "Triggered" even though nothing happened. The resolution step is a
    cheap Cassandra read (~7ms observed) and the actual compose is the slow
    part, so — same reasoning as admin_compose_next's short .get() — wait
    briefly for JUST an instant failure to surface, then stop waiting once
    it's clear the real (multi-minute) compose is underway.
    """
    try:
        from celery import Celery
        from celery.exceptions import TimeoutError as CeleryTimeoutError

        from app.core.config import settings

        async_result = Celery(
            broker=settings.celery_broker_url, backend=settings.redis_result_url
        ).send_task(
            "app.tasks.newspaper.recompose_session_service",
            args=[source_url],
            queue="pipeline",
        )
        import contextlib

        early_result: dict | None = None
        with contextlib.suppress(CeleryTimeoutError):
            early_result = async_result.get(timeout=4)
    except Exception as exc:
        return json_error_response(502, "broker_unavailable", str(exc))

    if early_result is not None and early_result.get("status") == "error":
        return json_error_response(
            422, "recompose_failed", str(early_result.get("reason") or "unknown error")
        )
    return {"triggered": True, "source_url": source_url}


def admin_recompose_session(request: Request) -> Response | dict:
    """Recompose the live article behind this compose session.

    "I just read this transcript, changed a prompt, and want to see it behave now."

    A compose session has no article_id of its own and its originating
    publish_queue row has almost always already resolved by the time an
    admin is reading it, so this is a DIFFERENT trigger from Queue tab's
    "Recompose now" (which needs a still-pending queue row). Dispatches
    recompose_session_service(source_url), which resolves the live article
    behind this source and hands off to recompose_published — the same
    archive-refresh path the pipeline's own weekly recompose cadence uses.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    try:
        body = serialization.loads(request.body or "{}")
    except Exception:
        body = {}
    source_url = str(body.get("source_url", "")).strip()
    if not source_url:
        return json_error_response(400, "invalid_request", "source_url is required")

    return _dispatch_recompose(source_url)


def admin_recompose_article(request: Request) -> Response | dict:
    """Recompose the given article directly from the Articles admin page — "reprocess this one now" without going through a compose session or publish-queue row. Resolves the article's own source_url and dispatches the same recompose_session_service path admin_recompose_session and the pipeline's own weekly cadence use."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    article_id = request.path_params.get("article_id", "")

    article = store.get_article(article_id)
    if article is None:
        return json_error_response(404, "not_found", "Article not found")
    source_url = (article.source_url or "").strip()
    if not source_url:
        return json_error_response(
            422, "no_source_url", "Article has no source_url to recompose from"
        )

    return _dispatch_recompose(source_url)


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

    from app.core import config
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
    # Row TTL matching workers' enqueue_url (0 = CQL's documented "no TTL").
    ttl = max(0, config.URL_QUEUE_ROW_TTL_SECONDS)
    session.execute(
        UrlQueueStmts.INSERT,
        (
            queue_id,
            seed_url,
            "frontier_approval",
            seed_priority,
            now,
            "pending",
            queue_metadata,
            ttl,
        ),
    )
    session.execute(UrlQueueStmts.INSERT_BY_URL, (seed_url, queue_id, now, "pending", ttl))
    session.execute(
        UrlQueueStmts.INSERT_PENDING,
        ("pending", seed_priority, now, queue_id, seed_url, "frontier_approval", ttl),
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
    # Event-driven: kick the frontier crawl now so an approved domain is
    # explored immediately instead of waiting for the (now hourly) beat. The
    # actual scrape+compose for this service happens on run_llm_diff_check's
    # own ~10min beat, which already picks up any never-scraped/never-
    # throttled service promptly — no separate immediate-scrape trigger
    # needed (fetch_source, which did that fire-and-forget with its result
    # never read by any caller, was removed 2026-08-28).
    if enqueued:
        try:
            from celery import Celery

            from app.core.config import settings

            Celery(broker=settings.celery_broker_url).send_task(
                "app.tasks.crawler.drain_url_queue", queue="scrape"
            )
        except Exception:
            logger.warning("failed to trigger crawl for approved domain %s", domain, exc_info=True)
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


def admin_set_domain(request: Request) -> Response:
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


    result = _admin_set_domain_compute(payload, wallet)
    _invalidate_domains_cache()
    return result


_CLEAR_DOMAINS_TABLES = ("domain_tracking",)


def admin_clear_domains(request: Request) -> Response:
    """Forget the whole crawl frontier: every explored/pending/dead-end domain record. The blocklist (config) is unaffected."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    guard = _require_truncate_confirmation(request, _CLEAR_DOMAINS_TABLES)
    if guard is not None:
        return guard

    from app.core.cassandra import get_cassandra_session

    get_cassandra_session().execute("TRUNCATE domain_tracking")
    _invalidate_domains_cache()
    return {"cleared": True}


def admin_compose_next(request: Request) -> Response:
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

    from algorand_shared.article_transitions import list_backlog_articles

    from app.core.cassandra import get_cassandra_session
    from app.core.config import settings
    from app.core.statements import ClassifierReviewStmts

    session = get_cassandra_session()
    pending_review = session.execute(ClassifierReviewStmts.LIST_PENDING, ("pending", 1))
    if pending_review.one() is not None:
        return {
            "triggered": False,
            "reason": "classifier_review_pending",
            "message": "A proposal is already waiting in the review queue — "
            "resolve it before pulling a new one.",
        }
    if settings.pause_intake_on_feed_backlog and list_backlog_articles():
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
        # 2026-08-25: repointed from drain_standard_publish_queue (retired)
        # to its editorial-room successor -- see
        # workers/app/modules/newspaper/tasks/queue_drain_tasks.py.
        drain_async = app_c.send_task(
            "app.tasks.newspaper.drain_to_compose", queue="pipeline"
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
        outcome = _fire_and_wait()
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
    try:
        body = serialization.loads(request.body or "{}")
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

    try:
        body = serialization.loads(request.body or "{}")
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


def admin_investigation_findings(request: Request) -> Response:
    """Evidence trail: tool calls the investigative agent made for a source URL (?url=...)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    from urllib.parse import unquote

    url = unquote(request.query_params.get("url", "") or "")
    if not url:
        return {"items": []}

    def _compute() -> dict:
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
                result = serialization.loads(r.result_json) if r.result_json else {}
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


    return _compute()


def admin_artifacts_to_compose_preview(request: Request) -> Response | dict:
    """Read-only: what the editorial-room `artifacts`/`to_compose` selection (see algorand_shared.to_compose_selection) currently would pick for `day` (default: tomorrow) — the human-pin slot (if pinned) plus the top-priority platform picks, with each pending artifact's live priority breakdown.

    2026-08-25: this selection is LIVE -- select_to_compose_for_today_task
    (a daily beat) and drain_to_compose (the compose trigger) both read the
    same `to_compose` table this preview is forecasting. This endpoint
    itself is still read-only and side-effect-free either way.

    2026-08-26: calls algorand_shared.to_compose_selection.preview_to_compose_for_day
    directly instead of dispatching into a worker over Celery. The
    selection/priority logic lived only in workers/ purely because that's
    where it was originally written, not because it needed anything
    workers-only -- it's a handful of Cassandra reads plus pure-function
    scoring, the same shape as the Domains tab's admin_list_domains, which
    already reads `domain_tracking` directly. The Celery round-trip this
    replaced was also a real production liability: a heavy background job
    filling the shared queue could make this read-only route time out
    waiting for a worker slot alongside unrelated work.

    Never mutates artifact status or writes to `to_compose` (unlike
    select_to_compose_for_day itself) — safe to call on every dashboard load.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    from datetime import UTC, date, datetime, timedelta

    day = (request.query_params.get("day", "") or "").strip()
    if not day:
        day = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
    else:
        try:
            date.fromisoformat(day)
        except ValueError:
            return json_error_response(400, "invalid_request", "day must be YYYY-MM-DD")

    from algorand_shared.to_compose_selection import preview_to_compose_for_day

    return preview_to_compose_for_day(day)


def admin_artifacts_to_compose_selected(request: Request) -> Response | dict:
    """Read-only: the REAL, persisted `to_compose` lineup for `day` (default: tomorrow) -- what select_to_compose_for_day(day) actually picked the last time the daily beat (select_to_compose_for_today_task, 00:05 UTC) ran for that day, slot-ordered. Empty (`items: []`) until that beat has fired for `day` -- this is expected, not an error, and the UI should say so rather than reading as broken.

    Distinct from admin_artifacts_to_compose_preview just above: the preview
    forecasts what a selection run would currently pick (recomputed live,
    every dashboard load); this route reads back what was actually locked in
    and written to the `to_compose` table. They can legitimately disagree --
    e.g. the pending pool has changed since the beat last ran -- and the
    admin UI keeps them in visually separate sections for exactly that
    reason.

    2026-08-26: calls algorand_shared.to_compose_selection.list_to_compose_for_day
    directly -- a single already-prepared Cassandra SELECT with no scoring/
    computation, the same trivial shape as the Domains tab's own direct
    Cassandra reads -- instead of a Celery round-trip into a worker process.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    from datetime import UTC, date, datetime, timedelta

    day = (request.query_params.get("day", "") or "").strip()
    if not day:
        day = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
    else:
        try:
            date.fromisoformat(day)
        except ValueError:
            return json_error_response(400, "invalid_request", "day must be YYYY-MM-DD")

    from algorand_shared.to_compose_selection import list_to_compose_for_day

    items = list_to_compose_for_day(day)
    return {"compose_day": day, "items": items}


def admin_reset_to_compose_for_day(request: Request) -> Response | dict:
    """"Redo today's picks": clear `day`'s (default: tomorrow, matching the sibling to-compose routes and the Queue tab's own day field) locked-in `to_compose` selection and immediately re-run selection over the widened pool -- the fix for a bad automatic pick, or forcing a re-pick after correcting an upstream priority/pool bug, without waiting for the next daily beat.

    2026-08-26: calls algorand_shared.to_compose_selection.reset_and_reselect_for_day
    directly instead of dispatching into a worker over Celery. It clears
    `to_compose` for `day`, reverts any artifact it had selected back to
    PENDING -- but ONLY when that artifact is still in SELECTED status; one
    already progressed to composed (a real article now exists) or discarded
    (a gate permanently dropped it) is left alone and reported in the
    response's `reset.skipped` list -- then immediately calls
    select_to_compose_for_day(day) again, so the caller gets a freshly
    re-picked lineup in one round trip rather than two separate admin
    actions.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    from datetime import UTC, date, datetime, timedelta

    day = (request.query_params.get("day", "") or "").strip()
    if not day:
        day = (datetime.now(tz=UTC).date() + timedelta(days=1)).isoformat()
    else:
        try:
            date.fromisoformat(day)
        except ValueError:
            return json_error_response(400, "invalid_request", "day must be YYYY-MM-DD")

    from algorand_shared.to_compose_selection import reset_and_reselect_for_day

    return reset_and_reselect_for_day(day)


def admin_pin_artifact_for_tomorrow(request: Request) -> Response | dict:
    """Pin one editorial-room artifact as tomorrow's human pick (see algorand_shared.artifact_store.pin_artifact_for_day / algorand_shared.to_compose_selection.pin_for_tomorrow).

    2026-08-25: this is the LIVE human-pick mechanism -- the pinned artifact
    is picked up by the next select_to_compose_for_today_task run and
    composed by drain_to_compose (see queue_drain_tasks.py). Writes to
    `artifacts` / `artifacts_pending` / `to_compose`, never to publish_queue.

    2026-08-26: calls pin_for_tomorrow directly -- a single targeted UPDATE
    plus a pending-index reindex -- instead of dispatching into a worker
    over Celery.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    artifact_id = request.path_params.get("artifact_id", "")
    if not artifact_id:
        return json_error_response(400, "invalid_request", "artifact_id is required")

    from algorand_shared.to_compose_selection import pin_for_tomorrow

    ok = pin_for_tomorrow(artifact_id)
    if not ok:
        return json_error_response(404, "not_found", "unknown artifact_id")
    return {"ok": ok, "artifact_id": artifact_id}


def admin_get_artifact_content(request: Request) -> Response | dict:
    """Full title/content/url/metadata for one editorial-room artifact -- the raw text that would actually get fed to the writer/composer, plus its source URL. Fetched on demand when an admin expands a Queue-tab row to inspect it, never on the list/preview poll (which stays title-only, to keep that response small).

    2026-08-26: calls algorand_shared.artifact_store.get_artifact_detail
    directly -- a single-partition point read against `artifacts` +
    `artifact_content` by primary key -- instead of dispatching into a
    worker over Celery. Same assembly shared with workers' own
    get_artifact_detail Celery task (still used elsewhere), so the response
    shape is unchanged.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    artifact_id = request.path_params.get("artifact_id", "")
    if not artifact_id:
        return json_error_response(400, "invalid_request", "artifact_id is required")

    from algorand_shared.artifact_store import get_artifact_detail

    result = get_artifact_detail(artifact_id)
    if result is None:
        return json_error_response(404, "not_found", "unknown artifact_id")
    return result


def register_admin_routes(app: Router) -> None:
    """Register all admin API endpoints on the given Robyn app."""
    app.get("/api/v1/admin/analytics")(admin_analytics)
    app.get("/api/v1/admin/articles/drafts")(admin_list_draft_articles)
    app.get("/api/v1/admin/articles/:article_id/versions")(admin_list_article_versions)
    app.get("/api/v1/admin/articles/:article_id/versions/:version")(admin_get_article_version)
    app.get("/api/v1/admin/articles/:article_id")(admin_get_article)
    app.patch("/api/v1/admin/articles/:article_id")(admin_patch_article)
    app.delete("/api/v1/admin/articles/:article_id")(admin_delete_article)
    app.post("/api/v1/admin/articles/:article_id/draft")(admin_set_article_draft)
    app.post("/api/v1/admin/articles/:article_id/recompose")(admin_recompose_article)
    app.get("/api/v1/admin/briefs")(admin_list_briefs)
    app.post("/api/v1/admin/briefs")(admin_create_brief)
    app.post("/api/v1/admin/briefs/:brief_id/assign-now")(admin_assign_brief_now)
    app.get("/api/v1/admin/classifier-reviews")(admin_list_classifier_reviews)
    app.get("/api/v1/admin/pending-feed-backlog")(admin_pending_feed_backlog)
    app.get("/api/v1/admin/training-stats")(admin_training_stats)
    app.post("/api/v1/admin/retrain")(admin_retrain)
    app.post("/api/v1/admin/classifier-feedback")(admin_classifier_feedback)
    app.post("/api/v1/admin/sources")(admin_upsert_source)
    app.post("/api/v1/admin/sources/merge")(admin_merge_services)
    app.delete("/api/v1/admin/sources/:service_id")(admin_delete_source)
    app.get("/api/v1/admin/scrapers")(admin_list_scrapers)
    app.post("/api/v1/admin/scrapers/run")(admin_run_scraper)
    app.get("/api/v1/admin/celery")(admin_celery_overview)
    app.get("/api/v1/admin/health-checks/:name")(admin_health_check)
    app.post("/api/v1/admin/articles/reset")(admin_reset_articles)
    app.post("/api/v1/admin/classifier-reviews/clear")(admin_clear_classifier_reviews)
    app.get("/api/v1/admin/domains")(admin_list_domains)
    app.get("/api/v1/admin/tool-suggestions")(admin_list_tool_suggestions)
    app.get("/api/v1/admin/compose-feedback")(admin_list_compose_feedback)
    app.get("/api/v1/admin/compose-sessions")(admin_list_compose_sessions)
    app.get("/api/v1/admin/compose-sessions/:session_id")(admin_get_compose_session)
    app.post("/api/v1/admin/compose-sessions/:session_id/interrogate")(
        admin_interrogate_compose_session
    )
    app.post("/api/v1/admin/compose-sessions/:session_id/recompose")(admin_recompose_session)
    app.post("/api/v1/admin/domains/set")(admin_set_domain)
    app.post("/api/v1/admin/domains/clear")(admin_clear_domains)
    app.post("/api/v1/admin/classifier-reviews/compose-next")(admin_compose_next)
    app.post("/api/v1/admin/classifier-reviews/recompose")(admin_recompose_review)
    app.post("/api/v1/admin/translations/backfill")(admin_backfill_translations)
    app.get("/api/v1/admin/investigations")(admin_investigation_findings)
    app.get("/api/v1/admin/glossary")(admin_list_glossary)
    app.post("/api/v1/admin/glossary")(admin_upsert_glossary_term)
    app.delete("/api/v1/admin/glossary/:slug")(admin_delete_glossary_term)
    app.post("/api/v1/admin/articles/:article_id/share-links")(admin_create_share_link)
    app.get("/api/v1/admin/articles/:article_id/share-links")(admin_list_share_links)
    app.delete("/api/v1/admin/articles/:article_id/share-links/:token")(admin_revoke_share_link)
    app.get("/api/v1/admin/articles/:article_id/comments")(admin_list_article_comments)
    app.delete("/api/v1/admin/articles/:article_id/comments/:comment_id")(
        admin_delete_article_comment
    )
    # Editorial-room artifact system -- LIVE since 2026-08-25 (see
    # to_compose_selection.py). preview forecasts what a selection run would
    # currently pick; selected reads back what was actually persisted by the
    # daily beat; pin-for-tomorrow writes the human pick both read from.
    app.get("/api/v1/admin/artifacts/to-compose-preview")(admin_artifacts_to_compose_preview)
    app.get("/api/v1/admin/artifacts/to-compose-selected")(admin_artifacts_to_compose_selected)
    app.post("/api/v1/admin/artifacts/to-compose-reset")(admin_reset_to_compose_for_day)
    app.post("/api/v1/admin/artifacts/:artifact_id/pin-for-tomorrow")(
        admin_pin_artifact_for_tomorrow
    )
    app.get("/api/v1/admin/artifacts/:artifact_id/content")(admin_get_artifact_content)
