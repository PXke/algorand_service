"""Signed fulfillment receipts: bind exactly what a paid route delivered to exactly what was paid for.

Owner conversation 2026-09-03 (see docs/x402-execution-trust-evaluation.md
item 1, "Verifiable execution / attestation schema" -- read that first, do
not re-derive the reasoning here). Evidence, not a guarantee: a receipt
proves authentically what we sent for a specific settled payment, never that
the response was any good (grading already covers quality; auto-refund
already covers total delivery failure).

Hooked into modules/x402/paid_request.run_with_refund's success path
(attach_fulfillment_receipt is called from there, never from a route
directly) so every route wired through run_with_refund gets a receipt
automatically once the signing key is configured -- no per-route code.

Signing key: a FRESH, DEDICATED Algorand keypair in
settings.x402_receipt_signing_mnemonic, never x402_refund_mnemonic /
kyc_payout_mnemonic / x402_pay_to_address -- it never holds funds and is
never asked to. Signed with algosdk.util.sign_bytes, the SAME "MX"-domain-
separated primitive workers/app/modules/wallet/signer.py already uses for
algo_signData: it prepends Algorand's own domain-separation prefix before
signing, so the resulting signature can never be replayed as authorization
for a real on-chain transaction -- safe to keep on this network-facing
service even though it lives in a container reachable from the internet.
Empty mnemonic = receipt generation is skipped entirely (logged at DEBUG,
never blocks or fails the paid route) -- same "empty is inert" convention as
every other optional signing wallet in this codebase.

Public wire contract (X-Fulfillment-Receipt response header)
--------------------------------------------------------------
A successful response from a route wired through run_with_refund carries, IF
the signing key is configured::

    X-Fulfillment-Receipt: {"receipt_id": "...", "settlement_tx_id": "...",
        "resource": "...", "signing_address": "ALGORAND58CHARADDR...",
        "signature": "<base64>", "request_hash": "<hex sha256>",
        "response_hash": "<hex sha256>", "issued_at_epoch": 1234567890}

`request_hash` is sha256 of the raw incoming request body bytes.
`response_hash` is sha256 of the UTF-8 JSON response body bytes (the exact
`{**product_result, "settlement_tx_id": ...}` shape every run_with_refund
caller already serves). `signature` is `sign_bytes(payload, private_key)`
where::

    payload = f"{request_hash}|{response_hash}|{settlement_tx_id}|{issued_at_epoch}".encode()

A third party verifies with `algosdk.util.verify_bytes(payload, signature,
signing_address)` after rebuilding `payload` from the same four fields (the
full response body, byte-identical, is retrievable for 90 days via
`GET /api/v1/x402/receipts/{receipt_id}` -- see api/routes.py in
x402_receipts/ -- so response_hash is independently checkable, not just
trusted).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core import serialization
from app.core.config import settings
from app.core.http import Request
from app.modules.x402.guard import PaymentResult
from app.modules.x402.receipt_store import ReceiptRecord, ReceiptStore, get_receipt_store

logger = logging.getLogger(__name__)

RECEIPT_HEADER = "X-Fulfillment-Receipt"


def _private_key() -> str | None:
    """The signing wallet's private key, or None if unconfigured/invalid.

    Isolated from any other except block on purpose (this exact codebase
    was bitten twice already this session, refund.py and payout_service.py):
    the installed algosdk's mnemonic.to_private_key raises
    ValueError(mnemonic) -- the exception MESSAGE IS THE ENTIRE 25-WORD
    SECRET PHRASE -- when any word is not in the wordlist. Never let that
    reach a log call. str(exc) is never logged from this function.
    """
    from algosdk import mnemonic

    phrase = settings.x402_receipt_signing_mnemonic.strip()
    if not phrase:
        return None
    try:
        return mnemonic.to_private_key(phrase)
    except Exception:
        logger.error(
            "x402 receipts: configured signing mnemonic is invalid (value never logged) -- "
            "receipts cannot be generated until this is fixed"
        )
        return None


def _build_response_text(*, outcome: dict[str, Any], settlement_tx_id: str) -> str:
    """The exact JSON text every run_with_refund route caller already serves as its body."""
    return serialization.dumps({**outcome, "settlement_tx_id": settlement_tx_id})


def attach_fulfillment_receipt(
    result: PaymentResult,
    *,
    resource: str,
    request: Request | None,
    outcome: Any,  # noqa: ANN401 -- whatever product_write() returned; only a dict is receiptable
    store: ReceiptStore | None = None,
) -> None:
    """Best-effort: sign, store, and attach a fulfillment receipt for one successful paid product write.

    Called from paid_request.run_with_refund's success path, right after
    `product_write()` returns without raising -- the same moment the route
    goes on to call mark_fulfilled. Mutates `result.settlement_headers` IN
    PLACE (adding RECEIPT_HEADER) so the route's existing
    `**result.settlement_headers` spread carries the receipt with zero
    per-route code -- see every run_with_refund caller's own
    `headers={"Content-Type": ..., **result.settlement_headers}`.

    Additive only. NEVER raises and never fails/blocks the paid response:
    unconfigured signing key, missing request context, a non-dict outcome
    (nothing to hash predictably), or a Redis/Cassandra blip while storing
    all fall through to "no receipt this time", logged at DEBUG (expected,
    silent) or WARNING (unexpected, with context) as appropriate -- never at
    a level that would page anyone, since a fulfilled paid response has
    already been served either way.
    """
    if not settings.x402_receipt_signing_mnemonic.strip():
        logger.debug(
            "x402 receipts: signing key not configured, skipping receipt for resource=%s", resource
        )
        return
    if request is None:
        logger.debug(
            "x402 receipts: no request context available, skipping receipt for resource=%s",
            resource,
        )
        return
    if not isinstance(outcome, dict):
        # Not an error: not every run_with_refund caller serves the uniform
        # `{**outcome, "settlement_tx_id": ...}` shape most product routes
        # do (x402_social's routes, for one, hand back a domain object and
        # build their own response dict by hand) -- there is no reliable way
        # to reproduce that route's actual response bytes from here, so this
        # is an expected, silent skip, not a warning-worthy condition.
        logger.debug(
            "x402 receipts: product_write outcome for resource=%s is a %s, not a dict -- "
            "cannot hash it predictably, skipping receipt",
            resource,
            type(outcome).__name__,
        )
        return

    try:
        _generate_and_store_receipt(
            result, resource=resource, request=request, outcome=outcome, store=store
        )
    except Exception:
        logger.warning(
            "x402 receipts: failed to generate/store a receipt for resource=%s tx_id=%s "
            "-- the paid response is served without one",
            resource,
            result.payment_txid,
            exc_info=True,
        )


def _generate_and_store_receipt(
    result: PaymentResult,
    *,
    resource: str,
    request: Request,
    outcome: dict[str, Any],
    store: ReceiptStore | None,
) -> None:
    """The real work, isolated so its caller's except-Exception can wrap every failure mode uniformly.

    Storage happens BEFORE the header is attached -- if the store write
    raises, this whole function raises (caught by the caller), and NO
    header is attached: a receipt the read endpoint cannot actually serve
    must never be advertised as delivered.
    """
    private_key = _private_key()
    if private_key is None:
        return  # already logged by _private_key(), never the raw secret

    from algosdk import account
    from algosdk.util import sign_bytes

    settlement_tx_id = result.payment_txid or ""
    request_hash = hashlib.sha256(request.body or b"").hexdigest()
    response_text = _build_response_text(outcome=outcome, settlement_tx_id=settlement_tx_id)
    response_hash = hashlib.sha256(response_text.encode("utf-8")).hexdigest()
    issued_at_epoch = int(datetime.now(tz=UTC).timestamp())

    payload = f"{request_hash}|{response_hash}|{settlement_tx_id}|{issued_at_epoch}".encode()
    signature = sign_bytes(payload, private_key)
    signing_address = account.address_from_private_key(private_key)

    max_chars = settings.x402_receipt_output_max_chars
    output_truncated = len(response_text) > max_chars
    output_text = response_text[:max_chars] if output_truncated else response_text

    receipt_id = str(uuid.uuid4())
    record = ReceiptRecord(
        receipt_id=receipt_id,
        settlement_tx_id=settlement_tx_id,
        resource=resource,
        signing_address=signing_address,
        signature=signature,
        request_hash=request_hash,
        response_hash=response_hash,
        output=output_text,
        output_truncated=output_truncated,
        issued_at_epoch=issued_at_epoch,
    )
    (store or get_receipt_store()).record_receipt(record)

    result.settlement_headers[RECEIPT_HEADER] = serialization.dumps(
        {
            "receipt_id": receipt_id,
            "settlement_tx_id": settlement_tx_id,
            "resource": resource,
            "signing_address": signing_address,
            "signature": signature,
            "request_hash": request_hash,
            "response_hash": response_hash,
            "issued_at_epoch": issued_at_epoch,
        }
    )


def receipt_json(record: ReceiptRecord) -> dict[str, Any]:
    """Public JSON shape for GET /api/v1/x402/receipts/{receipt_id} -- every field a third party needs to independently recompute `payload` and call `algosdk.util.verify_bytes(payload, signature, signing_address)`, plus the retained output itself."""
    return {
        "receipt_id": record.receipt_id,
        "settlement_tx_id": record.settlement_tx_id,
        "resource": record.resource,
        "signing_address": record.signing_address,
        "signature": record.signature,
        "request_hash": record.request_hash,
        "response_hash": record.response_hash,
        "issued_at_epoch": record.issued_at_epoch,
        "output": record.output,
        "output_truncated": record.output_truncated,
    }
