"""Backup creation, retrieval, listing, renewal and deletion; pricing math."""

from __future__ import annotations

import hashlib
import logging
import random
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from x402.mechanisms.avm.utils import parse_money_to_decimal

from app.core.config import settings
from app.modules.x402_storage.backends.base import StorageBackend
from app.modules.x402_storage.backends.factory import get_storage_backend
from app.modules.x402_storage.models.domain import (
    STATUS_ACTIVE,
    STATUS_DELETED,
    StorageCapacityUnavailable,
    StorageError,
    StoredBackup,
)
from app.modules.x402_storage.stores.base import BackupStore
from app.modules.x402_storage.stores.factory import get_backup_store

logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1024 * 1024

# Bound on the free-text label, same "bounded, not shape-validated" treatment
# as x402_directory's `contact` field.
MAX_LABEL_LENGTH = 256


def _new_backup_id() -> str:
    """A fresh timeuuid-compatible id, with a random node instead of this process's real MAC address.

    Same reasoning and construction as x402_social's `_new_post_or_comment_id`
    (services/post_service.py): plain uuid.uuid1() embeds the host's NIC MAC
    address in the node field, which a public id should never leak. Forcing
    the multicast bit on a random 48-bit node is the standard RFC 4122 way to
    mint a time-based UUID with no real MAC in it, and Cassandra only
    validates the version nibble server-side, which this preserves.
    """
    return str(uuid.uuid1(node=random.getrandbits(48) | 0x010000000000))


