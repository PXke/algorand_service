"""HTTP routes for x402 agent backup storage: paid backup creation and renewal, free challenge/list/detail/delete.

Route paths are /api/v1/x402/*, not the bare /x402/* the build plan names.
nginx only proxies `location ^~ /api/` to this backend on the API host and
answers everything else with 404 (deploy/nginx/algorand-platform.conf), so a
bare /x402/storage would be unreachable in production without an nginx
change this change is not authorized to deploy.

Auth shape (no session -- see services/auth_service.py's own module
docstring for why): the free routes that need to know WHO is asking (list,
detail, delete) read `wallet`, `nonce`, `proof_method` and `signature_b64`
straight from query params (GET/DELETE never carry a body in this backend's
own convention -- see x402_directory/api/routes.py's admin-delete docstring)
and verify them fresh, in THIS request, via `_authenticate` below. There is
no bearer token anywhere in this module.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.core.request_headers import header_value
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import mark_fulfilled, require_paid_request, run_with_refund
from app.modules.x402_storage.models.domain import STATUS_DELETED, StorageError, StoredBackup
from app.modules.x402_storage.models.schemas import (
    BackupCreateRequest,
    BackupRenewRequest,
    ChallengeRequest,
)
from app.modules.x402_storage.services import auth_service, rate_limit
from app.modules.x402_storage.services.backup_service import (
    MAX_LABEL_LENGTH,
    BackupService,
    at_remaining_cap,
    compute_price,
    validate_declared_size,
)
from app.modules.x402_storage.services.reaper import reap_expired

logger = logging.getLogger(__name__)

# Store is resolved lazily on first use, so this is safe as a module-level
# singleton shared by every route.
backup_service = BackupService()

_RESOURCE_CREATE = "x402-storage-backup-create"
_RESOURCE_RENEW = "x402-storage-backup-renew"

_OUTPUT_EXAMPLE = {
    "backup_id": "0" * 36,
    "size_bytes": 0,
    "content_hash": "0" * 64,
    "label": "",
    "created_at_epoch": 0,
    "expires_at_epoch": 0,
    "status": "active",
    "settlement_tx_id": "...",
}


def _backup_json(item: StoredBackup) -> dict:
    """Serialize a stored backup's metadata for the wire.

    NEVER `connector` / `connector_params` -- an internal implementation
    detail, see StoredBackup's own docstring. Never `wallet` either: every
    caller of this already knows its own wallet, and every route that reaches
    here already scoped the lookup to it.
    """
    return {
        "backup_id": item.backup_id,
        "size_bytes": item.size_bytes,
        "content_hash": item.content_hash,
        "label": item.label,
        "created_at_epoch": item.created_at_epoch,
        "expires_at_epoch": item.expires_at_epoch,
        "status": item.status,
        "settlement_tx_id": item.settlement_tx_id,
    }


def _authenticate(request: Request) -> tuple[str, Response | None]:
    """Verify a free route's wallet-signature proof from query params (`wallet`, `nonce`, `proof_method`, `signature_b64`).

    Returns (wallet, None) on success, ("", <error Response>) otherwise.
    Order matters: the per-IP budget is checked first (before any Redis
    challenge lookup or verification work), then the required params are
    checked present, then the signature itself is verified, and ONLY THEN --
    once the wallet is actually proven -- the per-wallet budget. See
    services/rate_limit.py's own docstring for why the wallet axis must
    never be checked before verification succeeds.
    """
    if rate_limit.ip_rate_limited(request):
        return "", json_error_response(
            429, "rate_limited", "Too many requests -- please try again later"
        )

    wallet = query_param(request.query_params.get("wallet", ""))
    nonce = query_param(request.query_params.get("nonce", ""))
    proof_method = query_param(request.query_params.get("proof_method", "")) or "signed_bytes"
    signature_b64 = query_param(request.query_params.get("signature_b64", ""))
    if not wallet or not nonce or not signature_b64:
        return "", json_error_response(
            400, "invalid_request", "wallet, nonce and signature_b64 are required"
        )

    try:
        verified = auth_service.verify_challenge_signature(
            wallet=wallet, nonce=nonce, proof_method=proof_method, signature_b64=signature_b64
        )
    except auth_service.StorageAuthStoreError:
        return "", json_error_response(
            503,
            "auth_store_unavailable",
            "Wallet-signature verification is temporarily unavailable",
        )
    if not verified:
        return "", json_error_response(401, "unauthorized", "Invalid or expired wallet signature")

    if rate_limit.wallet_rate_limited(wallet):
        return "", json_error_response(
            429, "rate_limited", "Too many requests -- please try again later"
        )

    return wallet, None


def x402_storage_auth_challenge(request: Request) -> Response | dict:
    """Free: mint a short-TTL, single-use nonce for one wallet to sign, proving control for exactly one following protected call (GET list/detail or DELETE).

    `wallet` here is an UNAUTHENTICATED CLAIM (nothing has proven the caller
    holds that wallet's key yet) -- see services/rate_limit.py's own
    docstring for why this route is limited per-IP only, never per-wallet.
    """
    if rate_limit.ip_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many requests -- please try again later"
        )

    try:
        payload = serialization.decode(request.body, ChallengeRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        challenge = auth_service.issue_challenge(payload.wallet)
    except auth_service.StorageAuthStoreError:
        return json_error_response(
            503, "auth_store_unavailable", "Challenge issuance is temporarily unavailable"
        )
    return {
        "nonce": challenge.nonce,
        "signing_message": challenge.signing_message,
        "expires_at_epoch": challenge.expires_at,
        "proof_methods": list(auth_service.PROOF_METHODS),
    }


def x402_storage_create_backup(request: Request) -> Response:
    """Paid: store one opaque backup blob for x402_storage_term_days, price = ceil(declared_size_bytes/1MB) * x402_storage_price_per_mb.

    Everything checkable without knowing who is paying is checked BEFORE the
    payment gate, so a caller is never charged for a request that was doomed:
    `declared_size_bytes` is range/cap-checked (validate_declared_size), the
    body is decoded, and the base64 payload is decoded -- all free 400s/413s.

    Two DIFFERENT failure shapes can still occur AFTER the gate, inside
    backup_service.create() -- deliberately treated differently by
    run_with_refund (migration 102):

    - `size_mismatch` (the decoded payload is bigger than what
      `declared_size_bytes` claimed, i.e. what was actually charged): a
      StorageError, a PlatformError subclass -- payment kept, no refund, no
      circuit-breaker count. This is the payer's own doing (they under-
      declared the size), the same "settled but refused" contract every
      other module's ownership-conflict rejection gets.
    - Storage capacity/configuration failures (no usable connector, or the
      write would exceed the configured local-disk ceiling): a
      StorageCapacityUnavailable, deliberately NOT a PlatformError -- OUR
      problem, not the payer's, so run_with_refund's generic-exception path
      refunds the payer and counts it against this resource's circuit
      breaker (see that exception's own docstring in models/domain.py).
    """
    if circuit_breaker.is_tripped(_RESOURCE_CREATE):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    raw_declared = query_param(request.query_params.get("declared_size_bytes", ""))
    try:
        declared_size_bytes = int(raw_declared)
    except ValueError:
        return json_error_response(
            400, "invalid_request", "declared_size_bytes must be an integer query param"
        )

    try:
        validate_declared_size(declared_size_bytes)
    except StorageError as exc:
        return json_error_from_platform(exc)

    try:
        payload = serialization.decode(request.body, BackupCreateRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        data = base64.b64decode(payload.data, validate=True)
    except Exception:
        return json_error_response(400, "invalid_request", "data must be valid base64")

    price = compute_price(declared_size_bytes)
    result = require_paid_request(
        request,
        price=price,
        resource=_RESOURCE_CREATE,
        description=(
            f"Store one opaque backup blob (up to declared_size_bytes={declared_size_bytes} "
            f"bytes, charged at ceil(size/1MB) * {settings.x402_storage_price_per_mb}/MB), "
            f"retrievable and deletable for up to {settings.x402_storage_max_remaining_days} days "
            "(one paid term, not stackable past that ceiling) by proving control of this same "
            "wallet again (no session -- POST .../auth/challenge then GET/DELETE "
            "/storage/backups with the signed proof). Stored content is OPAQUE with NO "
            "confidentiality guarantee beyond owner-only access control: encrypt sensitive "
            "data yourself before uploading."
        ),
        extensions=describe_json_endpoint(
            body_type="json",
            input={"data": "<base64>", "label": "my-agent-state-backup"},
            input_schema={
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "description": "Base64-encoded opaque bytes, at most declared_size_bytes when decoded.",
                    },
                    "label": {"type": "string", "maxLength": MAX_LABEL_LENGTH},
                },
                "required": ["data"],
            },
            output_example=_OUTPUT_EXAMPLE,
        ),
    )
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_RESOURCE_CREATE,
        product_write=lambda: _backup_json(
            backup_service.create(
                wallet=result.payer,
                data=data,
                declared_size_bytes=declared_size_bytes,
                label=payload.label,
                settlement_tx_id=result.payment_txid or "",
            )
        ),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_RESOURCE_CREATE)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
    )


def x402_storage_renew_backup(request: Request) -> Response:
    """Paid: extend an existing backup's expiry by one more term, owner only, priced from its ALREADY-STORED size_bytes.

    Remaining life is capped at x402_storage_max_remaining_days (90): if the
    backup is already at that ceiling, this returns a free 400 before the
    payment gate so the caller is not charged for a no-op. Expired-but-not-
    yet-reaped backups (inside x402_storage_reaper_grace_days) can still be
    renewed -- that is the "pay to keep the bytes" window.

    Existence is checked BEFORE the payment gate (a free 404 for an unknown
    id) using `wallet` from the request body purely as a lookup hint --
    x402_storage_backups is partitioned by wallet, so a point read needs it
    up front, exactly the same trust level as x402_directory's own renew
    taking `url` from its body. That hint proves nothing by itself.

    Ownership CANNOT be checked before the gate -- the real payer is only
    known once the payment has settled -- so a renewal by a wallet other
    than the one that created the backup is refused with the payment already
    taken and the backup untouched (403), same accepted tradeoff and same
    "payment kept, receipt served, never refunded, never counted against the
    circuit breaker" contract as x402_board's own renew (that check is
    re-evaluated here, before ever calling run_with_refund, for the exact
    same reason x402_board's docstring gives: refunding a fully
    caller-controlled rejection would be a free way to pump the breaker).
    """
    if circuit_breaker.is_tripped(_RESOURCE_RENEW):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    backup_id = query_param(request.path_params.get("backup_id", ""))
    try:
        payload = serialization.decode(request.body, BackupRenewRequest)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    backup = backup_service.get(payload.wallet, backup_id)
    if backup is None or backup.status == STATUS_DELETED:
        return json_error_response(404, "not_found", "No backup with that id for that wallet")

    if at_remaining_cap(backup):
        return json_error_response(
            400,
            "term_at_maximum",
            "This backup already has the maximum remaining storage term "
            f"({settings.x402_storage_max_remaining_days} days). Payment was not taken.",
        )

    price = compute_price(backup.size_bytes)
    term_days = settings.x402_storage_term_days
    result = require_paid_request(
        request,
        price=price,
        resource=_RESOURCE_RENEW,
        description=(
            f"Extend an existing backup's retrieval window by up to {term_days} more days, "
            f"capped at {settings.x402_storage_max_remaining_days} days remaining from now "
            "(renewing early does not stack past that ceiling). Works during the "
            f"{settings.x402_storage_reaper_grace_days}-day grace after expiry, before the "
            "reaper deletes the bytes. Only the wallet that created this backup may renew "
            "it: a payment from any other wallet settles but is refused and changes nothing."
        ),
        extensions=describe_json_endpoint(
            body_type="json",
            input={"wallet": "A" * 58},
            input_schema={
                "type": "object",
                "properties": {"wallet": {"type": "string", "minLength": 58, "maxLength": 58}},
                "required": ["wallet"],
            },
            output_example=_OUTPUT_EXAMPLE,
        ),
    )
    if result.error:
        return result.error

    # Same condition as a mismatch between the settled payer and the row's
    # own wallet -- re-checked here, before ever calling run_with_refund, so
    # this deliberate no-refund rejection is never mistaken for the generic
    # "any exception refunds" path. See this function's own docstring.
    attributed = (result.payer or "").strip()
    if not attributed or attributed != backup.wallet:
        return Response(
            status_code=403,
            headers={"Content-Type": "application/json", **result.settlement_headers},
            description=serialization.dumps(
                {
                    "error": {
                        "code": "backup_owned_by_another_payer",
                        "message": (
                            "Only the wallet that created this backup may renew it. "
                            "Payment has settled but the backup's expiry was not changed."
                        ),
                    }
                }
            ),
        )

    outcome = run_with_refund(
        result,
        resource=_RESOURCE_RENEW,
        product_write=lambda: _backup_json(
            backup_service.renew(backup, settlement_tx_id=result.payment_txid or "")
        ),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_RESOURCE_RENEW)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
    )


def x402_storage_list_backups(request: Request) -> Response | dict:
    """Free, wallet-signature-authenticated: this wallet's own active, unexpired backups, newest first."""
    wallet, error = _authenticate(request)
    if error is not None:
        return error

    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw_limit) if raw_limit else settings.x402_storage_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")

    items = backup_service.list_live(wallet, limit=limit)
    return {"items": [_backup_json(item) for item in items]}


