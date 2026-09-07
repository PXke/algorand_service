"""Domain types for agent backup storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from app.core.errors import PlatformError, http_status_for_code

STATUS_ACTIVE = "active"
STATUS_DELETED = "deleted"


class StorageError(PlatformError):
    """A storage-flow error CAUSED BY THE CALLER, mapped to an HTTP status.

    Raised only for a rejection the payer directly controls (today: only
    `size_mismatch` -- claiming a smaller declared_size_bytes than what was
    actually uploaded). Being a PlatformError means paid_request.
    run_with_refund treats this as its own documented "payment settles but
    is refused" contract: kept, never refunded, never counted against the
    circuit breaker. See StorageCapacityUnavailable below for the OTHER
    write-time failure shape (ours, not the caller's), which is deliberately
    NOT a StorageError/PlatformError for exactly that reason.
    """

    def __init__(self, code: str, message: str, *, http_status: int | None = None) -> None:
        """Map a storage error code to its HTTP status via http_status_for_code, unless given explicitly."""
        super().__init__(
            code,
            message,
            http_status=http_status if http_status is not None else http_status_for_code(code),
        )


class StorageCapacityUnavailable(Exception):
    """Raised when the storage connector cannot currently accept a write: no connector is configured/resolvable, usage_bytes() itself failed, or writing would exceed the configured local-disk ceiling.

    Deliberately NOT a StorageError/PlatformError. This is an operational
    failure on OUR side (misconfiguration or a full disk), not a
    caller-fault rejection the payer controls the way size_mismatch is -- so
    it should NOT get StorageError's payment-kept-no-refund treatment.
    Left to propagate out of backup_service.create()'s product_write, it
    reaches modules/x402/paid_request.run_with_refund's generic
    except-Exception branch instead of the PlatformError branch: the payer
    is refunded from the dedicated refund wallet, and the resource's
    circuit-breaker failure counter is incremented (see run_with_refund's
    own docstring). That breaker counter is exactly the right mechanism for
    this failure mode too -- a full disk is a sustained operational problem
    that should eventually stop accepting new paid writes until an operator
    frees capacity and resets the breaker, the same as any other unexpected
    product-write failure.
    """


@dataclass
class StoredBackup:
    """One backup's metadata row (x402_storage_backups).

    `connector` + `connector_params` say which StorageBackend wrote the bytes
    and how to find them again -- `connector_params` is an internal
    implementation detail and is NEVER served on the wire (see api/routes.py's
    _backup_json). `content_hash` is a sha256 hex digest of the actual bytes,
    checked again on every restore (GET .../:backup_id) so silent corruption
    is caught rather than served.

    `current_version` (migration 115) is the version number this row's own
    connector/connector_params/size_bytes/content_hash/label currently
    mirror -- 1 for a backup that has never had a version added (including
    every row written before migration 115, which reads back
    current_version=None, mapped to 1 in both stores' row mappers). This
    row is ALWAYS what unversioned GET/list callers see (CLAUDE.md-adjacent
    contract: "GET .../:backup_id still returns its current content the
    same way it does today"). See services/backup_service.add_version and
    StoredBackupVersion below for the per-version history this row's own
    lifecycle does NOT track independently -- this row's own expiry is
    still governed exactly as before (create/renew), unaffected by adding a
    version other than which bytes/hash/size it now points at.
    """

    wallet: str
    backup_id: str
    connector: str
    connector_params: dict[str, str] = field(default_factory=dict)
    size_bytes: int = 0
    content_hash: str = ""
    label: str = ""
    created_at_epoch: int = 0
    expires_at_epoch: int = 0
    status: str = STATUS_ACTIVE
    # Traces the row to the payment that bought its current term -- same
    # convention every other paid x402 product's stored row keeps
    # (StoredListing, StoredPlacement). Replaced by a renewal's own txid,
    # same as those.
    settlement_tx_id: str = ""
    current_version: int = 1

    @property
    def is_active(self) -> bool:
        """True unless this row has been soft-deleted."""
        return self.status == STATUS_ACTIVE

    def is_live(self, *, now_epoch: int) -> bool:
        """True when this row is active AND has not passed its expiry."""
        return self.is_active and self.expires_at_epoch > now_epoch


@dataclass(frozen=True, slots=True)
class ExpiryIndexRow:
    """One x402_storage_by_expiry projection row (migration 112).

    Enough to find the canonical backup and to delete THIS projection row
    if it has gone stale (the backup was renewed onto a new expires_at, or
    already deleted).
    """

    wallet: str
    backup_id: str
    expires_at_epoch: int


def expiry_day_utc(expires_at_epoch: int) -> date:
    """UTC calendar date of `expires_at_epoch` -- the expiry projection's partition key."""
    return datetime.fromtimestamp(expires_at_epoch, tz=UTC).date()


@dataclass
class StoredBackupVersion:
    """One version's metadata + content pointer row (x402_storage_backup_versions, migration 115).

    Written once at creation time (version 1 alongside every new
    StoredBackup, or version N+1 by BackupService.add_version), and never
    revised in place except the ONE later mutation delete()/delete_version()
    make: flipping `status` to STATUS_DELETED, same "full re-INSERT, never a
    partial UPDATE" rule as StoredBackup (CLAUDE.md section 3).

    `expires_at_epoch` is computed fresh from THIS version's own
    created_at_epoch (current_expires_at=None, exactly like a brand-new
    create()) -- deliberately NEVER inherited from an older version, from
    the head's remaining time, or extended by a later renew of the head.
    This is what keeps the per-version retention ceiling independent: an
    old, superseded version dies on its own original schedule regardless of
    what happens to the backup_id's current content. The version currently
    mirrored by the head (`StoredBackup.current_version`) is the one
    exception -- its lifecycle is governed by the head's own expiry/renew,
    not this row's own `expires_at_epoch` (see
    services/reaper.py's `_reap_one_version`).
    """

    wallet: str
    backup_id: str
    version: int
    connector: str
    connector_params: dict[str, str] = field(default_factory=dict)
    size_bytes: int = 0
    content_hash: str = ""
    label: str = ""
    created_at_epoch: int = 0
    expires_at_epoch: int = 0
    status: str = STATUS_ACTIVE
    settlement_tx_id: str = ""

    @property
    def is_active(self) -> bool:
        """True unless this row has been soft-deleted."""
        return self.status == STATUS_ACTIVE

    def is_live(self, *, now_epoch: int) -> bool:
        """True when this row is active AND has not passed its own expiry."""
        return self.is_active and self.expires_at_epoch > now_epoch


@dataclass(frozen=True, slots=True)
class VersionExpiryIndexRow:
    """One x402_storage_version_by_expiry projection row (migration 115).

    Same shape and purpose as ExpiryIndexRow, one level down: enough to
    find the canonical version row and to drop THIS projection row once it
    has gone stale (status flipped to deleted, or -- the one case
    ExpiryIndexRow never has to handle -- the version it points at is still
    the backup's CURRENT content, in which case the head's own expiry
    governs it and this row is dropped without touching the version or its
    bytes; see reaper.py's `_reap_one_version`).
    """

    wallet: str
    backup_id: str
    version: int
    expires_at_epoch: int
