"""msgspec.Struct request bodies for x402 agent backup storage.

Decoded through app/core/serialization.py like every other route in this
backend -- pydantic is fully removed (CLAUDE.md section 5 / global rule).
Response bodies are plain dicts built by api/routes.py's own `_backup_json`,
the same convention every other x402 product's routes use.

GET and DELETE requests never carry a body in this backend's own
convention, so the wallet-signature auth proof for the free authenticated
routes (list, detail,
delete) is read from query params directly in api/routes.py, not through a
Struct here -- there is nothing to decode a JSON body into on those routes.
"""

from __future__ import annotations

from typing import Annotated, Literal

import msgspec
from msgspec import Meta

# Shape bound for a 58-character Algorand address, defined locally so this
# module stays self-contained.
WalletAddress = Annotated[str, Meta(min_length=58, max_length=58)]

ProofMethod = Literal["legacy_message", "signed_bytes"]

# Mirrors services/backup_service.MAX_LABEL_LENGTH -- kept as a literal
# compile-time constant here (msgspec.Meta bounds must be compile-time
# constants) rather than imported: a compile-time shape bound here, the real
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


# POST /storage/backups/:backup_id/renew takes no body at all: `wallet` moved
# to a query param 2026-09-06 (api/routes.py's x402_storage_renew_backup own
# docstring has the full reasoning) so it is no longer decoded through a
# Struct here -- there used to be a BackupRenewRequest with just that one
# field.
