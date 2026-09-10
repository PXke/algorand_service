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
own convention) and verify them fresh, in THIS request, via `_authenticate`
below. There is
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
from app.modules.x402.guard import PaymentResult
from app.modules.x402.paid_request import (
    challenge_if_unpaid,
    mark_fulfilled,
    require_paid_request,
    run_with_refund,
)
from app.modules.x402_storage.models.domain import (
    STATUS_DELETED,
    StorageError,
    StoredBackup,
    StoredBackupVersion,
)
from app.modules.x402_storage.models.schemas import BackupCreateRequest, ChallengeRequest
from app.modules.x402_storage.services import auth_service, rate_limit
from app.modules.x402_storage.services.backup_service import (
    MAX_LABEL_LENGTH,
    BackupService,
    at_remaining_cap,
    compute_price,
    validate_declared_size,
    validate_retention_days,
)
from app.modules.x402_storage.services.reaper import reap_expired

logger = logging.getLogger(__name__)

# Store is resolved lazily on first use, so this is safe as a module-level
# singleton shared by every route.
backup_service = BackupService()

_RESOURCE_CREATE = "x402-storage-backup-create"
_RESOURCE_RENEW = "x402-storage-backup-renew"
_RESOURCE_ADD_VERSION = "x402-storage-backup-add-version"

_OUTPUT_EXAMPLE = {
    "backup_id": "0" * 36,
    "size_bytes": 0,
    "content_hash": "0" * 64,
    "label": "",
    "created_at_epoch": 0,
    "expires_at_epoch": 0,
    "status": "active",
    "settlement_tx_id": "...",
    "current_version": 1,
}

_VERSION_OUTPUT_EXAMPLE = {
    "version": 1,
    "size_bytes": 0,
    "content_hash": "0" * 64,
    "label": "",
    "created_at_epoch": 0,
    "expires_at_epoch": 0,
    "status": "active",
    "settlement_tx_id": "...",
}

# Points a wallet at a real, runnable client-side encryption recipe before
# it ever uploads anything sensitive -- see docs/x402-storage-encryption-guide.md
# for the actual age/openssl one-liners. Kept as a short pointer in the
# route description (mirrors how other routes in this catalog point at a
# fuller doc) rather than inlining the recipe here, where it would bloat
# every single 402 offer response.
_ENCRYPTION_GUIDE_POINTER = (
    "See docs/x402-storage-encryption-guide.md for a copy-paste recipe "
    "(age or openssl) to encrypt before base64-encoding into `data`, and "
    "decrypt after retrieval."
)


def backup_metadata_json(item: StoredBackup) -> dict:
    """Serialize a stored backup's metadata for the wire.

    NEVER `connector` / `connector_params` -- an internal implementation
    detail, see StoredBackup's own docstring. Never `wallet` either: every
    caller of this already knows its own wallet, and every route that reaches
    here already scoped the lookup to it. `current_version` (migration 115)
    says which version number this row's own content currently mirrors --
    1 for a backup that has never had a version added.
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
        "current_version": item.current_version,
    }


def version_metadata_json(item: StoredBackupVersion) -> dict:
    """Serialize one version row's metadata for the wire -- same redaction rules as backup_metadata_json (never connector/connector_params/wallet)."""
    return {
        "version": item.version,
        "size_bytes": item.size_bytes,
        "content_hash": item.content_hash,
        "label": item.label,
        "created_at_epoch": item.created_at_epoch,
        "expires_at_epoch": item.expires_at_epoch,
        "status": item.status,
        "settlement_tx_id": item.settlement_tx_id,
    }


