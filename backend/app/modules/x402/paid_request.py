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
   point read an operator reconciles from. **Unless the route opts into
   `run_with_refund` below, which replaces this rule for that route only.**
3. Do NOT release the replay claim on a fulfillment failure. The payment is
   committed on-chain, so the facilitator cannot settle the same header
   again -- releasing would only turn a retry's 409 into a 402 -- and an
   un-claimed header is exactly the double-fulfillment window this design
   closes. A retry after a fulfillment failure is a reconciliation, not a
   new request.
4. mark_fulfilled never raises and is idempotent; a route does not need to
   guard it. It logs at ERROR with the txid if the flip itself fails.

Opt-in: auto-refund on product-write failure (migration 102)
--------------------------------------------------------------
A route for an endpoint we fully control (not a third-party listing) can
choose to refund the payer instead of leaving rule 2 above to a human
operator's manual reconciliation -- owner requirement 2026-09-02: "not a
500, not a 400 -- a 200 or your money back." Opt in by wrapping the product
write in `run_with_refund` instead of calling it directly::

    result = require_paid_request(request, price=..., resource=RESOURCE)
    if result.error is not None:
        return result.error
    outcome = run_with_refund(result, resource=RESOURCE, product_write=lambda: service.store(...))
    if isinstance(outcome, Response):
        return outcome          # refunded (or refund attempted) -- do NOT call mark_fulfilled
    mark_fulfilled(result.payment_txid, resource=RESOURCE)
    return response(outcome, headers=result.settlement_headers)

The caller MUST check `circuit_breaker.is_tripped(resource)` itself, BEFORE
ever calling `require_paid_request` -- a tripped resource must be refused
before the payment gate, so no further money is ever at risk while tripped
(see modules/x402/circuit_breaker.py). `run_with_refund` itself only handles
what happens once payment has already succeeded.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, TypeVar

from x402.http.constants import PAYMENT_SIGNATURE_HEADER

from app.core.errors import PlatformError
from app.core.http import Request, Response
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.request_headers import header_value
from app.modules.x402 import circuit_breaker
from app.modules.x402.guard import PaymentResult, require_payment
from app.modules.x402.preview import preview_rate_limited
from app.modules.x402.promo import attempt_promo_redemption
from app.modules.x402.refund import send_refund
from app.modules.x402.replay import claim_payment, release_claim
from app.modules.x402.settlement import (
    SettlementStore,
    mark_fulfilled,
    record_refund,
    record_settlement,
)

_T = TypeVar("_T")

logger = logging.getLogger(__name__)

# mark_fulfilled is re-exported so a route imports its whole paid-request
# contract from one module (see the module docstring).
__all__ = ["mark_fulfilled", "require_paid_request", "run_with_refund"]


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