def x402_storage_get_backup(request: Request) -> Response | dict:
    """Free, wallet-signature-authenticated, owner-only: one backup's metadata plus its restored bytes (base64), sha256-verified.

    404 covers not-found, not-owned (the authenticated wallet is the ONLY
    partition this can ever look up, so "not owned" and "not found" are
    structurally the same case here), soft-deleted and expired -- all via
    backup_service.get_live(). A content_hash mismatch, or the connector
    failing to produce the bytes at all, is a REAL internal error (logged at
    ERROR, 500) -- CLAUDE.md section 2: never silently serve corrupted data.
    """
    wallet, error = _authenticate(request)
    if error is not None:
        return error

    backup_id = query_param(request.path_params.get("backup_id", ""))
    backup = backup_service.get_live(wallet, backup_id)
    if backup is None:
        return json_error_response(404, "not_found", "No live backup with that id for this wallet")

    data = backup_service.read_bytes(backup)
    if data is None:
        logger.error(
            "x402 storage: connector could not produce bytes for wallet=%s backup_id=%s connector=%s",
            wallet,
            backup_id,
            backup.connector,
        )
        return json_error_response(
            500, "backup_unreadable", "This backup's stored bytes could not be read"
        )

    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != backup.content_hash:
        logger.error(
            "x402 storage: content_hash mismatch on restore for wallet=%s backup_id=%s "
            "(expected=%s actual=%s)",
            wallet,
            backup_id,
            backup.content_hash,
            actual_hash,
        )
        return json_error_response(
            500, "integrity_check_failed", "Stored data failed integrity verification"
        )

    return {**_backup_json(backup), "data": base64.b64encode(data).decode("ascii")}