def _decode_upload_payload(request: Request) -> tuple[bytes, str, Response | None]:
    """Decode the JSON body + base64 `data` shape create() and add_version() both take. Returns (data, label, error) -- error is None on success.

    Shared so the two paid write routes never carry two independently
    maintained copies of the same decode-then-base64 validation (CLAUDE.md
    section 3).
    """
    try:
        payload = serialization.decode(request.body, BackupCreateRequest)
    except serialization.DecodeError as exc:
        return b"", "", json_error_response(400, "invalid_request", str(exc))
    try:
        data = base64.b64decode(payload.data, validate=True)
    except Exception:
        return b"", "", json_error_response(400, "invalid_request", "data must be valid base64")
    return data, payload.label, None


def _parse_retention_days(request: Request) -> tuple[int, Response | None]:
    """Parse+validate the optional `retention_days` query param. Returns (retention_days, error) -- error is None on success.

    Shared by create() and add_version() (CLAUDE.md section 3: no new
    copies of existing logic) -- both need this resolved BEFORE the payment
    gate, exactly like `declared_size_bytes` above, since the offer's price
    depends on it. Defaults to the full x402_storage_term_days when the
    caller omits it, same "always the max term" behavior this module had
    before retention_days became a real input. Query param, not a JSON body
    field, for the identical reason `declared_size_bytes` is one: the price
    must be fixed before the body is ever decoded (see
    x402_storage_create_backup's own docstring).
    """
    raw = query_param(request.query_params.get("retention_days", ""))
    if not raw:
        return settings.x402_storage_term_days, None
    try:
        retention_days = int(raw)
    except ValueError:
        return 0, json_error_response(
            400, "invalid_request", "retention_days must be an integer query param"
        )
    try:
        validate_retention_days(retention_days)
    except StorageError as exc:
        return 0, json_error_from_platform(exc)
    return retention_days, None


def _parse_size_and_retention(request: Request) -> tuple[int, int, Response | None]:
    """Parse+validate `declared_size_bytes` and `retention_days` together. Returns (declared_size_bytes, retention_days, error) -- error is None on success.

    Shared by create() and add_version(): both need BOTH values resolved
    before the payment gate (the offer's price is computed from both), so
    this collapses what would otherwise be four near-identical branches
    duplicated in each route body into one call (CLAUDE.md section 3: no
    new copies of existing logic; also keeps each route function's own
    cyclomatic complexity down).
    """
    raw_declared = query_param(request.query_params.get("declared_size_bytes", ""))
    try:
        declared_size_bytes = int(raw_declared)
    except ValueError:
        return (
            0,
            0,
            json_error_response(
                400, "invalid_request", "declared_size_bytes must be an integer query param"
            ),
        )
    try:
        validate_declared_size(declared_size_bytes)
    except StorageError as exc:
        return 0, 0, json_error_from_platform(exc)

    retention_days, retention_error = _parse_retention_days(request)
    if retention_error is not None:
        return 0, 0, retention_error
    return declared_size_bytes, retention_days, None


