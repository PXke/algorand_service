"""Paid-route wrapper around require_payment: replay protection + settlement ledger.

require_payment (modules/x402/guard.py) stays the single payment gate — this
adds the two things CLAUDE.md section 9 requires around it without copying any
of its logic:

1. A replay claim on the payment header, taken BEFORE the gate runs, so an
   already-spent header can never reach the facilitator's settle a second time.
2. A settlement ledger row written AFTER a successful settle.

Both are deliberately fail-soft. Once the facilitator has settled, the payer
has been charged, and no bookkeeping or Redis problem on our side may turn that
into a dropped response — the paid work is served and the failure is logged
loudly enough to reconstruct the ledger row from the log line.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

from x402.http.constants import PAYMENT_SIGNATURE_HEADER

from app.core.config import settings
from app.core.http import Request
from app.core.http_errors import json_error_response
from app.core.redis_client import get_redis
from app.core.request_headers import header_value
from app.modules.x402.guard import PaymentResult, require_payment
from app.modules.x402_directory.models.domain import SettlementRecord
from app.modules.x402_directory.stores.base import ListingStore
from app.modules.x402_directory.stores.factory import get_listing_store

logger = logging.getLogger(__name__)

_REPLAY_PREFIX = "algorand:x402:spent:"


def _payment_header(request: Request) -> str:
    """The incoming payment header, read under the same name the x402 package reads."""
    return header_value(request.headers, PAYMENT_SIGNATURE_HEADER)


def _replay_key(header: str) -> str:
    """Redis key for one payment header, hashed so no signature material is stored."""
    return _REPLAY_PREFIX + hashlib.sha256(header.encode("utf-8")).hexdigest()


def _claim_payment(header: str) -> tuple[str | None, bool]:
    """Atomically claim a payment header as being spent right now.

    Returns (key_to_release_on_failure, already_seen). SET NX is the claim, so
    two concurrent requests carrying the same header cannot both proceed to
    settle. The key outlives the request by x402_replay_ttl_seconds, which is
    required to be >= 2x the facilitator's own HTTP timeout.

    Fails OPEN: a Redis outage must not take a paid endpoint offline. The
    facilitator and the chain remain the authoritative double-spend guard; this
    is defence in depth in front of them, not the only line.
    """
    if not header:
        return None, False
    try:
        claimed = get_redis().set(
            _replay_key(header), "1", nx=True, ex=settings.x402_replay_ttl_seconds
        )
    except Exception:
        logger.warning(
            "x402 replay check failed; failing open and letting the payment through",
            exc_info=True,
        )
        return None, False
    if not claimed:
        return None, True
    return _replay_key(header), False


def _release_claim(key: str | None) -> None:
    """Release a claim whose payment never settled, so a valid retry is not burned."""
    if not key:
        return
    try:
        get_redis().delete(key)
    except Exception:
        logger.warning(
            "x402 replay claim %s could not be released; a retry of this payment header "
            "will be rejected as a replay until it expires",
            key,
            exc_info=True,
        )


def record_settlement(
    result: PaymentResult,
    *,
    resource: str,
    store: ListingStore | None = None,
) -> None:
    """Append a settled payment to the bookkeeping ledger (CLAUDE.md section 9).

    Never raises. The payer has already been charged by the time this runs, so
    a ledger failure logs at ERROR with every field inline — the row stays
    recoverable from the log — and lets the caller serve the paid response.
    """
    record = SettlementRecord(
        tx_id=result.payment_txid or "",
        asset_id=result.asset_id or "",
        amount_atomic=result.amount_atomic or "",
        payer=result.payer or "",
        resource=resource,
        network=result.network or settings.x402_network,
        settled_at_epoch=int(datetime.now(tz=UTC).timestamp()),
        # No FX lookup exists yet; see SettlementRecord.eur_value.
        eur_value=0.0,
    )
    try:
        (store or get_listing_store()).record_settlement(record)
    except Exception:
        logger.exception(
            "x402 SETTLEMENT LEDGER WRITE FAILED — payment already settled, response still "
            "served. Recover this row by hand: tx_id=%s asset_id=%s amount_atomic=%s "
            "payer=%s resource=%s network=%s settled_at_epoch=%s eur_value=%s",
            record.tx_id,
            record.asset_id,
            record.amount_atomic,
            record.payer,
            record.resource,
            record.network,
            record.settled_at_epoch,
            record.eur_value,
        )


def require_paid_request(
    request: Request,
    *,
    price: str,
    resource: str,
    description: str | None = None,
    extensions: dict[str, Any] | None = None,
    store: ListingStore | None = None,
) -> PaymentResult:
    """Run the shared payment gate with replay protection and settlement logging.

    Returns the same PaymentResult shape require_payment does, so a handler
    reads identically: check `.error`, then use `.payer` / `.settlement_headers`.
    """
    header = _payment_header(request)
    claim_key, already_seen = _claim_payment(header)
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
        _release_claim(claim_key)
        return result

    record_settlement(result, resource=resource, store=store)
    return result
