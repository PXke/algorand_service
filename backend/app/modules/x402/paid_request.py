"""The canonical entry point for a paid x402 route.

The payment gate plus replay protection plus settlement logging, in one
call. Every paid route in every module should call require_paid_request, not
the bare require_payment in guard.py -- that stays the low-level primitive
this wraps.

Moved out of x402_directory 2026-08-30 (was require_paid_request there,
directory-specific in name only): every future paid module needs the
identical replay-then-gate-then-ledger sequence, so this is where it
actually belongs. See modules/x402/replay.py and settlement.py for the two
pieces this composes.
"""

from __future__ import annotations

import logging
from typing import Any

from x402.http.constants import PAYMENT_SIGNATURE_HEADER

from app.core.http import Request
from app.core.http_errors import json_error_response
from app.core.request_headers import header_value
from app.modules.x402.guard import PaymentResult, require_payment
from app.modules.x402.replay import claim_payment, release_claim
from app.modules.x402.settlement import SettlementStore, record_settlement

logger = logging.getLogger(__name__)


def _payment_header(request: Request) -> str:
    """The incoming payment header, read under the same name the x402 package reads."""
    return header_value(request.headers, PAYMENT_SIGNATURE_HEADER)


def require_paid_request(
    request: Request,
    *,
    price: str,
    resource: str,
    description: str | None = None,
    extensions: dict[str, Any] | None = None,
    settlement_store: SettlementStore | None = None,
) -> PaymentResult:
    """Run the shared payment gate with replay protection and settlement logging.

    Returns the same PaymentResult shape require_payment does, so a handler
    reads identically: check `.error`, then use `.payer` / `.settlement_headers`.
    """
    header = _payment_header(request)
    claim_key, already_seen = claim_payment(header)
    if already_seen:
        logger.warning("x402 replayed payment header rejected for resource %s", resource)
        return PaymentResult(
            error=json_error_response(
                409,
                "payment_replayed",
                "This payment header has already been used. Submit a new payment.",
            )
        )

    result = require_payment(
        request,
        price=price,
        resource=resource,
        description=description,
        extensions=extensions,
    )
    if result.error is not None:
        # Nothing settled, so the header was never spent — free the claim rather
        # than locking a payer out of retrying after a transient failure.
        release_claim(claim_key)
        return result

    record_settlement(result, resource=resource, store=settlement_store)
    return result