def _ownership_refused(result: PaymentResult, action: str) -> Response:
    """The shared 'payment settled but the wallet does not own this backup' 403, used by renew() and add_version().

    Same no-refund, no-circuit-breaker-count contract both routes'
    docstrings give: re-checked before ever calling run_with_refund, so
    this deliberate rejection is never mistaken for the generic
    "any exception refunds" path.
    """
    return Response(
        status_code=403,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "error": {
                    "code": "backup_owned_by_another_payer",
                    "message": (
                        f"Only the wallet that created this backup may {action}. "
                        "Payment has settled but nothing was changed."
                    ),
                }
            }
        ),
    )


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
    """Paid: store one opaque backup blob for a caller-chosen retention_days, price = max(floor, ceil(declared_size_bytes/1KB) * x402_storage_price_per_kb_per_90d * retention_days/x402_storage_term_days).

    Everything checkable without knowing who is paying is checked BEFORE the
    payment gate, so a caller is never charged for a request that was doomed:
    `declared_size_bytes` is range/cap-checked (validate_declared_size),
    `retention_days` is range-checked (validate_retention_days), the body is
    decoded, and the base64 payload is decoded -- all free 400s/413s.

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

    declared_size_bytes, retention_days, validation_error = _parse_size_and_retention(request)
    if validation_error is not None:
        return validation_error

    price = compute_price(declared_size_bytes, retention_days)
    offer = {
        "price": price,
        "resource": _RESOURCE_CREATE,
        "description": (
            f"Store one opaque backup blob (up to declared_size_bytes={declared_size_bytes} "
            f"bytes, billed by the KB actually used, never rounded up to a whole MB) for "
            f"retention_days={retention_days} days (optional query param, 1-"
            f"{settings.x402_storage_term_days}, default {settings.x402_storage_term_days}), "
            f"charged at max({settings.x402_storage_price_floor}, ceil(size/1KB) * "
            f"{settings.x402_storage_price_per_kb_per_90d}/KB * "
            f"retention_days/{settings.x402_storage_term_days}). Overall remaining life is "
            f"still capped at {settings.x402_storage_max_remaining_days} days from now "
            "(renew separately to extend past what this call's retention_days bought), "
            "retrievable and deletable throughout by proving control of this same "
            "wallet again (no session -- POST .../auth/challenge then GET/DELETE "
            "/storage/backups with the signed proof). Stored content is OPAQUE: never "
            "scanned, indexed, or acted on by us. Not confidential from us, though -- an "
            "operator can inspect or remove a specific backup for abuse/legal response "
            "(no other agent can). Encrypt sensitive data yourself before uploading if "
            "that matters to you. " + _ENCRYPTION_GUIDE_POINTER + " Need more than one "
            "version? POST .../storage/backups/{backup_id}/versions adds a new version "
            "under this same id without losing the old ones."
        ),
        "extensions": describe_json_endpoint(
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
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid) -- but only once declared_size_bytes AND
    # retention_days are known, since the offered price is computed from
    # both. With a payment attached, the body and its base64 are still
    # validated before the gate.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    data, label, decode_error = _decode_upload_payload(request)
    if decode_error is not None:
        return decode_error

    result = require_paid_request(request, **offer)
    if result.error:
        return result.error

    outcome = run_with_refund(
        result,
        resource=_RESOURCE_CREATE,
        product_write=lambda: backup_metadata_json(
            backup_service.create(
                wallet=result.payer,
                data=data,
                declared_size_bytes=declared_size_bytes,
                label=label,
                settlement_tx_id=result.payment_txid or "",
                retention_days=retention_days,
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
    """Paid: extend an existing backup's expiry by one more full term, owner only, priced from its ALREADY-STORED size_bytes.

    Priced by `compute_price(backup.size_bytes)` with no `retention_days`
    argument -- a renewal always buys one full fresh x402_storage_term_days
    term (never a caller-chosen shorter one, unlike create()/add_version()),
    so compute_price() resolves its own default (the full term) rather than
    scaling down. See compute_price()'s own docstring for the KB-granular
    formula.

    `wallet` arrives as a query param (`?wallet=...`), not the JSON body --
    moved 2026-09-06 so a header-less probe can see the 402 offer at all
    (found live: this was the one paid route in the marketplace that always
    400'd an unpaid request instead, because its price is
    `compute_price(backup.size_bytes)`, which needs the backup looked up
    first, and x402_storage_backups is partitioned by wallet -- a point read
    needs the wallet up front). Exactly the same trust level as before: this
    is purely a lookup hint, never proof of ownership -- see the ownership
    check below.

    Remaining life is capped at x402_storage_max_remaining_days (90): if the
    backup is already at that ceiling, this returns a free 400 before the
    payment gate so the caller is not charged for a no-op. Expired-but-not-
    yet-reaped backups (inside x402_storage_reaper_grace_days) can still be
    renewed -- that is the "pay to keep the bytes" window.

    Existence is checked BEFORE the payment gate: an unknown backup_id, and a
    wallet that does not own any backup under that id (wrong wallet, typo, a
    probe with a made-up value), both return the same free 404 -- there is
    nothing new to invent for a wallet/id mismatch, it is the same "unknown
    id" case this function has always had.

    Ownership CANNOT be checked before the gate -- the real payer is only
    known once the payment has settled -- so a renewal by a wallet other
    than the one that created the backup is refused with the payment already
    taken and the backup untouched (403) -- an accepted tradeoff, with a
    "payment kept, receipt served, never refunded, never counted against the
    circuit breaker" contract (that check is re-evaluated here, before ever
    calling run_with_refund, because refunding a fully caller-controlled
    rejection would be a free way to pump the breaker).
    """
    if circuit_breaker.is_tripped(_RESOURCE_RENEW):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    backup_id = query_param(request.path_params.get("backup_id", ""))
    wallet = query_param(request.query_params.get("wallet", ""))

    backup = backup_service.get(wallet, backup_id)
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
    offer = {
        "price": price,
        "resource": _RESOURCE_RENEW,
        "resource_path": "/api/v1/x402/storage/backups/{backup_id}/renew",
        "description": (
            f"Extend an existing backup's retrieval window by up to {term_days} more days, "
            f"capped at {settings.x402_storage_max_remaining_days} days remaining from now "
            "(renewing early does not stack past that ceiling). Works during the "
            f"{settings.x402_storage_reaper_grace_days}-day grace after expiry, before the "
            "reaper deletes the bytes. Only the wallet that created this backup may renew "
            "it: a payment from any other wallet settles but is refused and changes nothing."
        ),
        "extensions": describe_json_endpoint(body_type="json", output_example=_OUTPUT_EXAMPLE),
    }
    # The offer's price is only knowable once the backup lookup above has
    # run, so (unlike every other reordered route) the challenge fires after
    # a free Cassandra read, not before it -- see this function's own
    # docstring and challenge_if_unpaid's for why this is the accepted shape
    # here rather than a gap.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    result = require_paid_request(request, **offer)
    if result.error:
        return result.error

    # Same condition as a mismatch between the settled payer and the row's
    # own wallet -- re-checked here, before ever calling run_with_refund, so
    # this deliberate no-refund rejection is never mistaken for the generic
    # "any exception refunds" path. See this function's own docstring.
    attributed = (result.payer or "").strip()
    if not attributed or attributed != backup.wallet:
        return _ownership_refused(result, "renew it")

    outcome = run_with_refund(
        result,
        resource=_RESOURCE_RENEW,
        product_write=lambda: backup_metadata_json(
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


def x402_storage_add_version(request: Request) -> Response:
    """Paid: store a NEW version's bytes under an EXISTING backup_id, owner only, priced identically to create() from the NEW upload's declared_size_bytes.

    Same free-checks-before-the-gate shape as create() (validate_declared_size,
    body decode, base64 decode all run before any payment is taken) PLUS the
    same existence-before-gate / ownership-after-settlement shape as renew()
    (`wallet` is a query param, a purely-a-lookup-hint the way renew's own
    docstring explains -- an unknown backup_id is a free 404, but a payment
    from a wallet other than the one that created the backup settles and is
    refused, same "payment kept, receipt served, never refunded, never
    counted against the circuit breaker" contract renew's docstring gives
    for the identical reason).

    Unlike renew(), the OFFER's price never depends on a prior lookup (it is
    computed straight from `declared_size_bytes` and this version's own
    `retention_days`, exactly like create()) -- the existence check below
    exists purely so an obviously-doomed request against an unknown/deleted
    backup_id is never charged for, not because pricing needs it.
    """
    if circuit_breaker.is_tripped(_RESOURCE_ADD_VERSION):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    declared_size_bytes, retention_days, validation_error = _parse_size_and_retention(request)
    if validation_error is not None:
        return validation_error

    backup_id = query_param(request.path_params.get("backup_id", ""))
    wallet = query_param(request.query_params.get("wallet", ""))

    backup = backup_service.get(wallet, backup_id)
    if backup is None or backup.status == STATUS_DELETED:
        return json_error_response(404, "not_found", "No backup with that id for that wallet")

    price = compute_price(declared_size_bytes, retention_days)
    offer = {
        "price": price,
        "resource": _RESOURCE_ADD_VERSION,
        "resource_path": "/api/v1/x402/storage/backups/{backup_id}/versions",
        "description": (
            "Store a new version's bytes (up to declared_size_bytes="
            f"{declared_size_bytes} bytes, billed by the KB actually used) for "
            f"retention_days={retention_days} days (optional query param, 1-"
            f"{settings.x402_storage_term_days}, default {settings.x402_storage_term_days} -- "
            "this version's OWN independent expiry, same shape as the initial store), "
            f"charged at max({settings.x402_storage_price_floor}, ceil(size/1KB) * "
            f"{settings.x402_storage_price_per_kb_per_90d}/KB * "
            f"retention_days/{settings.x402_storage_term_days}, same rate as the initial "
            "store) under this EXISTING backup_id, without discarding the versions "
            "already stored under it. This version becomes the backup's current "
            "content; the backup's own OVERALL retrieval window is a separate thing this "
            f"call never changes either way (capped at {settings.x402_storage_max_remaining_days} "
            "days remaining -- renew it separately to extend). Older versions keep "
            "expiring on their own original schedule, unaffected by this call. List "
            "every version with GET .../versions, or fetch one specific "
            "version's content with GET .../versions/{version}. Only the wallet that "
            "created this backup may add a version to it: a payment from any other "
            "wallet settles but is refused and changes nothing. " + _ENCRYPTION_GUIDE_POINTER
        ),
        "extensions": describe_json_endpoint(
            body_type="json",
            input={"data": "<base64>", "label": "my-agent-state-backup-v2"},
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
            output_example=_VERSION_OUTPUT_EXAMPLE,
        ),
    }
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    data, label, decode_error = _decode_upload_payload(request)
    if decode_error is not None:
        return decode_error

    result = require_paid_request(request, **offer)
    if result.error:
        return result.error

    # Same no-refund ownership contract as renew() -- re-checked here,
    # before ever calling run_with_refund, for the identical reason its own
    # docstring gives.
    attributed = (result.payer or "").strip()
    if not attributed or attributed != backup.wallet:
        return _ownership_refused(result, "add a version to it")

    outcome = run_with_refund(
        result,
        resource=_RESOURCE_ADD_VERSION,
        product_write=lambda: version_metadata_json(
            backup_service.add_version(
                backup,
                data=data,
                declared_size_bytes=declared_size_bytes,
                label=label,
                settlement_tx_id=result.payment_txid or "",
                retention_days=retention_days,
            )
        ),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource=_RESOURCE_ADD_VERSION)
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
    return {"items": [backup_metadata_json(item) for item in items]}


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

    return {**backup_metadata_json(backup), "data": base64.b64encode(data).decode("ascii")}


def x402_storage_list_versions(request: Request) -> Response | dict:
    """Free, wallet-signature-authenticated, owner-only: metadata for every version of one backup, newest-version first.

    404 covers not-found/not-owned/soft-deleted/expired for the BACKUP
    itself, same reasoning as x402_storage_get_backup -- checked explicitly
    here (rather than relying on an empty version list) so a non-owner or a
    made-up backup_id gets the same 404 every other owner-only route gives,
    not a silently-empty `items`.
    """
    wallet, error = _authenticate(request)
    if error is not None:
        return error

    backup_id = query_param(request.path_params.get("backup_id", ""))
    backup = backup_service.get_live(wallet, backup_id)
    if backup is None:
        return json_error_response(404, "not_found", "No live backup with that id for this wallet")

    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw_limit) if raw_limit else settings.x402_storage_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")

    items = backup_service.list_versions_live(wallet, backup_id, limit=limit)
    return {"items": [version_metadata_json(item) for item in items]}


def x402_storage_get_version(request: Request) -> Response | dict:
    """Free, wallet-signature-authenticated, owner-only: one specific version's metadata plus its restored bytes (base64), sha256-verified.

    Same 404/500 shape as x402_storage_get_backup, one level down: 404
    covers not-found/not-owned/soft-deleted/expired for either the backup
    or the specific version; a content_hash mismatch or an unreadable
    connector is a real internal error (500, logged at ERROR) -- never
    silently serve corrupted data.
    """
    wallet, error = _authenticate(request)
    if error is not None:
        return error

    backup_id = query_param(request.path_params.get("backup_id", ""))
    backup = backup_service.get_live(wallet, backup_id)
    if backup is None:
        return json_error_response(404, "not_found", "No live backup with that id for this wallet")

    raw_version = query_param(request.path_params.get("version", ""))
    try:
        version = int(raw_version)
    except ValueError:
        return json_error_response(400, "invalid_request", "version must be an integer")

    version_row = backup_service.get_version_live(wallet, backup_id, version)
    if version_row is None:
        return json_error_response(
            404, "not_found", "No live version with that number for this backup"
        )

    data = backup_service.read_version_bytes(version_row)
    if data is None:
        logger.error(
            "x402 storage: connector could not produce bytes for wallet=%s backup_id=%s version=%s connector=%s",
            wallet,
            backup_id,
            version,
            version_row.connector,
        )
        return json_error_response(
            500, "backup_unreadable", "This version's stored bytes could not be read"
        )

    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != version_row.content_hash:
        logger.error(
            "x402 storage: content_hash mismatch on version restore for wallet=%s backup_id=%s "
            "version=%s (expected=%s actual=%s)",
            wallet,
            backup_id,
            version,
            version_row.content_hash,
            actual_hash,
        )
        return json_error_response(
            500, "integrity_check_failed", "Stored data failed integrity verification"
        )

    return {**version_metadata_json(version_row), "data": base64.b64encode(data).decode("ascii")}


def x402_storage_delete_backup(request: Request) -> Response | dict:
    """Free, wallet-signature-authenticated, owner-only: delete one backup outright.

    Marks the row deleted first, then deletes the connector bytes (see
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
    """Register the free challenge route, paid create/renew/add-version, free list/detail/delete/versions, and the internal reaper.

    x402-marketplace-ux-audit.md section 3.3 "identity primitive" (N6):
    social's `auth/challenge` returns a session-token bearer, but this
    route's "challenge" is a single-use nonce presented as query params on
    the next call -- opposite contract, same path segment. `auth/nonce` is a
    second, direct registration against the identical handler; the old path
    is never removed (section 3.5, live-mainnet callers).
    """
    app.post("/api/v1/x402/storage/auth/challenge")(x402_storage_auth_challenge)
    app.post("/api/v1/x402/storage/auth/nonce")(x402_storage_auth_challenge)
    app.post("/api/v1/x402/storage/backups")(x402_storage_create_backup)
    app.get("/api/v1/x402/storage/backups")(x402_storage_list_backups)
    app.get("/api/v1/x402/storage/backups/:backup_id")(x402_storage_get_backup)
    app.post("/api/v1/x402/storage/backups/:backup_id/renew")(x402_storage_renew_backup)
    app.post("/api/v1/x402/storage/backups/:backup_id/versions")(x402_storage_add_version)
    app.get("/api/v1/x402/storage/backups/:backup_id/versions")(x402_storage_list_versions)
    app.get("/api/v1/x402/storage/backups/:backup_id/versions/:version")(x402_storage_get_version)
    app.delete("/api/v1/x402/storage/backups/:backup_id")(x402_storage_delete_backup)
    app.post("/api/v1/internal/x402/storage/reap")(x402_storage_reap)
