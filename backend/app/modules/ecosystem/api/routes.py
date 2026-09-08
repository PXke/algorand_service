"""HTTP routes for the Algorand Open Registry (roadmap item 26): free public submit/list/detail/badge, admin review queue.

A NEW, separate concern from the paid x402 directory (see
docs/awesome-algorand-directory-design.md section 6) -- no payment gate
anywhere in this module.
"""

from __future__ import annotations

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.admin.auth import require_admin_wallet, verified_admin_wallet
from app.modules.ecosystem.models.domain import (
    CATEGORIES,
    EcosystemError,
    EntryRequest,
    StoredProject,
    SubmissionQueueItem,
)
from app.modules.ecosystem.services import (
    blurb_service,
    request_service,
    review_service,
    submission_service,
)
from app.modules.ecosystem.services.rate_limit import read_rate_limited, submit_rate_limited
from app.modules.ecosystem.stores.factory import get_project_store
from app.schemas import (
    EcosystemDecisionRequest,
    EcosystemRequestResolveRequest,
    EcosystemRequestSubmitRequest,
    EcosystemSubmitRequest,
    EcosystemUpdateRequest,
)


def _public_project_json(item: StoredProject) -> dict:
    """Serialize a registry entry for a public read. Never includes `contact` or `draft_description` (design doc section 3.1: contact is private/admin-only; draft_description never auto-publishes)."""
    return {
        "slug": item.slug,
        "name": item.name,
        "domain": item.domain,
        "url": item.url,
        "repo_url": item.repo_url,
        "x402_url": item.x402_url,
        "x402_enabled": item.x402_enabled,
        "description": item.description,
        "category": item.category,
        "tags": item.tags,
        "stage": item.stage,
        "open_source": item.open_source,
        "source": item.source,
        "editor_pick": item.editor_pick,
        "last_probed_at_epoch": item.last_probed_at_epoch,
        "reachable": item.reachable,
        # Just a status code, not sensitive -- exposed so the entry page can
        # say what was actually measured ("Site responded (HTTP 200)")
        # instead of an unqualified "Online" (2026-09-08 Fable review: that
        # wording overclaimed for a site that could be parked or wound down
        # while still answering 200 -- this check never reads the body).
        "last_http_status": item.last_http_status,
        "submitted_at_epoch": item.submitted_at_epoch,
        "reviewed_at_epoch": item.reviewed_at_epoch,
    }


def _admin_project_json(item: StoredProject) -> dict:
    """Serialize a registry entry for the admin queue/detail view -- adds the private/review-only fields."""
    return {
        **_public_project_json(item),
        "status": item.status,
        "contact": item.contact,
        "draft_description": item.draft_description,
        "reviewed_by": item.reviewed_by,
        "reject_reason": item.reject_reason,
        "service_id": item.service_id,
        "category_suggestion": item.category_suggestion,
    }


def _queue_item_json(item: SubmissionQueueItem) -> dict:
    return {
        "slug": item.slug,
        "name": item.name,
        "domain": item.domain,
        "url": item.url,
        "category": item.category,
        "source": item.source,
        "submitted_at_epoch": item.submitted_at_epoch,
        "status": item.status,
    }


def _request_json(item: EntryRequest) -> dict:
    """Serialize an entry request for the admin queue/detail view. Never a public read -- these are admin-only end to end."""
    return {
        "request_id": item.request_id,
        "slug": item.slug,
        "kind": item.kind,
        "message": item.message,
        "contact": item.contact,
        "status": item.status,
        "created_at_epoch": item.created_at_epoch,
        "resolved_at_epoch": item.resolved_at_epoch,
        "resolved_by": item.resolved_by,
    }


def _clamped_limit(request: Request) -> int | Response:
    raw = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw) if raw else settings.ecosystem_list_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")
    return max(1, min(limit, settings.ecosystem_list_max_results))


# --------------------------------------------------------------------------- #
# Public routes
# --------------------------------------------------------------------------- #


