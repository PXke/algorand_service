"""HTTP route for the x402 sandboxed file/tarball scan: POST a URL, get a static-analysis report.

Route is /api/v1/x402/scan/url. Body-upload (`POST /scan/file` with the raw
bytes) is deliberately not built in this prototype -- URL-fetch keeps the
byte cap and SSRF pinning in one place (scan_service._fetch_bounded) instead
of needing a second, differently-shaped bound on Falcon's own request body
reader. Direct upload is a reasonable v1 addition once the URL path is
proven.
"""

from __future__ import annotations

import logging

from app.core import serialization
from app.core.config import settings
from app.core.errors import PlatformError
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_response
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.guard import PaymentResult
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request, run_with_refund
from app.modules.x402.promo import promo_request_params
from app.modules.x402_scan.services.concurrency import (
    ConcurrencyLimitError,
    acquire_scan_slot,
    release_scan_slot,
)
from app.modules.x402_scan.services.rate_limit import scan_rate_limited
from app.modules.x402_scan.services.scan_service import FetchError, SandboxError, scan_url
from app.schemas import X402ScanUrlRequest

logger = logging.getLogger(__name__)

_RESOURCE = "x402-scan-url"

_INPUT_EXAMPLE = {"url": "https://example.com/suspicious-download.zip"}
_OUTPUT_EXAMPLE = {
    "source_url": "https://example.com/suspicious-download.zip",
    "download_bytes": 18422,
    "schema_version": 1,
    "status": "ok",
    "one_line_summary": "No concerns found -- no indicators -- file type: Zip archive data",
    "target": {
        "label": "input",
        "size_bytes": 18422,
        "type": "Zip archive data",
        "entropy_bits_per_byte": 7.91,
        "indicators": {"urls": [], "ipv4_addresses": []},
    },
    "clamav": {"engine": "clamdscan", "exit_code": 0, "infected_files": [], "clean": True},
    "archive": {
        "archive_kind": "zip",
        "member_count": 3,
        "declared_uncompressed_bytes": 40200,
        "extraction_skipped_reason": None,
        "clamav": {"engine": "clamdscan", "exit_code": 0, "infected_files": [], "clean": True},
        "members": [],
    },
    "yara": {"matched_rules": 0},
    "fuzzy_hash": {
        "ssdeep_hash": "3:hMCE0O++uV4d3QOMikMR+RFgVn:hu0O0u29MRuFgV",
        "comparison_corpus": None,
        "note": "no known-bad comparison corpus is bundled -- informational only, never contributes to risk_score",
    },
    "risk": {
        "score": 0.0,
        "verdict": "no concerns found",
        "malicious": False,
        "caution_notes": [],
    },
}


def x402_scan_url(request: Request) -> Response:
    """Paid: fetch a URL (bounded, SSRF-safe) and run it through the static-analysis sandbox.

    v0/prototype: static analysis only -- ClamAV signature match, file-type
    check, entropy, embedded URL/IP extraction, and (for zip/tar) a
    bomb-safe member listing with the same checks per member. The target is
    never executed. See sandbox/scan.py for the full tool rationale and
    sandbox_runner.py for the Docker-vs-Firecracker isolation decision.

    Auto-refund (migration 102, owner requirement 2026-09-02): a
    ConcurrencyLimitError/SandboxError used to be charged but unfulfilled
    with no way back for the payer -- this route now refunds either of
    those (or anything else the sandbox raises) via run_with_refund, for a
    REAL payment. A circuit-breaker check runs before the payment gate, so
    a resource with too many recent refund-triggering failures is refused
    before anyone is charged again.

    FetchError is deliberately NOT refunded (found-in-audit gap,
    2026-09-02): the caller supplies `url` themselves, so pointing it at a
    server that refuses connections, times out, or returns a non-200 would
    have been a free, on-demand, unlimited way to trigger refunds and pump
    the circuit breaker -- the exact abuse `run_with_refund`'s PlatformError
    exemption exists to close for every other route's caller-fault case.
    _scan_product_write converts a FetchError into a PlatformError right at
    this boundary so it gets the same payment-kept, no-refund, direct-4xx
    treatment as any other module's ownership/validation rejection, without
    needing FetchError itself to become a PlatformError subclass (it is
    also raised and caught in `_handle_promo_scan` below, unchanged).
    """
    if circuit_breaker.is_tripped(_RESOURCE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    if scan_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many scan requests — please try again later"
        )

    try:
        payload = serialization.decode(request.body, X402ScanUrlRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    url = payload.url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return json_error_response(400, "invalid_request", "url must be http:// or https://")

    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        price=settings.x402_scan_price,
        resource=_RESOURCE,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
        description=(
            "Static security scan of a file fetched from a URL: known-malware "
            "signature match (ClamAV), file-type verification, entropy, embedded "
            "URL/IP extraction, and a zip/tar-bomb-safe archive member listing "
            "with the same checks applied per member. The target is downloaded "
            f"server-side (max {settings.x402_scan_max_download_bytes} bytes) and "
            "scanned in a network-isolated sandbox that never executes it."
        ),
        extensions=describe_json_endpoint(
            input=_INPUT_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "format": "uri"}},
                "required": ["url"],
            },
            output_example=_OUTPUT_EXAMPLE,
            # Without this the package defaults to a query-params discovery
            # extension, which would describe this POST-with-JSON-body route
            # incorrectly (found during a 2026-09-01 discovery audit -- see
            # x402_directory's own x402_list for the same documented pitfall).
            body_type="json",
        ),
    )
    if result.error:
        return result.error

    if result.is_promo:
        return _handle_promo_scan(url, result)

    outcome = run_with_refund(
        result, resource=_RESOURCE, product_write=lambda: _scan_product_write(url), request=request
    )
    if isinstance(outcome, Response):
        # run_with_refund's own failure-path response -- refunded (Concurrency
        # LimitError/SandboxError/anything else _do_scan can raise) or the
        # payment-kept direct 422 (FetchError, converted to a PlatformError by
        # _scan_product_write). Return it directly, do NOT call mark_fulfilled.
        return outcome

    mark_fulfilled(result.payment_txid, resource=_RESOURCE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
    )


