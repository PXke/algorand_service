"""Cassandra-backed backup metadata storage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.core.cassandra import get_cassandra_session
from app.core.statements import X402StorageStmts
from app.modules.x402_storage.models.domain import STATUS_ACTIVE, StoredBackup


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
    a well-formed but unknown id gets, instead of an unhandled 500.
    """
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        return None


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
    )


class CassandraBackupStore:
    """Cassandra-backed x402 storage backup metadata: one table, partitioned by wallet.

    Every access pattern is single-partition (list-by-wallet, point read by
    (wallet, backup_id)), always LIMITed on the list side -- no projection
    table is needed, unlike the directory/board's global feeds: this
    product's own routes never list across wallets.
    """

    def upsert(self, item: StoredBackup) -> None:
        """Full INSERT, never a partial UPDATE -- see CLAUDE.md section 3."""
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
            ),
        )

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