def ecosystem_submit(request: Request) -> Response | dict:
    """Free, unauthenticated: submit one project for review (design doc section 3)."""
    try:
        payload = serialization.decode(request.body, EcosystemSubmitRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    # Honeypot tripped: answer success so the bot learns nothing, store nothing.
    if payload.website.strip():
        return {"ok": True, "submission_id": "", "status": "pending"}

    if submit_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many submissions — please try again later"
        )

    try:
        domain = submission_service.domain_key(submission_service.normalize_url(payload.url))
    except EcosystemError as exc:
        return json_error_from_platform(exc)
    existing_message = submission_service.existing_domain_message(domain) if domain else None
    if existing_message:
        return json_error_response(409, "duplicate", existing_message)

    try:
        item = submission_service.submit_project(
            name=payload.name,
            url=payload.url,
            description=payload.description,
            category=payload.category,
            repo_url=payload.repo_url,
            x402_url=payload.x402_url,
            tags=list(payload.tags),
            contact=payload.contact,
            category_suggestion=payload.category_suggestion,
        )
    except EcosystemError as exc:
        return json_error_from_platform(exc)

    return {
        "ok": True,
        "submission_id": item.slug,
        "status": item.status,
        "status_url": f"/api/v1/ecosystem/submissions/{item.slug}",
    }


def ecosystem_categories(request: Request) -> Response | dict:
    """Free: the closed category enum (design doc section 4), for the nav/chip row."""
    _ = request
    return {"categories": list(CATEGORIES)}


def ecosystem_list(request: Request) -> Response | dict:
    """Free: approved entries, optionally filtered by category or tag (mutually exclusive), rate-limited per IP."""
    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )

    limit = _clamped_limit(request)
    if isinstance(limit, Response):
        return limit

    raw_category = query_param(request.query_params.get("category", ""))
    raw_tag = query_param(request.query_params.get("tag", ""))
    if raw_category and raw_tag:
        return json_error_response(
            400, "invalid_request", "category and tag are mutually exclusive"
        )

    store = get_project_store()
    if raw_tag:
        items = store.list_by_tag(submission_service.normalize_tag(raw_tag), limit=limit)
    elif raw_category:
        try:
            category = submission_service.validate_category(raw_category)
        except EcosystemError as exc:
            return json_error_from_platform(exc)
        items = store.list_by_category(category, limit=limit)
    else:
        from algorand_shared.ecosystem_statements import ALL_CATEGORY_PARTITION

        items = store.list_by_category(ALL_CATEGORY_PARTITION, limit=limit)
    return {"items": [_public_project_json(item) for item in items]}


def ecosystem_detail(request: Request) -> Response | dict:
    """Free: one approved entry by slug, rate-limited per IP. Pending/rejected entries are 404 here -- see ecosystem_submission_status for the submitter's own view."""
    from app.modules.ecosystem.models.domain import STATUS_APPROVED

    if read_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )
    slug = request.path_params.get("slug", "").strip().lower()
    item = get_project_store().get(slug) if slug else None
    if item is None or item.status != STATUS_APPROVED:
        return json_error_response(404, "not_found", "No registry entry for that slug")
    return _public_project_json(item)


def ecosystem_submission_status(request: Request) -> Response | dict:
    """Free: a submitter's own status lookup (design doc section 3.1 — the status page IS the notification, there is no outbound email)."""
    slug = request.path_params.get("id", "").strip().lower()
    item = get_project_store().get(slug) if slug else None
    if item is None:
        return json_error_response(404, "not_found", "No submission for that id")
    body = {"submission_id": item.slug, "status": item.status}
    if item.status == "approved":
        body["slug"] = item.slug
    if item.status == "rejected":
        body["reason"] = item.reject_reason
    return body


def ecosystem_request_submit(request: Request) -> Response | dict:
    """Free, unauthenticated: suggest a change or removal against an already-approved entry (owner ask 2026-09-08). Reuses submit's own honeypot/rate-limit gates -- same abuse surface, no reason for a second budget."""
    slug = request.path_params.get("slug", "").strip().lower()
    try:
        payload = serialization.decode(request.body, EcosystemRequestSubmitRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    # Honeypot tripped: answer success so the bot learns nothing, store nothing.
    if payload.website.strip():
        return {"ok": True, "request_id": ""}

    if submit_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests — please try again later"
        )

    try:
        item = request_service.submit_request(
            slug=slug,
            kind=payload.kind,
            message=payload.message,
            contact=payload.contact,
        )
    except EcosystemError as exc:
        return json_error_from_platform(exc)

    return {"ok": True, "request_id": item.request_id}