def _handle_promo_scan(url: str, result: PaymentResult) -> Response:
    """The promo path: run the scan, but never refund on failure.

    A promo bypass settles nothing -- require_paid_request's promo branch
    returns before record_settlement ever runs, so there is no ledger row
    and nothing to refund here. Keeps the original specific error codes
    (422/503) rather than routing through run_with_refund, which would
    incorrectly claim a refund for a payment that never happened. Extracted
    from x402_scan_url purely to stay under the complexity budget.
    """
    try:
        report = _do_scan(url)
    except ConcurrencyLimitError as exc:
        logger.warning(
            "x402 scan: concurrency limit rejected a promo request (url=%r): %s", url, exc
        )
        response = json_error_response(
            503, "scan_unavailable", "Scan capacity is temporarily full — please retry shortly"
        )
        response.headers.update(result.settlement_headers)
        return response
    except FetchError as exc:
        logger.warning("x402 scan: fetch failed for a promo request (url=%r): %s", url, exc)
        response = json_error_response(422, "fetch_failed", str(exc))
        response.headers.update(result.settlement_headers)
        return response
    except SandboxError as exc:
        logger.error("x402 scan: sandbox failed for a promo request (url=%r): %s", url, exc)
        response = json_error_response(
            503, "scan_unavailable", "Scan is temporarily unavailable — please retry"
        )
        response.headers.update(result.settlement_headers)
        return response
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**report, "settlement_tx_id": "", "via": "promo"}),
    )


def _do_scan(url: str) -> dict[str, object]:
    """The product write: acquire a scan slot, run the sandboxed scan, release the slot.

    acquire_scan_slot() raising ConcurrencyLimitError means no slot was ever
    held, so there is nothing to release. Once a slot is acquired, it is
    always released via `finally`, whether scan_url succeeds or raises
    FetchError/SandboxError -- same nesting the route used before the
    refund retrofit, just extracted so both the promo and real-payment
    branches share one implementation.
    """
    acquire_scan_slot()
    try:
        return scan_url(url)
    finally:
        release_scan_slot()


def _scan_product_write(url: str) -> dict[str, object]:
    """The real-payment product write run_with_refund protects: `_do_scan`, with FetchError re-raised as a PlatformError so it is never refunded.

    See x402_scan_url's own docstring ("FetchError is deliberately NOT
    refunded") for why: `url` is caller-supplied, so a fetch failure is
    100% caller-controlled and must get the same payment-kept, no-refund
    treatment every other module's own PlatformError gets from
    run_with_refund -- ConcurrencyLimitError/SandboxError are OUR faults
    and fall through unchanged to the refund path.
    """
    try:
        return _do_scan(url)
    except FetchError as exc:
        raise PlatformError("fetch_failed", str(exc), http_status=422) from exc


def register_x402_scan_routes(app: Router) -> None:
    """Register the one prototype route: paid URL scan."""
    app.post("/api/v1/x402/scan/url")(x402_scan_url)
