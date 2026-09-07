"""HTTP routes for the x402 uptime/reachability check and its history read.

POST /api/v1/x402/uptime/check does one on-demand, SSRF-guarded reachability
check plus up to `settings.x402_uptime_percentile_samples` back-to-back
samples of the SAME target within that one paid call, so it can also report
latency percentiles (p50/p90/p99) -- see services/percentiles.py for why
this needed no new persistent storage.

GET /api/v1/x402/uptime/history is the separate, genuinely-stateful feature:
aggregated uptime % and latency percentiles across MANY of this product's
own past real checks for one url, over a caller-chosen window -- see
services/history_service.py and stores/ (migration 117).

See docs/x402-uptime-check-design.md for the original design (SSRF guard
reuse, no-body-download, asymmetric cache TTL, two-dimension rate limiting,
why "down" is a normal billable answer rather than a refunded failure) --
percentiles and history are additions on top of that design, not a rewrite
of it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core import serialization
from app.core.config import settings
from app.core.errors import PlatformError
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.guard import PaymentResult
from app.modules.x402.paid_request import (
    challenge_if_unpaid,
    mark_fulfilled,
    require_paid_request,
    run_with_refund,
)
from app.modules.x402.preview import preview_requested
from app.modules.x402.promo import promo_request_params
from app.modules.x402_directory.services.listing_service import normalize_url
from app.modules.x402_uptime.services.cache import get_cached, is_fresh, set_cached
from app.modules.x402_uptime.services.checker import UptimeResult
from app.modules.x402_uptime.services.history_service import HistoryService
from app.modules.x402_uptime.services.percentiles import compute_percentiles, sample_target
from app.modules.x402_uptime.services.rate_limit import (
    ip_rate_limited,
    target_key_for,
    target_over_budget,
)
from app.schemas import X402UptimeCheckRequest

logger = logging.getLogger(__name__)

_RESOURCE = "x402-uptime-check"
_HISTORY_RESOURCE = "x402-uptime-history"

# Module-level, like x402_directory/api/routes.py's own `listing_service` --
# defaults to the process-wide store built from settings.x402_uptime_history_store;
# tests monkeypatch this attribute to a HistoryService wrapping an
# InMemoryUptimeHistoryStore.
history_service = HistoryService()

_INPUT_EXAMPLE = {"url": "https://example.com"}
_OUTPUT_EXAMPLE = {
    "target_url": "https://example.com/",
    "final_url": "https://example.com/",
    "checked_at": "2026-09-04T12:00:00Z",
    "cache": "hit",
    "cached_at": "2026-09-04T11:58:30Z",
    "reachable": True,
    "http_status": 200,
    "response_time_ms": 143,
    "latency_samples": 5,
    "latency_min_ms": 120,
    "latency_p50_ms": 140,
    "latency_p90_ms": 165,
    "latency_p99_ms": 165,
    "latency_max_ms": 165,
    "redirect_chain": ["https://example.com/"],
    "resolved_ip": "93.184.216.34",
    "error": "",
}


class TargetBudgetExhaustedError(Exception):
    """No cached result exists yet for this target and its real-fetch budget is exhausted this hour.

    OUR capacity limit, not the caller's fault -- see rate_limit.py's
    target_over_budget docstring and docs/x402-uptime-check-design.md's "what
    happens when the per-target budget is exhausted." A real (non-promo)
    request raising this is caught by run_with_refund and refunded; the
    circuit breaker check before require_paid_request means a resource
    tripping this repeatedly gets refused before further money is at risk.
    """


def x402_uptime_check(request: Request) -> Response:
    """Paid: check whether a caller-supplied URL is reachable from our servers.

    "Down" is a normal, fully-paid-for answer, not a failure -- see the
    design doc for why this diverges from x402_scan's own FetchError
    handling. The only refunded failure mode is TargetBudgetExhaustedError
    (our own per-target rate limiter has no cached fallback to serve yet).
    """
    if circuit_breaker.is_tripped(_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    if ip_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many uptime-check requests — please try again later"
        )

    offer = {
        "price": settings.x402_uptime_price,
        "resource": _RESOURCE,
        "description": (
            "Reachability check of a caller-supplied URL from our servers: HTTP status, "
            "response time, redirect chain and resolved IP, plus latency percentiles "
            f"(p50/p90/p99) computed over up to {settings.x402_uptime_percentile_samples} "
            "back-to-back samples of the same target taken within this one call. Never "
            "downloads the response body, never forwards caller-supplied headers to the "
            "target, SSRF-guarded on every hop. Heavily cached per target and "
            "rate-limited per caller IP and per target host — a 'down' result is a "
            "normal, fully-charged answer, the same as 'up'. See "
            "GET /api/v1/x402/uptime/history for aggregated uptime % and latency "
            "percentiles across many of THIS product's own past checks over a longer "
            "window. Supports ?preview=true: a REAL check through the same "
            "SSRF-guarded, cached, budget-limited path, unpaid and rate-limited, taking "
            "exactly one real sample (never the full multi-sample percentile "
            "computation a paid call takes) with the exact latency figures redacted."
        ),
        "extensions": describe_json_endpoint(
            input=_INPUT_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "format": "uri"}},
                "required": ["url"],
            },
            output_example=_OUTPUT_EXAMPLE,
            body_type="json",
        ),
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid); with a payment attached, the url is still
    # validated before the gate so a malformed request is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    try:
        payload = serialization.decode(request.body, X402UptimeCheckRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        normalized_url = normalize_url(payload.url)
    except PlatformError as exc:
        return json_error_from_platform(exc)

    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        **offer,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
        preview=preview_requested(request),
    )
    if result.error:
        return result.error

    if result.is_preview:
        return _handle_preview_check(normalized_url)

    if result.is_promo:
        return _handle_promo_check(normalized_url, result)

    outcome = run_with_refund(
        result,
        resource=_RESOURCE,
        product_write=lambda: _uptime_product_write(normalized_url),
        request=request,
    )
    if isinstance(outcome, Response):
        # run_with_refund's own failure-path response (TargetBudgetExhaustedError,
        # refunded). Return it directly, do NOT call mark_fulfilled.
        return outcome

    mark_fulfilled(result.payment_txid, resource=_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
    )


def _handle_preview_check(normalized_url: str) -> Response:
    """The `?preview=true` path: a REAL check through `_uptime_product_write`, latency redacted.

    Unlike x402_scan_url's preview (a real scan is expensive and touches an
    outbound fetch of a caller-supplied URL run through a full sandbox), a
    reachability check is exactly the "cheap, harmless GET/HEAD" this route
    already does for every paid call -- so the preview reuses the identical
    product-write function rather than a second, differently-guarded code
    path. That means it inherits the SAME SSRF guard (normalize_url's own
    validation plus check_target's own DNS-pinned fetch), the same per-target
    cache, and the same hourly per-target budget as a real payment -- there
    is no preview-only bypass of any of those. Only the exact latency
    figures are redacted (the numbers an SLA-monitoring customer is
    actually paying for); reachability, HTTP status, redirect chain and
    resolved IP are served for real, same "genuinely useful
    try-before-you-pay" judgment call CLAUDE.md's own worked examples
    (ping/news_search) make elsewhere.

    On a genuine cache miss this passes `max_samples=1` down to
    `_uptime_product_write`, so a preview NEVER runs the full
    up-to-`x402_uptime_percentile_samples` multi-sample fetch a paid call
    can -- a preview that cost the same real-fetch amplification as a paid
    call would let an unauthenticated caller trigger repeated multi-hit
    traffic against a third-party URL for free, defeating the point of
    "preview is a cheap dry run" (fixed 2026-09-06; a same-night review
    caught this taking the full sample count). A cache hit or a
    budget-exhausted stale-cache serve is unaffected either way -- neither
    ever takes a new sample.

    Settles nothing, so a TargetBudgetExhaustedError (OUR OWN capacity
    limit, not the caller's fault) is served as a plain 503 with no refund
    attempt -- same "nothing was ever paid, nothing to refund" reasoning as
    `_handle_promo_check` below.
    """
    try:
        report = _uptime_product_write(normalized_url, max_samples=1)
    except TargetBudgetExhaustedError as exc:
        logger.warning(
            "x402 uptime check: target budget exhausted for a preview request (url=%r): %s",
            normalized_url,
            exc,
        )
        return json_error_response(
            503,
            "uptime_check_unavailable",
            "This target's check budget is exhausted for now — please retry shortly",
        )
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        description=serialization.dumps(
            {
                **report,
                "response_time_ms": -1,
                "latency_min_ms": -1,
                "latency_p50_ms": -1,
                "latency_p90_ms": -1,
                "latency_p99_ms": -1,
                "latency_max_ms": -1,
                "settlement_tx_id": "<preview>",
            }
        ),
    )


def _handle_promo_check(normalized_url: str, result: PaymentResult) -> Response:
    """The promo path: run the check, but never refund on failure (nothing was ever paid)."""
    try:
        report = _uptime_product_write(normalized_url)
    except TargetBudgetExhaustedError as exc:
        logger.warning(
            "x402 uptime check: target budget exhausted for a promo request (url=%r): %s",
            normalized_url,
            exc,
        )
        response = json_error_response(
            503,
            "uptime_check_unavailable",
            "This target's check budget is exhausted for now — please retry shortly",
        )
        response.headers.update(result.settlement_headers)
        return response
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**report, "settlement_tx_id": "", "via": "promo"}),
    )


def _uptime_product_write(
    normalized_url: str, *, max_samples: int | None = None
) -> dict[str, object]:
    """The product write: serve a fresh cache hit, else a real check (budget permitting), else stale.

    Cache hits never touch the per-target counter (see rate_limit.py's own
    docstring on why that matters for the DDoS defense this composes into).
    Raises TargetBudgetExhaustedError only when there is truly nothing
    cached yet to fall back to. A cache hit or a budget-exhausted stale
    serve reuses the ORIGINAL real check's percentile samples (stored
    alongside it, see cache.py's CachedCheck.latency_samples_ms) rather than
    a single point -- neither path ever calls sample_target() or writes a
    history row, since neither is a NEW measurement (see
    models/domain.StoredUptimeCheck's own docstring).

    `max_samples` is forwarded to `sample_target()` on a genuine cache MISS
    only -- `_handle_preview_check` passes `max_samples=1` so a preview
    never takes more than one real sample regardless of
    `settings.x402_uptime_percentile_samples`; the real paid path omits it
    (`None`), which leaves `sample_target()`'s own default (the configured
    setting) in effect. Has no effect on a cache hit or a stale serve --
    neither takes a new sample at all.
    """
    cached = get_cached(normalized_url)
    if cached is not None and is_fresh(cached):
        return _serialize(
            normalized_url,
            cached.result,
            cache="hit",
            cached_at=cached.cached_at,
            latency_samples_ms=cached.latency_samples_ms,
        )

    if target_over_budget(target_key_for(normalized_url)):
        if cached is not None:
            return _serialize(
                normalized_url,
                cached.result,
                cache="stale",
                cached_at=cached.cached_at,
                latency_samples_ms=cached.latency_samples_ms,
            )
        raise TargetBudgetExhaustedError(
            f"no cached result yet for {normalized_url!r} and its check budget is exhausted this hour"
        )

    result, latency_samples_ms = sample_target(normalized_url, samples=max_samples)
    cached_at = set_cached(normalized_url, result, latency_samples_ms)
    history_service.record(normalized_url, result, checked_at_epoch=int(cached_at))
    return _serialize(
        normalized_url,
        result,
        cache="miss",
        cached_at=cached_at,
        latency_samples_ms=latency_samples_ms,
    )


def _serialize(
    normalized_url: str,
    result: UptimeResult,
    *,
    cache: str,
    cached_at: float,
    latency_samples_ms: list[int],
) -> dict[str, object]:
    percentiles = compute_percentiles(latency_samples_ms)
    return {
        "target_url": normalized_url,
        "final_url": result.final_url,
        "checked_at": datetime.now(tz=UTC),
        "cache": cache,
        "cached_at": datetime.fromtimestamp(cached_at, tz=UTC),
        "reachable": result.reachable,
        "http_status": result.http_status,
        "response_time_ms": result.response_time_ms,
        "latency_samples": percentiles["count"],
        "latency_min_ms": percentiles["min"],
        "latency_p50_ms": percentiles["p50"],
        "latency_p90_ms": percentiles["p90"],
        "latency_p99_ms": percentiles["p99"],
        "latency_max_ms": percentiles["max"],
        "redirect_chain": result.redirect_chain,
        "resolved_ip": result.resolved_ip,
        "error": result.error,
    }


def _parse_history_days(request: Request) -> int | Response:
    """The caller's `days`, clamped to [1, x402_uptime_history_max_days]; a 400 Response when non-integer."""
    raw_days = query_param(request.query_params.get("days", ""))
    try:
        requested = int(raw_days) if raw_days else settings.x402_uptime_history_max_days
    except ValueError:
        return json_error_response(400, "invalid_request", "days must be an integer")
    return max(1, min(requested, settings.x402_uptime_history_max_days))


def _history_offer() -> dict[str, object]:
    """The static parts of the history route's 402 offer -- built before url/days are parsed."""
    return {
        "price": settings.x402_uptime_history_price,
        "resource": _HISTORY_RESOURCE,
        "description": (
            "Aggregated uptime % and latency percentiles (p50/p90/p99) for a "
            f"caller-supplied url over up to the last ?days= (capped at "
            f"{settings.x402_uptime_history_max_days}), built only from this product's "
            "own real checks -- never a re-serving of a cached hit. Up to "
            f"{settings.x402_uptime_history_max_results} of the underlying check rows "
            "are returned too, newest first. A frequently-checked target can exhaust "
            "that row cap well before reaching the requested `days` boundary, so the "
            "response reports the real covered span too "
            "(window_days_requested/window_days_actual/truncated/"
            "oldest_checked_at_epoch/newest_checked_at_epoch) rather than assuming the "
            "requested window was fully covered. Supports ?preview=true: a FIXED "
            "example shape, unpaid and rate-limited, never a real query."
        ),
        "extensions": describe_json_endpoint(
            input={"url": _INPUT_EXAMPLE["url"], "days": 30},
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "format": "uri"},
                    "days": {"type": "integer", "minimum": 1},
                },
                "required": ["url"],
            },
            output_example={
                "target_url": "https://example.com/",
                "window_days_requested": 30,
                "window_days_actual": 1.2,
                "truncated": True,
                "oldest_checked_at_epoch": 1757073600,
                "newest_checked_at_epoch": 1757160000,
                "checks_recorded": 42,
                "uptime_pct": 97.62,
                "latency_p50_ms": 120,
                "latency_p90_ms": 250,
                "latency_p99_ms": 400,
                "latency_min_ms": 90,
                "latency_max_ms": 410,
                "history": [
                    {
                        "checked_at_epoch": 1757160000,
                        "reachable": True,
                        "http_status": 200,
                        "response_time_ms": 120,
                        "error": "",
                    }
                ],
            },
            # No body_type: this is a GET whose input is query params
            # (url/days), not a POST/PUT/PATCH body -- see
            # describe_json_endpoint's own docstring for why passing
            # body_type="json" here would fail the facilitator's
            # validate_discovery_extension for a GET route (found by
            # tests/test_x402_bazaar_extension_sweep.py).
        ),
    }