_BADGE_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="164" height="20" role="img" '
    'aria-label="Listed on PXke Algorand">'
    '<linearGradient id="s" x2="0" y2="100%">'
    '<stop offset="0" stop-color="#bbb" stop-opacity=".1"/>'
    '<stop offset="1" stop-opacity=".1"/></linearGradient>'
    '<clipPath id="r"><rect width="164" height="20" rx="3" fill="#fff"/></clipPath>'
    '<g clip-path="url(#r)">'
    '<rect width="76" height="20" fill="#333"/>'
    '<rect x="76" width="88" height="20" fill="#2b6cb0"/>'
    '<rect width="164" height="20" fill="url(#s)"/></g>'
    '<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,sans-serif" font-size="11">'
    '<text x="38" y="14">Listed on</text>'
    '<text x="120" y="14">PXke Algorand</text></g></svg>'
)


def ecosystem_badge(request: Request) -> Response:
    """Free, uncached-rate-limit (fetched by browsers/GitHub's camo proxy, design doc section 2): an SVG badge for an approved entry's slug.

    404s (as a tiny SVG comment, not JSON — a badge consumer expects an
    image response) for anything not approved, so a badge cannot be shown
    for a pending/rejected/nonexistent entry.
    """
    from app.modules.ecosystem.models.domain import STATUS_APPROVED

    slug = request.path_params.get("slug", "").strip().lower()
    item = get_project_store().get(slug) if slug else None
    if item is None or item.status != STATUS_APPROVED:
        return Response(
            status_code=404,
            headers={"Content-Type": "image/svg+xml"},
            description="<svg xmlns='http://www.w3.org/2000/svg'/>",
        )
    return Response(
        status_code=200,
        headers={"Content-Type": "image/svg+xml", "Cache-Control": "public, max-age=3600"},
        description=_BADGE_TEMPLATE,
    )


# --------------------------------------------------------------------------- #
# Admin routes
# --------------------------------------------------------------------------- #


def admin_ecosystem_queue(request: Request) -> Response | dict:
    """Admin: the review queue for one status partition (design doc section 3.3)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    from app.modules.ecosystem.models.domain import STATUSES

    status = query_param(request.query_params.get("status", "pending")).strip().lower() or "pending"
    if status not in STATUSES:
        return json_error_response(
            400, "invalid_request", f"status must be one of: {', '.join(STATUSES)}"
        )
    limit = _clamped_limit(request)
    if isinstance(limit, Response):
        return limit
    items = review_service.list_queue(status, limit=limit)
    return {"items": [_queue_item_json(item) for item in items]}


def admin_ecosystem_detail(request: Request) -> Response | dict:
    """Admin: full detail of one entry, any status."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    slug = request.path_params.get("slug", "").strip().lower()
    item = review_service.get_entry(slug) if slug else None
    if item is None:
        return json_error_response(404, "not_found", "No entry for that slug")
    return _admin_project_json(item)


