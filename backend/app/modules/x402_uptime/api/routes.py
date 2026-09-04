"""HTTP route for the x402 uptime/reachability check: POST a URL, get back whether it's up.

Route is /api/v1/x402/uptime/check. See docs/x402-uptime-check-design.md for
the full design (SSRF guard reuse, no-body-download, asymmetric cache TTL,
two-dimension rate limiting, why "down" is a normal billable answer rather
than a refunded failure).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from app.core import serialization
from app.core.config import settings
from app.core.errors import PlatformError
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.guard import PaymentResult
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request, run_with_refund
from app.modules.x402.promo import promo_request_params
from app.modules.x402_directory.services.listing_service import normalize_url
from app.modules.x402_uptime.services.cache import get_cached, is_fresh, set_cached
from app.modules.x402_uptime.services.checker import UptimeResult, check_target
from app.modules.x402_uptime.services.rate_limit import (
    ip_rate_limited,
    target_key_for,
    target_over_budget,
)
from app.schemas import X402UptimeCheckRequest

logger = logging.getLogger(__name__)

_RESOURCE = "x402-uptime-check"

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
        price=settings.x402_uptime_price,
        resource=_RESOURCE,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
        description=(
            "Reachability check of a caller-supplied URL from our servers: HTTP status, "
            "response time, redirect chain and resolved IP. Never downloads the response "
            "body, never forwards caller-supplied headers to the target, SSRF-guarded on "
            "every hop. Heavily cached per target and rate-limited per caller IP and per "
            "target host — a 'down' result is a normal, fully-charged answer, the same as "
            "'up'."
        ),
        extensions=describe_json_endpoint(
            input=_INPUT_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "format": "uri"}},
                "required": ["url"],
            },
            output_example=_OUTPUT_EXAMPLE,
            body_type="json",
        ),
    )
    if result.error:
        return result.error

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


def _uptime_product_write(normalized_url: str) -> dict[str, object]:
    """The product write: serve a fresh cache hit, else a real check (budget permitting), else stale.

    Cache hits never touch the per-target counter (see rate_limit.py's own
    docstring on why that matters for the DDoS defense this composes into).
    Raises TargetBudgetExhaustedError only when there is truly nothing
    cached yet to fall back to.
    """
    cached = get_cached(normalized_url)
    if cached is not None and is_fresh(cached):
        return _serialize(normalized_url, cached.result, cache="hit", cached_at=cached.cached_at)

    if target_over_budget(target_key_for(normalized_url)):
        if cached is not None:
            return _serialize(
                normalized_url, cached.result, cache="stale", cached_at=cached.cached_at
            )
        raise TargetBudgetExhaustedError(
            f"no cached result yet for {normalized_url!r} and its check budget is exhausted this hour"
        )

    result = check_target(
        normalized_url,
        timeout_s=settings.x402_uptime_check_timeout_s,
        max_redirects=settings.x402_uptime_max_redirects,
    )
    cached_at = set_cached(normalized_url, result)
    return _serialize(normalized_url, result, cache="miss", cached_at=cached_at)


def _serialize(
    normalized_url: str, result: UptimeResult, *, cache: str, cached_at: float
) -> dict[str, object]:
    return {
        "target_url": normalized_url,
        "final_url": result.final_url,
        "checked_at": datetime.now(tz=UTC),
        "cache": cache,
        "cached_at": datetime.fromtimestamp(cached_at, tz=UTC),
        "reachable": result.reachable,
        "http_status": result.http_status,
        "response_time_ms": result.response_time_ms,
        "redirect_chain": result.redirect_chain,
        "resolved_ip": result.resolved_ip,
        "error": result.error,
    }


def register_x402_uptime_routes(app: Router) -> None:
    """Register the one prototype route: paid uptime/reachability check."""
    app.post("/api/v1/x402/uptime/check")(x402_uptime_check)