def _parse_history_request(request: Request) -> tuple[str, int] | Response:
    """The caller's (normalized_url, days), or a 400 Response for a malformed url or days."""
    raw_url = query_param(request.query_params.get("url", ""))
    if not raw_url:
        return json_error_response(400, "invalid_request", "url is required")
    try:
        normalized_url = normalize_url(raw_url)
    except PlatformError as exc:
        return json_error_from_platform(exc)

    days = _parse_history_days(request)
    if isinstance(days, Response):
        return days
    return normalized_url, days


def _handle_history_promo(normalized_url: str, days: int, result: PaymentResult) -> Response:
    """The promo path: a real (non-redacted) read that settles nothing."""
    report = history_service.read(normalized_url, days=days)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**report, "settlement_tx_id": "", "via": "promo"}),
    )


def _history_preview_response(days: int) -> Response:
    """A FIXED, obviously-fake preview shape.

    See x402_uptime_history's own docstring for why a real read is never
    served here, paid or not.
    """
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json"},
        description=serialization.dumps(
            {
                "target_url": "<preview>",
                "window_days_requested": days,
                "window_days_actual": -1,
                "truncated": False,
                "oldest_checked_at_epoch": None,
                "newest_checked_at_epoch": None,
                "checks_recorded": -1,
                "uptime_pct": None,
                "latency_p50_ms": -1,
                "latency_p90_ms": -1,
                "latency_p99_ms": -1,
                "latency_min_ms": -1,
                "latency_max_ms": -1,
                "history": [],
                "settlement_tx_id": "<preview>",
            }
        ),
    )