def admin_ecosystem_decision(request: Request) -> Response | dict:
    """Admin: approve or reject a pending (or previously decided) entry, with optional inline edits on approve."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    slug = request.path_params.get("slug", "").strip().lower()
    if not slug:
        return json_error_response(400, "invalid_request", "slug required")
    try:
        payload = serialization.decode(request.body, EcosystemDecisionRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    wallet = verified_admin_wallet(request)
    try:
        if payload.decision == "approve":
            item = review_service.approve(
                slug,
                wallet=wallet,
                name=payload.name,
                description=payload.description,
                category=payload.category,
                tags=payload.tags,
            )
        else:
            item = review_service.reject(slug, wallet=wallet, reason=payload.reason)
    except EcosystemError as exc:
        return json_error_from_platform(exc)
    return _admin_project_json(item)


def admin_ecosystem_update(request: Request) -> Response | dict:
    """Admin: free-form edit of any entry, regardless of status."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    slug = request.path_params.get("slug", "").strip().lower()
    if not slug:
        return json_error_response(400, "invalid_request", "slug required")
    try:
        payload = serialization.decode(request.body, EcosystemUpdateRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        item = review_service.update_entry(
            slug,
            name=payload.name,
            description=payload.description,
            category=payload.category,
            tags=payload.tags,
            stage=payload.stage,
            open_source=payload.open_source,
            editor_pick=payload.editor_pick,
            repo_url=payload.repo_url,
            x402_url=payload.x402_url,
        )
    except EcosystemError as exc:
        return json_error_from_platform(exc)
    return _admin_project_json(item)


def admin_ecosystem_delete(request: Request) -> Response | dict:
    """Admin: remove an entry outright (abuse report / owner request)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    slug = request.path_params.get("slug", "").strip().lower()
    if not slug or not review_service.delete_entry(slug):
        return json_error_response(404, "not_found", "No entry for that slug")
    return {"deleted": True, "slug": slug}


def admin_ecosystem_requests_queue(request: Request) -> Response | dict:
    """Admin: the "suggest a change" queue for one status partition."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    from app.modules.ecosystem.models.domain import REQUEST_STATUSES

    status = query_param(request.query_params.get("status", "pending")).strip().lower() or "pending"
    if status not in REQUEST_STATUSES:
        return json_error_response(
            400, "invalid_request", f"status must be one of: {', '.join(REQUEST_STATUSES)}"
        )
    limit = _clamped_limit(request)
    if isinstance(limit, Response):
        return limit
    items = request_service.list_requests(status, limit=limit)
    return {"items": [_request_json(item) for item in items]}


def admin_ecosystem_request_resolve(request: Request) -> Response | dict:
    """Admin: mark a "suggest a change" request resolved or dismissed."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    request_id = request.path_params.get("id", "").strip()
    if not request_id:
        return json_error_response(400, "invalid_request", "id required")
    try:
        payload = serialization.decode(request.body, EcosystemRequestResolveRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    wallet = verified_admin_wallet(request)
    try:
        item = request_service.resolve_request(request_id, wallet=wallet, status=payload.status)
    except EcosystemError as exc:
        return json_error_from_platform(exc)
    return _request_json(item)


def admin_ecosystem_seed(request: Request) -> Response | dict:
    """Admin: one-off, idempotent seed from the crawler's existing ecosystem_listed domains (design doc section 6.3)."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    return review_service.seed_from_ecosystem_listed(limit=settings.ecosystem_seed_max_entries)


def admin_ecosystem_draft_blurb(request: Request) -> Response | dict:
    """Admin: ask workers for a grounded blurb-draft suggestion (design doc section 6.3). Never auto-publishes -- writes only `draft_description`, fire-and-forget."""
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied
    slug = request.path_params.get("slug", "").strip().lower()
    item = review_service.get_entry(slug) if slug else None
    if item is None:
        return json_error_response(404, "not_found", "No entry for that slug")
    enqueued = blurb_service.request_blurb_draft(slug, url=item.url, domain=item.domain)
    return {"enqueued": enqueued, "slug": slug}


def register_ecosystem_routes(app: Router) -> None:
    """Register every public and admin registry route."""
    app.post("/api/v1/ecosystem/submit")(ecosystem_submit)
    app.get("/api/v1/ecosystem/categories")(ecosystem_categories)
    app.get("/api/v1/ecosystem")(ecosystem_list)
    app.get("/api/v1/ecosystem/submissions/:id")(ecosystem_submission_status)
    app.post("/api/v1/ecosystem/:slug/request")(ecosystem_request_submit)
    app.get("/api/v1/ecosystem/:slug/badge.svg")(ecosystem_badge)
    app.get("/api/v1/ecosystem/:slug")(ecosystem_detail)

    app.get("/api/v1/admin/ecosystem")(admin_ecosystem_queue)
    app.post("/api/v1/admin/ecosystem/seed")(admin_ecosystem_seed)
    app.get("/api/v1/admin/ecosystem/requests")(admin_ecosystem_requests_queue)
    app.post("/api/v1/admin/ecosystem/requests/:id/resolve")(admin_ecosystem_request_resolve)
    app.get("/api/v1/admin/ecosystem/:slug")(admin_ecosystem_detail)
    app.post("/api/v1/admin/ecosystem/:slug/decision")(admin_ecosystem_decision)
    app.post("/api/v1/admin/ecosystem/:slug/draft-blurb")(admin_ecosystem_draft_blurb)
    app.patch("/api/v1/admin/ecosystem/:slug")(admin_ecosystem_update)
    app.delete("/api/v1/admin/ecosystem/:slug")(admin_ecosystem_delete)