def mb_units(size_bytes: int) -> int:
    """ceil(size_bytes / 1MB), minimum 1 -- a 1-byte backup still buys at least one priced unit."""
    return max(1, -(-max(0, size_bytes) // _BYTES_PER_MB))


def compute_price(declared_size_bytes: int) -> str:
    """The Money string to charge for a backup of `declared_size_bytes`: ceil(size/1MB) * x402_storage_price_per_mb."""
    per_mb = Decimal(str(parse_money_to_decimal(settings.x402_storage_price_per_mb)))
    total = per_mb * mb_units(declared_size_bytes)
    return f"${total:.6f}"


def validate_declared_size(declared_size_bytes: int) -> None:
    """Refuse a declared size that is not positive or exceeds the hard per-blob cap.

    Called on the FREE initial request, before the payment gate -- a cheap
    early rejection nobody pays for (declared_size_bytes must be stable
    across the 402 offer and the paid retry, so this check applies to both
    identically since both read it the same way, from the same query param).
    """
    if declared_size_bytes <= 0:
        raise StorageError("invalid_request", "declared_size_bytes must be a positive integer")
    cap_bytes = settings.x402_storage_max_backup_mb * _BYTES_PER_MB
    if declared_size_bytes > cap_bytes:
        raise StorageError(
            "declared_size_exceeds_cap",
            f"declared_size_bytes exceeds the {settings.x402_storage_max_backup_mb}MB "
            "per-backup cap",
            http_status=413,
        )


class BackupService:
    """Creates and reads stored backups."""

    def __init__(
        self, store: BackupStore | None = None, backend: StorageBackend | None = None
    ) -> None:
        """Take an explicit store/backend for tests; otherwise resolve the configured ones lazily."""
        self._store = store
        self._backend = backend

    @property
    def store(self) -> BackupStore:
        """The injected metadata store, or the process-wide one built from settings."""
        return self._store or get_backup_store()

    def backend_for(self, connector: str) -> StorageBackend | None:
        """The StorageBackend for `connector` -- the injected test backend if given, otherwise the real factory resolution."""
        if self._backend is not None:
            return self._backend
        return get_storage_backend(connector)

    def create(
        self,
        *,
        wallet: str,
        data: bytes,
        declared_size_bytes: int,
        label: str,
        settlement_tx_id: str,
        now: datetime | None = None,
    ) -> StoredBackup:
        """Store a paid backup and return its metadata row.

        Raises StorageError("size_mismatch") -- payment-kept, no-refund, a
        caller-fault rejection (see StorageError's own docstring) -- when the
        actual decoded byte count exceeds `declared_size_bytes`, which is
        what the payer was charged for.

        Raises StorageCapacityUnavailable -- refunded, see that exception's
        own docstring -- when the configured connector has no usable backend
        (unconfigured root, or an unimplemented connector name), when its
        usage_bytes() call itself fails, or when writing `data` would push
        usage_bytes() past x402_storage_local_max_total_mb. This is OUR
        capacity/configuration problem, not the payer's fault, so it is
        deliberately NOT a StorageError.

        Bytes are written to the connector BEFORE the metadata row is stored
        (store the durable artifact, then the row that says "this exists") --
        if the metadata write then fails, the bytes are an orphan the caller
        cannot yet reach, not a row pointing at bytes that were never written.
        """
        if len(data) > declared_size_bytes:
            raise StorageError(
                "size_mismatch",
                f"Actual upload size ({len(data)} bytes) exceeds declared_size_bytes "
                f"({declared_size_bytes}), which is what was charged. Payment has "
                "settled but nothing was stored.",
                http_status=422,
            )

        connector = settings.x402_storage_backend
        backend = self.backend_for(connector)
        if backend is None:
            raise StorageCapacityUnavailable(
                "No storage connector is currently configured/available."
            )

        ceiling_bytes = settings.x402_storage_local_max_total_mb * _BYTES_PER_MB
        try:
            current_usage = backend.usage_bytes()
        except Exception as exc:
            logger.error(
                "x402 storage: usage_bytes() failed on connector=%s", connector, exc_info=True
            )
            raise StorageCapacityUnavailable("Storage capacity could not be verified.") from exc
        if current_usage + len(data) > ceiling_bytes:
            raise StorageCapacityUnavailable("This storage connector is at capacity.")

        connector_params = backend.put(data)
        moment = now or datetime.now(tz=UTC)
        backup = StoredBackup(
            wallet=wallet,
            backup_id=_new_backup_id(),
            connector=connector,
            connector_params=connector_params,
            size_bytes=len(data),
            content_hash=hashlib.sha256(data).hexdigest(),
            label=label.strip()[:MAX_LABEL_LENGTH],
            created_at_epoch=int(moment.timestamp()),
            expires_at_epoch=int(
                (moment + timedelta(days=settings.x402_storage_term_days)).timestamp()
            ),
            status=STATUS_ACTIVE,
            settlement_tx_id=settlement_tx_id,
        )
        self.store.upsert(backup)
        return backup

    def get(self, wallet: str, backup_id: str) -> StoredBackup | None:
        """One backup row (any status), or None -- used by owner-only checks before deciding what a caller may do with it."""
        return self.store.get(wallet, backup_id)

    def get_live(
        self, wallet: str, backup_id: str, *, now: datetime | None = None
    ) -> StoredBackup | None:
        """One backup row, or None if it does not exist, is soft-deleted, or has expired."""
        moment = now or datetime.now(tz=UTC)
        found = self.store.get(wallet, backup_id)
        if found is None or not found.is_live(now_epoch=int(moment.timestamp())):
            return None
        return found

    def list_live(
        self, wallet: str, *, limit: int, now: datetime | None = None
    ) -> list[StoredBackup]:
        """This wallet's active, unexpired backups, newest first, clamped to x402_storage_max_results.

        Filtered after the LIMITed read, same accepted tradeoff as
        x402_directory.search() / x402_board.list_active(): a page can come
        back short when the front of this wallet's own history is full of
        deleted/expired rows.
        """
        moment = now or datetime.now(tz=UTC)
        cutoff = int(moment.timestamp())
        clamped = max(1, min(limit, settings.x402_storage_max_results))
        rows = self.store.list_recent(wallet, limit=clamped)
        return [row for row in rows if row.is_live(now_epoch=cutoff)]

    def read_bytes(self, backup: StoredBackup) -> bytes | None:
        """The stored bytes for one backup row, or None if the connector cannot produce them."""
        backend = self.backend_for(backup.connector)
        if backend is None:
            return None
        return backend.get(backup.connector_params)

    def delete(self, backup: StoredBackup) -> bool:
        """Delete the connector bytes, then mark the row deleted. Returns True once the row is marked.

        Bytes-then-row ordering (not the reverse): a crash between the two
        steps leaves a `status="deleted"` row pointing at already-gone bytes
        (a safe, user-facing 404 on any later read) rather than a `status=
        "active"` row whose bytes silently vanished from disk with nothing
        recording that they are gone -- the former is a clean failure mode,
        the latter is an actual, undetected disk leak.
        """
        backend = self.backend_for(backup.connector)
        if backend is not None:
            try:
                backend.delete(backup.connector_params)
            except Exception:
                logger.warning(
                    "x402 storage: connector delete failed for wallet=%s backup_id=%s "
                    "(marking the row deleted anyway)",
                    backup.wallet,
                    backup.backup_id,
                    exc_info=True,
                )
        self.store.upsert(replace(backup, status=STATUS_DELETED))
        return True

    def renew(
        self, backup: StoredBackup, *, settlement_tx_id: str, now: datetime | None = None
    ) -> StoredBackup:
        """Extend a backup's expiry by one more configured term and return it.

        The new expiry runs from max(now, current expires_at) -- same "later
        of now and current expiry" rule x402_directory's renew() uses:
        renewing early adds a full term on top of what is left, renewing
        after expiry starts a fresh one from now, and neither shortens what
        was already paid for. Nothing about the stored bytes or their
        connector changes.
        """
        moment = now or datetime.now(tz=UTC)
        base = max(int(moment.timestamp()), backup.expires_at_epoch)
        renewed = replace(
            backup,
            expires_at_epoch=int(
                (
                    datetime.fromtimestamp(base, tz=UTC)
                    + timedelta(days=settings.x402_storage_term_days)
                ).timestamp()
            ),
            settlement_tx_id=settlement_tx_id,
        )
        self.store.upsert(renewed)
        return renewed