def x402_storage_delete_backup(request: Request) -> Response | dict:
    """Free, wallet-signature-authenticated, owner-only: delete one backup outright.

    Deletes the connector bytes first, then marks the row deleted (see
    backup_service.delete()'s own docstring for why that ordering matters).
    404 covers not-found, not-owned and already-deleted, same reasoning as
    x402_storage_get_backup.
    """
    wallet, error = _authenticate(request)
    if error is not None:
        return error

    backup_id = query_param(request.path_params.get("backup_id", ""))
    backup = backup_service.get(wallet, backup_id)
    if backup is None or backup.status == STATUS_DELETED:
        return json_error_response(404, "not_found", "No backup with that id for this wallet")

    backup_service.delete(backup)
    return {"deleted": True, "backup_id": backup_id}


def x402_storage_reap(_request: Request) -> Response | dict:
    """Internal: delete backups whose reaper grace window after expiry has elapsed.

    Not a marketplace product -- the Celery beat on this same box POSTs here
    because the local-disk connector lives on the API host. Gated on
    x402_storage_reaper_token (empty token = 404, same disabled convention
    as an empty local_root). Path is /api/v1/internal/... so it is not
    required to appear in the x402 catalog roster.
    """
    expected = settings.x402_storage_reaper_token.strip()
    if not expected:
        return json_error_response(404, "not_found", "Not found")
    presented = header_value(_request.headers, "x-storage-reaper-token")
    if (
        not presented
        or len(presented) != len(expected)
        or not hmac.compare_digest(presented, expected)
    ):
        return json_error_response(401, "unauthorized", "Invalid reaper token")
    return reap_expired(backup_service)


def register_x402_storage_routes(app: Router) -> None:
    """Register the free challenge route, paid create/renew, free list/detail/delete, and the internal reaper."""
    app.post("/api/v1/x402/storage/auth/challenge")(x402_storage_auth_challenge)
    app.post("/api/v1/x402/storage/backups")(x402_storage_create_backup)
    app.get("/api/v1/x402/storage/backups")(x402_storage_list_backups)
    app.get("/api/v1/x402/storage/backups/:backup_id")(x402_storage_get_backup)
    app.post("/api/v1/x402/storage/backups/:backup_id/renew")(x402_storage_renew_backup)
    app.delete("/api/v1/x402/storage/backups/:backup_id")(x402_storage_delete_backup)
    app.post("/api/v1/internal/x402/storage/reap")(x402_storage_reap)
