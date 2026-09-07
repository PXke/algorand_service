"""Cassandra-backed backup metadata storage."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from app.core.cassandra import get_cassandra_session
from app.core.statements import X402StorageStmts
from app.modules.x402_storage.models.domain import (
    STATUS_ACTIVE,
    ExpiryIndexRow,
    StoredBackup,
    StoredBackupVersion,
    VersionExpiryIndexRow,
    expiry_day_utc,
)


def _dt(epoch: int) -> datetime:
    return datetime.fromtimestamp(epoch, tz=UTC)


def _epoch(value: datetime | None) -> int:
    """UTC epoch seconds from a stored timestamp.

    The Cassandra driver returns timezone-naive datetimes that are already
    UTC wall-clock values -- calling .timestamp() directly would make Python
    assume the server's LOCAL zone and silently shift the result (the exact
    bug class root-caused 2026-09-03 in x402_social/stores/cassandra.py and
    news/stores/cassandra.py before it).
    """
    if value is None:
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return int(value.timestamp())


def _uuid(value: str) -> uuid.UUID:
    """Parse a domain-layer backup_id string into the UUID object the driver's timeuuid codec needs.

    backup_id is minted as `str(uuid.uuid1(node=...))` in
    services/backup_service.py -- a real version-1 UUID, since Cassandra
    validates the version nibble server-side on a write to a timeuuid
    column. This raising form is for write paths, where the id was always
    just minted by this process; see `_try_uuid` for read paths that take a
    backup_id straight from external input.
    """
    return uuid.UUID(value)


def _try_uuid(value: str) -> uuid.UUID | None:
    """Like `_uuid`, but returns None instead of raising on a malformed id.

    Used by every read path that takes a backup_id straight from a URL path
    param, so a request for a malformed id resolves to the same "not found"
    a well-formed but unknown id gets, instead of an unhandled 500. A
    well-formed but non-version-1 UUID (the NIL uuid, a random v4) is "not
    found" too: backup_id is a `timeuuid` column and Cassandra rejects a
    non-v1 value bound to one with InvalidRequest, which the driver raises
    straight through to a 500 -- `uuid.UUID()` alone accepts it happily.
    """
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None
    # The raw version nibble, not `parsed.version`: Python reports None for
    # a non-RFC-4122 variant, whereas Cassandra's timeuuid check reads the
    # nibble alone -- so this accepts exactly what the server would.
    return parsed if (parsed.time_hi_version >> 12) == 1 else None


def _row_to_backup(row: object) -> StoredBackup:
    return StoredBackup(
        wallet=row.wallet,
        backup_id=str(row.backup_id),
        connector=row.connector or "",
        connector_params=dict(row.connector_params or {}),
        size_bytes=int(row.size_bytes or 0),
        content_hash=row.content_hash or "",
        label=row.label or "",
        created_at_epoch=_epoch(row.created_at),
        expires_at_epoch=_epoch(row.expires_at),
        status=row.status or STATUS_ACTIVE,
        settlement_tx_id=row.settlement_tx_id or "",
        # A pre-migration-115 row reads back current_version=None -- mapped
        # to 1, since every row written before that migration was, without
        # exception, a single-version backup. getattr guards a fake test
        # session's SimpleNamespace row that predates this column.
        current_version=int(getattr(row, "current_version", None) or 1),
    )


def _row_to_backup_version(row: object) -> StoredBackupVersion:
    return StoredBackupVersion(
        wallet=row.wallet,
        backup_id=str(row.backup_id),
        version=int(row.version),
        connector=row.connector or "",
        connector_params=dict(row.connector_params or {}),
        size_bytes=int(row.size_bytes or 0),
        content_hash=row.content_hash or "",
        label=row.label or "",
        created_at_epoch=_epoch(row.created_at),
        expires_at_epoch=_epoch(row.expires_at),
        status=row.status or STATUS_ACTIVE,
        settlement_tx_id=row.settlement_tx_id or "",
    )


class CassandraBackupStore:
    """Cassandra-backed x402 storage backup metadata: canonical table plus expiry projection.

    Every owner-facing access pattern is single-partition (list-by-wallet,
    point read by (wallet, backup_id)), always LIMITed on the list side.
    The reaper is the one cross-wallet reader, and it goes through
    x402_storage_by_expiry (migration 112), never ALLOW FILTERING.
    """

    def upsert(self, item: StoredBackup) -> None:
        """Full INSERT of the canonical row, then the expiry projection -- see CLAUDE.md section 3.

        Previous projection key is deleted only when it would not be
        overwritten in place (expires_at moved, or the row is no longer
        active). A crash between canonical write and projection leaves the
        backup reachable by its owner (point read still works) and missing
        from the reaper index until the next renew/delete rewrites it.
        """
        previous = self.get(item.wallet, item.backup_id)
        session = get_cassandra_session()
        session.execute(
            X402StorageStmts.UPSERT_BACKUP,
            (
                item.wallet,
                _uuid(item.backup_id),
                item.connector,
                dict(item.connector_params),
                item.size_bytes,
                item.content_hash,
                item.label,
                _dt(item.created_at_epoch),
                _dt(item.expires_at_epoch),
                item.status,
                item.settlement_tx_id,
                item.current_version,
            ),
        )
        self._sync_expiry_projection(item, previous)

    def get(self, wallet: str, backup_id: str) -> StoredBackup | None:
        """Point read by (wallet, backup_id); None for a malformed id or a row that does not exist."""
        parsed = _try_uuid(backup_id)
        if parsed is None:
            return None
        session = get_cassandra_session()
        row = session.execute(X402StorageStmts.GET_BACKUP, (wallet, parsed)).one()
        return None if row is None else _row_to_backup(row)

    def list_recent(self, wallet: str, *, limit: int) -> list[StoredBackup]:
        """Up to `limit` of this wallet's rows, newest first (the table's own CLUSTERING ORDER BY (backup_id DESC))."""
        session = get_cassandra_session()
        rows = session.execute(X402StorageStmts.LIST_RECENT, (wallet, limit))
        return [_row_to_backup(row) for row in rows]

    def list_by_expiry_day(self, expiry_day: date, *, limit: int) -> list[ExpiryIndexRow]:
        """Up to `limit` projection rows for one UTC expiry day, oldest expires_at first."""
        session = get_cassandra_session()
        rows = session.execute(X402StorageStmts.LIST_BY_EXPIRY_DAY, (expiry_day, limit))
        return [
            ExpiryIndexRow(
                wallet=row.wallet,
                backup_id=str(row.backup_id),
                expires_at_epoch=_epoch(row.expires_at),
            )
            for row in rows
        ]

    def delete_expiry_index(self, row: ExpiryIndexRow) -> None:
        """Remove one expiry-projection row by its full primary key."""
        parsed = _try_uuid(row.backup_id)
        if parsed is None:
            return
        session = get_cassandra_session()
        session.execute(
            X402StorageStmts.DELETE_BY_EXPIRY,
            (expiry_day_utc(row.expires_at_epoch), _dt(row.expires_at_epoch), row.wallet, parsed),
        )

    def _sync_expiry_projection(self, item: StoredBackup, previous: StoredBackup | None) -> None:
        """Refresh the expiry projection for `item`, dropping `previous`'s key when it moved or went inactive."""
        session = get_cassandra_session()
        new_key = (
            (expiry_day_utc(item.expires_at_epoch), item.expires_at_epoch, item.backup_id)
            if item.status == STATUS_ACTIVE
            else None
        )
        if previous is not None and previous.status == STATUS_ACTIVE:
            old_key = (
                expiry_day_utc(previous.expires_at_epoch),
                previous.expires_at_epoch,
                previous.backup_id,
            )
            if old_key != new_key:
                parsed = _try_uuid(previous.backup_id)
                if parsed is not None:
                    session.execute(
                        X402StorageStmts.DELETE_BY_EXPIRY,
                        (
                            old_key[0],
                            _dt(previous.expires_at_epoch),
                            previous.wallet,
                            parsed,
                        ),
                    )
        if new_key is not None:
            session.execute(
                X402StorageStmts.INSERT_BY_EXPIRY,
                (
                    new_key[0],
                    _dt(item.expires_at_epoch),
                    item.wallet,
                    _uuid(item.backup_id),
                ),
            )

    def upsert_version(self, item: StoredBackupVersion) -> None:
        """Full INSERT of one version row, then sync its own expiry projection.

        A version row's primary key (wallet, backup_id, version) never
        changes across its lifetime, so unlike the canonical row's upsert
        there is only ever one possible previous projection key to drop
        (this same key, once the row is no longer active) -- no
        expires_at-moved case exists because a version's `expires_at_epoch`
        is fixed at creation and never rewritten in place (see
        StoredBackupVersion's own docstring).
        """
        session = get_cassandra_session()
        session.execute(
            X402StorageStmts.UPSERT_BACKUP_VERSION,
            (
                item.wallet,
                _uuid(item.backup_id),
                item.version,
                item.connector,
                dict(item.connector_params),
                item.size_bytes,
                item.content_hash,
                item.label,
                _dt(item.created_at_epoch),
                _dt(item.expires_at_epoch),
                item.status,
                item.settlement_tx_id,
            ),
        )
        if item.status != STATUS_ACTIVE:
            session.execute(
                X402StorageStmts.DELETE_VERSION_BY_EXPIRY,
                (
                    expiry_day_utc(item.expires_at_epoch),
                    _dt(item.expires_at_epoch),
                    item.wallet,
                    _uuid(item.backup_id),
                    item.version,
                ),
            )
        else:
            session.execute(
                X402StorageStmts.INSERT_VERSION_BY_EXPIRY,
                (
                    expiry_day_utc(item.expires_at_epoch),
                    _dt(item.expires_at_epoch),
                    item.wallet,
                    _uuid(item.backup_id),
                    item.version,
                ),
            )

    def insert_new_version_if_absent(self, item: StoredBackupVersion) -> bool:
        """Claim (wallet, backup_id, item.version) via a lightweight transaction (IF NOT EXISTS). True if this call created it -- see BackupStore's own docstring for why add_version() must use this instead of upsert_version."""
        session = get_cassandra_session()
        result = session.execute(
            X402StorageStmts.INSERT_BACKUP_VERSION_IF_ABSENT,
            (
                item.wallet,
                _uuid(item.backup_id),
                item.version,
                item.connector,
                dict(item.connector_params),
                item.size_bytes,
                item.content_hash,
                item.label,
                _dt(item.created_at_epoch),
                _dt(item.expires_at_epoch),
                item.status,
                item.settlement_tx_id,
            ),
        )
        if not result.was_applied:
            return False
        if item.status == STATUS_ACTIVE:
            session.execute(
                X402StorageStmts.INSERT_VERSION_BY_EXPIRY,
                (
                    expiry_day_utc(item.expires_at_epoch),
                    _dt(item.expires_at_epoch),
                    item.wallet,
                    _uuid(item.backup_id),
                    item.version,
                ),
            )
        return True

    def resync_current_version_projection(
        self, *, wallet: str, backup_id: str, version: int, previous_epoch: int, new_epoch: int
    ) -> None:
        """Delete the projection key at `previous_epoch` (if it differs) and insert one at `new_epoch`, WITHOUT touching the canonical version row -- see BackupStore's own docstring."""
        if previous_epoch == new_epoch:
            return
        parsed = _try_uuid(backup_id)
        if parsed is None:
            return
        session = get_cassandra_session()
        if previous_epoch:
            session.execute(
                X402StorageStmts.DELETE_VERSION_BY_EXPIRY,
                (expiry_day_utc(previous_epoch), _dt(previous_epoch), wallet, parsed, version),
            )
        session.execute(
            X402StorageStmts.INSERT_VERSION_BY_EXPIRY,
            (expiry_day_utc(new_epoch), _dt(new_epoch), wallet, parsed, version),
        )

    def get_version(self, wallet: str, backup_id: str, version: int) -> StoredBackupVersion | None:
        """Point read by (wallet, backup_id, version); None for a malformed id or a row that does not exist."""
        parsed = _try_uuid(backup_id)
        if parsed is None:
            return None
        session = get_cassandra_session()
        row = session.execute(X402StorageStmts.GET_BACKUP_VERSION, (wallet, parsed, version)).one()
        return None if row is None else _row_to_backup_version(row)

    def list_versions(
        self, wallet: str, backup_id: str, *, limit: int
    ) -> list[StoredBackupVersion]:
        """Up to `limit` of one backup's version rows, newest-version-first (CLUSTERING ORDER BY (version DESC))."""
        parsed = _try_uuid(backup_id)
        if parsed is None:
            return []
        session = get_cassandra_session()
        rows = session.execute(X402StorageStmts.LIST_BACKUP_VERSIONS, (wallet, parsed, limit))
        return [_row_to_backup_version(row) for row in rows]

    def list_versions_by_expiry_day(
        self, expiry_day: date, *, limit: int
    ) -> list[VersionExpiryIndexRow]:
        """Up to `limit` version-projection rows for one UTC expiry day, oldest expires_at first."""
        session = get_cassandra_session()
        rows = session.execute(X402StorageStmts.LIST_VERSIONS_BY_EXPIRY_DAY, (expiry_day, limit))
        return [
            VersionExpiryIndexRow(
                wallet=row.wallet,
                backup_id=str(row.backup_id),
                version=int(row.version),
                expires_at_epoch=_epoch(row.expires_at),
            )
            for row in rows
        ]

    def delete_version_expiry_index(self, row: VersionExpiryIndexRow) -> None:
        """Remove one version-expiry-projection row by its full primary key."""
        parsed = _try_uuid(row.backup_id)
        if parsed is None:
            return
        session = get_cassandra_session()
        session.execute(
            X402StorageStmts.DELETE_VERSION_BY_EXPIRY,
            (
                expiry_day_utc(row.expires_at_epoch),
                _dt(row.expires_at_epoch),
                row.wallet,
                parsed,
                row.version,
            ),
        )
