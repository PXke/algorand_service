"""HTTP routes for KYC enrollment and the x402-gated lookup endpoint."""

from __future__ import annotations

from algosdk.encoding import is_valid_address

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
from app.modules.kyc.services.rate_limit import (
    consent_message_rate_limited,
    enroll_ip_rate_limited,
    enroll_wallet_rate_limited,
)
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import require_paid_request
from app.schemas import EnrollRequest, KycPayoutRetryRequest


def _current_round() -> int | None:
    from app.modules.chain.repository import get_chain_repository

    return get_chain_repository().get_chain_head_round()


# Stores/clients used by these two are constructed lazily on first use, so
# these are safe as module-level singletons shared by every route.
enrollment_service = EnrollmentService(current_round_fetcher=_current_round)
lookup_service = LookupService()


def kyc_consent_message(request: Request) -> Response:
    """Free, self-service: proves the requesting wallet's address to itself for the caller to display before they sign. No payment gate — the enrolled wallet is the one who benefits from being listed, not us. Rate-limited per IP like every other free endpoint in the marketplace."""
    if consent_message_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many consent-message requests — please try again later"
        )

    wallet = query_param(request.query_params.get("wallet_address", ""))
    if not wallet:
        return json_error_response(400, "invalid_request", "wallet_address required")
    message = build_kyc_consent_message(wallet_address=wallet)
    return {"message": message, "wallet_address": wallet}


def kyc_enroll(request: Request) -> Response:
    """Free enrollment: wallet-signed consent is the only gate. Computes trust signals from the public indexer and stores/overwrites the wallet's current KYC level — see EnrollmentService.

    Limited both per IP and per WALLET, and both checks run BEFORE the
    enrollment service is reached, so neither the outbound indexer requests nor
    the store write can be driven past the budget. The wallet limit is the one
    that matters: addresses are free to generate, so per-IP alone would bound
    request volume from one source while leaving "how many distinct wallets get
    enrolled" — the actual storage and outbound-request cost — unbounded.
    """
    if enroll_ip_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many enrollment requests — please try again later"
        )

    try:
        payload = serialization.decode(request.body, EnrollRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    if enroll_wallet_rate_limited(payload.wallet_address):
        return json_error_response(
            429,
            "rate_limited",
            "This wallet has been enrolled too many times today — please try again later",
        )

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


def kyc_verify(request: Request) -> Response:
    """The paid product: any third party (an exchange, a faucet, ...) pays to check a wallet's KYC status. Charged whether or not the wallet is enrolled (same as any paid lookup/search API) — the payout to the enrolled wallet only fires on a hit, since there's no subject to reward on a miss. See LookupService for the core rule (payout always goes to the LOOKED-UP wallet, never the payer).

    Everything checkable without knowing who is paying is checked BEFORE the
    payment gate, so nobody is charged for a request that was doomed: the
    wallet parameter must be present and must be a real Algorand address. A
    miss is a legitimate, chargeable answer ("that wallet is not enrolled"); a
    malformed address is not an answer at all, it is a request that could never
    have matched anything.
    """
    wallet = query_param(request.query_params.get("wallet", ""))
    if not wallet:
        return json_error_response(400, "invalid_request", "wallet query param required")
    # algosdk's own validator, not a length check: it verifies the base32
    # encoding and the trailing checksum too, so a 58-character string that
    # could not possibly be anyone's address is rejected before the gate
    # rather than after the payer has been charged for the inevitable miss.
    if not is_valid_address(wallet):
        return json_error_response(
            400, "invalid_request", "wallet must be a valid Algorand address"
        )

    result = require_paid_request(
        request,
        price=settings.kyc_lookup_price,
        resource="kyc-verify",
        extensions=describe_json_endpoint(
            input={"wallet": "ALGORAND_ADDRESS"},
            input_schema={
                "properties": {"wallet": {"type": "string"}},
                "required": ["wallet"],
            },
            output_example={
                "enrolled": True,
                "wallet_address": "...",
                "kyc_level": "basic",
                "payout_status": "sent",
            },
        ),
    )
    if result.error:
        return result.error

    payload = lookup_service.lookup(
        wallet_address=wallet,
        payer_address=result.payer or "",
        payment_txid=result.payment_txid or "",
        amount_atomic=result.amount_atomic or "0",
    )
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(payload),
    )


def kyc_payout_retry(request: Request) -> Response:
    """Admin-gated manual retry for a lookup whose payout failed (float too low, opt-in missing, algod hiccup, confirm timeout) — see kyc_lookup_events for which ones need it. Deliberately manual rather than an automatic backoff sweep: ship simple first, automate later if failures turn out to be common in practice."""
    from app.modules.admin.auth import require_admin_wallet

    denied = require_admin_wallet(request)
    if denied:
        return denied

    try:
        payload = serialization.decode(request.body, KycPayoutRetryRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    result = send_payout(
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
    app.get("/api/v1/kyc/consent-message")(kyc_consent_message)
    app.post("/api/v1/kyc/enroll")(kyc_enroll)
    app.get("/api/v1/kyc/verify")(kyc_verify)
    app.post("/api/v1/admin/kyc/payouts/retry")(kyc_payout_retry)
