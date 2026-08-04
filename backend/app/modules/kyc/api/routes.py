"""HTTP routes for KYC enrollment and the x402-gated lookup endpoint."""

from __future__ import annotations

import asyncio
import json

from x402.extensions.bazaar import declare_discovery_extension

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.kyc.models.domain import KycError
from app.modules.kyc.services.consent_message import build_kyc_consent_message
from app.modules.kyc.services.enrollment_service import EnrollmentService
from app.modules.kyc.services.lookup_service import LookupService
from app.modules.kyc.services.payout_service import send_payout
from app.modules.x402.guard import require_payment
from app.schemas import EnrollRequest, KycPayoutRetryRequest


def _current_round() -> int | None:
    from app.modules.chain.repository import get_chain_repository

    return get_chain_repository().get_chain_head_round()


# Stores/clients used by these two are constructed lazily on first use, so
# these are safe as module-level singletons shared by every route.
enrollment_service = EnrollmentService(current_round_fetcher=_current_round)
lookup_service = LookupService()


def kyc_test_ping(request: Request) -> Response:
    """Throwaway: proves the x402 402 -> pay -> verify -> settle round-trip through Robyn on TestNet. No attestation data, no Cassandra, no payout leg yet — those land once this is confirmed working end to end."""
    result = require_payment(
        request,
        price="$0.01",
        resource="kyc-ping",
        extensions=declare_discovery_extension(
            input={}, input_schema={}, output={"example": {"ok": True, "paid_by": "..."}}
        ),
    )
    if result.error:
        return result.error
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=json.dumps({"ok": True, "paid_by": result.payer}),
    )


def kyc_consent_message(request: Request) -> Response:
    """Free, self-service: proves the requesting wallet's address to itself for the caller to display before they sign. No payment gate — the enrolled wallet is the one who benefits from being listed, not us."""
    wallet = query_param(request.query_params.get("wallet_address", ""))
    if not wallet:
        return json_error_response(400, "invalid_request", "wallet_address required")
    message = build_kyc_consent_message(wallet_address=wallet)
    return {"message": message, "wallet_address": wallet}


def kyc_enroll(request: Request) -> Response:
    """Free enrollment: wallet-signed consent is the only gate. Computes trust signals from the public indexer and stores/overwrites the wallet's current KYC level — see EnrollmentService."""
    try:
        payload = serialization.decode(request.body, EnrollRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        record = enrollment_service.enroll(
            wallet_address=payload.wallet_address,
            consent_signature_b64=payload.consent_signature_b64,
        )
    except KycError as exc:
        return json_error_from_platform(exc)

    return {
        "wallet_address": record.wallet_address,
        "kyc_level": record.kyc_level,
        "wallet_age_round": record.wallet_age_round,
        "recent_tx_count": record.recent_tx_count,
        "enrolled_at_epoch": record.enrolled_at_epoch,
    }


async def kyc_verify(request: Request) -> Response:
    """The paid product: any third party (an exchange, a faucet, ...) pays to check a wallet's KYC status. Charged whether or not the wallet is enrolled (same as any paid lookup/search API) — the payout to the enrolled wallet only fires on a hit, since there's no subject to reward on a miss. See LookupService for the core rule (payout always goes to the LOOKED-UP wallet, never the payer)."""
    wallet = query_param(request.query_params.get("wallet", ""))
    if not wallet:
        return json_error_response(400, "invalid_request", "wallet query param required")

    result = require_payment(
        request,
        price=settings.kyc_lookup_price,
        resource="kyc-verify",
        extensions=declare_discovery_extension(
            input={"wallet": "ALGORAND_ADDRESS"},
            input_schema={
                "properties": {"wallet": {"type": "string"}},
                "required": ["wallet"],
            },
            output={
                "example": {
                    "enrolled": True,
                    "wallet_address": "...",
                    "kyc_level": "basic",
                    "payout_status": "sent",
                }
            },
        ),
    )
    if result.error:
        return result.error

    # The payout leg makes blocking algod calls (suggested params, submit,
    # confirm-wait) — run it off the event loop so one slow payout doesn't
    # stall every other in-flight request.
    payload = await asyncio.to_thread(
        lookup_service.lookup,
        wallet_address=wallet,
        payer_address=result.payer or "",
        payment_txid=result.payment_txid or "",
        amount_atomic=result.amount_atomic or "0",
    )
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=json.dumps(payload),
    )


async def kyc_payout_retry(request: Request) -> Response:
    """Admin-gated manual retry for a lookup whose payout failed (float too low, opt-in missing, algod hiccup, confirm timeout) — see kyc_lookup_events for which ones need it. Deliberately manual rather than an automatic backoff sweep: ship simple first, automate later if failures turn out to be common in practice."""
    from app.modules.admin.auth import require_admin_wallet

    denied = require_admin_wallet(request)
    if denied:
        return denied

    try:
        payload = serialization.decode(request.body, KycPayoutRetryRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    result = await asyncio.to_thread(
        send_payout,
        receiver=payload.wallet_address,
        amount_atomic=payload.amount_atomic,
    )
    return {
        "wallet_address": payload.wallet_address,
        "payout_status": result.status,
        "payout_txid": result.txid,
        "payout_error": result.error,
    }


def register_kyc_routes(app: Router) -> None:
    """Register the KYC enrollment, consent-message, x402-gated lookup, and payout-retry routes."""
    app.get("/api/v1/kyc/_test/ping")(kyc_test_ping)
    app.get("/api/v1/kyc/consent-message")(kyc_consent_message)
    app.post("/api/v1/kyc/enroll")(kyc_enroll)
    app.get("/api/v1/kyc/verify")(kyc_verify)
    app.post("/api/v1/admin/kyc/payouts/retry")(kyc_payout_retry)