def x402_uptime_history(request: Request) -> Response:
    """Paid: aggregated uptime % and latency percentiles for a caller url over the last N days.

    Built ONLY from this product's own real (cache-miss) checks recorded to
    x402_uptime_checks_by_url (migration 117) -- see StoredUptimeCheck's own
    docstring for why a cache hit or a stale-serve never contributes a row.

    PRICED, unlike the directory's free x402_probe_history_max_results read:
    that data is free because the workers probe fleet produces it anyway, on
    a fixed schedule, for LISTED endpoints only, at no incremental cost per
    read. THIS history is built entirely from rows that were each paid for
    once already (a real /uptime/check call) against an ARBITRARY caller
    url -- serving it back for free would let anyone read a url's recent
    check history without ever paying for a check themselves, undercutting
    the live check product for any url someone else already checked
    recently. See settings.x402_uptime_history_price's own comment.

    ?preview=true is a FIXED example shape, unpaid, rate-limited -- NOT a
    real query (unlike the check route's own preview, which IS a real
    check): a real preview read here would BE the exact free-rider
    substitution this route is priced to prevent, so there is no
    "genuinely useful real read" version of preview available for it.
    """
    if circuit_breaker.is_tripped(_HISTORY_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    if ip_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many uptime-check requests — please try again later"
        )

    offer = _history_offer()
    # An unpaid request sees the offer before url/days are ever parsed (see
    # challenge_if_unpaid) -- same fix as x402_probe_leaderboard's own
    # 2026-09-06 bug (a bare header-less probe with a malformed query param
    # got a 400 and never saw the price). With a payment attached, url and
    # days are still validated before the gate so a malformed request is
    # never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    parsed = _parse_history_request(request)
    if isinstance(parsed, Response):
        return parsed
    normalized_url, days = parsed

    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        **offer,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
        preview=preview_requested(request),
    )
    if result.error:
        return result.error

    if result.is_preview:
        return _history_preview_response(days)

    if result.is_promo:
        return _handle_history_promo(normalized_url, days, result)

    def _build_history() -> dict[str, object]:
        return history_service.read(normalized_url, days=days)

    outcome = run_with_refund(
        result,
        resource=_HISTORY_RESOURCE,
        product_write=_build_history,
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_HISTORY_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
    )


def register_x402_uptime_routes(app: Router) -> None:
    """Register both routes: the paid check, and its paid history read.

    x402-marketplace-ux-audit.md section 3.3 "trust / uptime": `check` is
    pluralized to `checks` (matching the collection-noun convention) and
    `history` is folded into the same collection name -- both new paths are
    second, direct registrations against the identical old handlers; the old
    paths are never removed (section 3.5, live-mainnet callers).
    """
    app.post("/api/v1/x402/uptime/check")(x402_uptime_check)
    app.post("/api/v1/x402/uptime/checks")(x402_uptime_check)
    app.get("/api/v1/x402/uptime/history")(x402_uptime_history)
    app.get("/api/v1/x402/uptime/checks")(x402_uptime_history)
