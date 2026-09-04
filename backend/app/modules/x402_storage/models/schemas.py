"""msgspec.Struct request bodies for x402 agent backup storage.

Decoded through app/core/serialization.py like every other route in this
backend -- pydantic is fully removed (CLAUDE.md section 5 / global rule).
Response bodies are plain dicts built by api/routes.py's own `_backup_json`,
the same convention every other x402 product's routes use.

GET and DELETE requests never carry a body in this backend's own convention
(see x402_directory/api/routes.py's admin-delete docstring), so the
wallet-signature auth proof for the free authenticated routes (list, detail,
delete) is read from query params directly in api/routes.py, not through a
Struct here -- there is nothing to decode a JSON body into on those routes.
"""

from __future__ import annotations

from typing import Annotated, Literal

import msgspec
from msgspec import Meta

# Same shape as x402_social/models/schemas.py's own WalletAddress -- not
# imported from there to keep this module self-contained (the task's own
# instruction: a small self-contained equivalent, not an x402_social import).
WalletAddress = Annotated[str, Meta(min_length=58, max_length=58)]

ProofMethod = Literal["legacy_message", "signed_bytes"]

# Mirrors services/backup_service.MAX_LABEL_LENGTH -- kept as a literal
# compile-time constant here (msgspec.Meta bounds must be compile-time
# constants) rather than imported, matching x402_social/models/schemas.py's
# own documented split between a compile-time shape bound here and the real
# runtime-configured cap enforced downstream.
_MAX_LABEL_LENGTH = 256

# Generous compile-time upper bound on the base64 request body so a wildly
# oversized upload 400s before it is even fully decoded -- the REAL cap is
# services/backup_service.validate_declared_size's x402_storage_max_backup_mb
# check, run before the payment gate. Base64 inflates by ~4/3, so 16 MiB of
# base64 covers the 10MB raw cap plus JSON wrapping. nginx client_max_body_size
# on the API vhost must stay above this (20M).
_MAX_DATA_B64_LENGTH = 16 * 1024 * 1024


class ChallengeRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /storage/auth/challenge."""

    wallet: WalletAddress


class BackupCreateRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /storage/backups. `data` is base64-encoded opaque bytes."""

    data: Annotated[str, Meta(max_length=_MAX_DATA_B64_LENGTH)]
    label: Annotated[str, Meta(max_length=_MAX_LABEL_LENGTH)] = ""


class BackupRenewRequest(msgspec.Struct, kw_only=True):
    """Request body for POST /storage/backups/:backup_id/renew.

    `wallet` here is ONLY a lookup hint for the free, pre-payment-gate
    existence/price check -- exactly the same trust level as
    X402ListingRenewRequest.url on the directory's own renew route. It is
    NOT itself proof of ownership: the actual authorization decision compares
    the REAL settled payment's `result.payer` against the row's own stored
    `wallet` column (api/routes.py's own docstring has the full reasoning) --
    this field never bypasses that check, it only tells the free lookup
    which Cassandra partition to read (x402_storage_backups is partitioned
    by wallet, so a point read needs it up front).
    """

    wallet: WalletAddress