def run_with_refund(
    result: PaymentResult,
    *,
    resource: str,
    product_write: Callable[[], _T],
    settlement_store: SettlementStore | None = None,
) -> _T | Response:
    """Run `product_write()`; on an unexpected exception, refund the payer instead of leaving a 500. A `PlatformError` (any module's own business-rejection exception -- DirectoryError, BoardError, GradingError, FeatureError, ...) is a DIFFERENT thing and is never refunded -- see below.

    See this module's own docstring ("Opt-in: auto-refund on product-write
    failure") for the full contract and the exact call shape a route uses.
    `result` must be a real, non-preview, non-promo PaymentResult from
    `require_paid_request` with `.error is None` -- this function does not
    check that itself, the same way a route calling mark_fulfilled directly
    does not either.

    On success: returns `product_write()`'s return value untouched. The
    caller still calls `mark_fulfilled` itself afterward -- this function
    never does, so the existing store-before-mark contract is unchanged.

    On a `PlatformError` (found-in-audit gap, 2026-09-02): every module in
    this codebase already raises its own PlatformError subclass for a
    business rejection the payer directly caused -- most commonly an
    ownership conflict (relisting/renewing something you don't own). That
    is payment-kept-but-refused, the SAME "settles but is refused" contract
    this marketplace already documents on every owner-only renew route --
    NOT a delivery failure of ours. Two of the six routes retrofitted onto
    this mechanism the same day independently made OPPOSITE calls about
    whether that case should refund (one did, one didn't), which is exactly
    the kind of drift a shared primitive should make impossible rather than
    leaving every route to decide alone. So: a PlatformError is now ALWAYS
    the payment-kept, no-refund, no-breaker-touch, direct-4xx path, for
    every current and future caller of this function, via
    `json_error_from_platform`. A route with a real caller-fault rejection
    (ownership, an unattributable payer, etc.) can now just let it raise
    from inside `product_write` instead of hand-checking the condition
    again before calling this function.

    On any OTHER exception: caught (never propagates), a refund is
    attempted from the dedicated refund wallet for the FULL settled amount,
    the outcome is recorded on the settlement ledger via `record_refund`,
    and a Response is returned for the caller to return directly instead of
    letting the exception propagate to the generic 500 handler -- this
    REPLACES rule 2 of this module's fulfillment contract for a route that
    opts in; a route that never calls this function keeps the old
    let-it-propagate-to-500 behaviour unchanged. Also increments the
    resource's circuit-breaker failure counter (modules/x402/
    circuit_breaker.py) -- the caller is responsible for checking
    `circuit_breaker.is_tripped(resource)` BEFORE the payment gate on a
    future request; this function does not check it, since by the time it
    runs payment has already happened for THIS request.

    Never raises.
    """
    try:
        return product_write()
    except PlatformError as exc:
        response = json_error_from_platform(exc)
        response.headers.update(result.settlement_headers)
        return response
    except Exception:
        logger.exception(
            "x402 product write failed after payment settled for resource=%s tx_id=%s "
            "payer=%s -- attempting refund",
            resource,
            result.payment_txid,
            result.payer,
        )
        circuit_breaker.record_refund_failure(resource)

        refund = send_refund(
            receiver=result.payer or "",
            amount_atomic=result.amount_atomic or "",
            asset_id=result.asset_id or "",
        )
        record_refund(
            result.payment_txid,
            refund_tx_id=refund.txid,
            refund_status=refund.status,
            resource=resource,
            store=settlement_store,
        )

        if refund.status == "sent":
            response = json_error_response(
                503,
                "product_failed_refunded",
                "This request failed after payment. The full amount has been refunded "
                f"(refund transaction {refund.txid}).",
            )
            response.headers.update(result.settlement_headers)
            return response

        if refund.status == "sent_unconfirmed":
            # Broadcast succeeded, confirmation just could not be verified in
            # time -- the money almost certainly moved. Tell the payer the
            # real txid (not a bare "pending" reference) so THEY can also
            # check it on-chain, same transparency the "sent" branch gives.
            response = json_error_response(
                503,
                "product_failed_refunded",
                "This request failed after payment. A refund was submitted "
                f"(refund transaction {refund.txid}); if it has not landed shortly, "
                "this is your reconciliation reference.",
            )
            response.headers.update(result.settlement_headers)
            return response

        logger.error(
            "x402 REFUND DID NOT SUCCEED after a product-write failure — payer may still be "
            "out of pocket, reconcile by hand: tx_id=%s resource=%s payer=%s amount_atomic=%s "
            "asset_id=%s refund_status=%s refund_error=%s",
            result.payment_txid,
            resource,
            result.payer,
            result.amount_atomic,
            result.asset_id,
            refund.status,
            refund.error,
        )
        response = json_error_response(
            503,
            "product_failed_refund_pending",
            "This request failed after payment. A refund is being processed; if you do not "
            f"receive it, this is your reconciliation reference: {result.payment_txid or ''}",
        )
        response.headers.update(result.settlement_headers)
        return response
