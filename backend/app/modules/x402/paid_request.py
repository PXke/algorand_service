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

Fulfillment contract every paid route MUST follow
-------------------------------------------------
By the time require_paid_request returns with `.error is None` the payer has
been charged and the ledger row exists with `fulfilled=False`. The route's
product write happens after that, so a route looks like::

    result = require_paid_request(request, price=..., resource=RESOURCE)
    if result.error is not None:
        return result.error
    stored = service.store(...)                # the product write, may raise
    mark_fulfilled(result.payment_txid, resource=RESOURCE)
    return response(stored, headers=result.settlement_headers)

1. Call `settlement.mark_fulfilled` ONLY after the product write has
   durably succeeded -- store before mark (CLAUDE.md section 2 invariant 2).
   Never before, never in a `finally`.
2. Do not swallow the product write's exception to "keep the response
   going": let it propagate to the 500 handler. The ledger row then stays
   `fulfilled=False`, which is the durable record that this payment bought
   nothing -- see x402_settlements_by_tx (migration 095) for the per-txid
   point read an operator reconciles from.
3. Do NOT release the replay claim on a fulfillment failure. The payment is
   committed on-chain, so the facilitator cannot settle the same header
   again -- releasing would only turn a retry's 409 into a 402 -- and an
   un-claimed header is exactly the double-fulfillment window this design
   closes. A retry after a fulfillment failure is a reconciliation, not a
   new request.
4. mark_fulfilled never raises and is idempotent; a route does not need to
   guard it. It logs at ERROR with the txid if the flip itself fails.
"""

from __future__ import annotations

import logging
from typing import Any

from x402.http.constants import PAYMENT_SIGNATURE_HEADER

from app.core.http import Request
from app.core.http_errors import json_error_response
from app.core.request_headers import header_value
from app.modules.x402.guard import PaymentResult, require_payment
from app.modules.x402.preview import preview_rate_limited
from app.modules.x402.promo import attempt_promo_redemption
from app.modules.x402.replay import claim_payment, release_claim
from app.modules.x402.settlement import SettlementStore, mark_fulfilled, record_settlement

logger = logging.getLogger(__name__)

# mark_fulfilled is re-exported so a route imports its whole paid-request
# contract from one module (see the module docstring).
__all__ = ["mark_fulfilled", "require_paid_request"]


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
    resource_path: str | None = None,
    settlement_store: SettlementStore | None = None,
    preview: bool = False,
    promo_code: str = "",
    promo_wallet: str = "",
) -> PaymentResult:
    """Run the shared payment gate with replay protection and settlement logging.

    Returns the same PaymentResult shape require_payment does, so a handler
    reads identically: check `.error`, then use `.payer` / `.settlement_headers`.
    On success the ledger row is written as unfulfilled; the route calls
    mark_fulfilled(result.payment_txid, ...) after its product write.

    Every new kwarg below defaults to off, so a caller that never passes them
    gets byte-for-byte the same behaviour as before they existed.

    `preview`: True bypasses the gate entirely (see guard.require_payment's
    own `preview` kwarg) after checking the shared preview rate limit — a
    preview call is still a real request even though nothing is charged
    (modules/x402/preview.py). The route computes this itself, typically via
    `preview.preview_requested(request)` on a `?preview=true` query param,
    and must serve a REDACTED response when `result.is_preview` comes back
    True, and must NEVER call mark_fulfilled on that result (there is
    nothing to mark — see settlement.mark_fulfilled's own no-op guard for an
    empty txid). Checked BEFORE promo: a preview call never spends a promo
    redemption.

    `promo_code` / `promo_wallet`: an attempted promo-code bypass, checked
    BEFORE the payment gate (modules/x402/promo.py). The route computes
    these itself, typically via `promo.promo_request_params(request)` on
    `?promo=` / `?promo_wallet=` query params. ANY redemption failure (bad
    code, wrong resource, exhausted, expired, already redeemed by this
    wallet, an invalid wallet, a rate-limited IP, or Redis/Cassandra being
    unreachable) falls straight through to the normal payment gate below —
    it is never a 402/error the caller cannot route around. On success the
    route serves its REAL response (a promo bypasses payment, not product
    quality) and must NEVER call mark_fulfilled on the result either (same
    no-settlement reason as preview).
    """
    if preview:
        if preview_rate_limited(request):
            return PaymentResult(
                error=json_error_response(
                    429,
                    "rate_limited",
                    "Too many preview requests — please try again later",
                )
            )
        return require_payment(request, price=price, resource=resource, preview=True)

    if promo_code:
        promo_result = attempt_promo_redemption(
            request, code=promo_code, wallet=promo_wallet, resource=resource
        )
        if promo_result is not None:
            return promo_result

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
        resource_path=resource_path,
    )
    if result.error is not None:
        # Nothing settled, so the header was never spent — free the claim rather
        # than locking a payer out of retrying after a transient failure.
        release_claim(claim_key)
        return result

    record_settlement(result, resource=resource, store=settlement_store)
    return result
