"""Reap backups whose grace window after expiry has elapsed.

The local-disk connector lives on the API host, so the actual delete runs
inside BackupService (this module is imported by the backend). A Celery
beat on the same box POSTs /api/v1/internal/x402/storage/reap to trigger
it -- workers cannot unlink files they do not mount.

Eligible: status=active AND expires_at + x402_storage_reaper_grace_days
<= now. During the grace window GET/list already hide the backup
(get_live), but POST .../renew still works so the owner can pay to keep
the bytes. After grace, this walk calls BackupService.delete (mark
deleted, then unlink).

Since migration 115 (versioned backups), this also sweeps
x402_storage_version_by_expiry -- one level down, same shape, for
historical version rows that have their OWN independent expiry (see
StoredBackupVersion's own docstring: a version's retention is never
inherited from an older version, the head, or a later renew of the head).
The one thing that shape borrows from the head sweep above it cannot
share: `_reap_one_version` must first confirm a due-looking version is not
still the backup's CURRENT content (StoredBackup.current_version) before
ever deleting its bytes -- the head and the versions table can otherwise
disagree about a version's remaining life whenever the head alone was
renewed.

While a version IS the backup's current content, BackupService.renew() and
add_version() keep its x402_storage_version_by_expiry projection row
re-keyed to track the head's own (moving) expiry (see
`resync_current_version_projection`'s own docstring) -- fixed 2026-09-06,
finding #3: before this, nothing ever touched that projection when only
the head moved, so a version that outlived enough renewals while current
had its projection day permanently fall outside
x402_storage_reaper_lookback_days, unreachable by this walk forever after,
even while its bytes were still the backup's live, served content. Once
such a version is later superseded, BackupService.add_version() hands it
back to its own frozen schedule itself (`_retire_superseded_version`),
reaping it immediately if that schedule is already overdue -- a
day-partition scan can never rescue a day this far in the past on its own.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.redis_client import get_redis
from app.modules.x402_storage.models.domain import (
    STATUS_ACTIVE,
    STATUS_DELETED,
    ExpiryIndexRow,
    VersionExpiryIndexRow,
)
from app.modules.x402_storage.services.backup_service import BackupService, grace_cutoff_epoch

logger = logging.getLogger(__name__)

_LOCK_KEY = "algorand:x402storage:reaper"


def _acquire_lock() -> bool:
    """True when this process holds the reaper lock, or Redis is down (fail open so a blip does not skip expiry forever)."""
    try:
        acquired = get_redis().set(
            _LOCK_KEY, "1", nx=True, ex=settings.x402_storage_reaper_lock_seconds
        )
    except Exception:
        logger.warning("x402 storage reaper: Redis lock unavailable, running anyway", exc_info=True)
        return True
    return bool(acquired)


def _release_lock() -> None:
    """Drop the reaper lock. Failures are logged, never raised."""
    try:
        get_redis().delete(_LOCK_KEY)
    except Exception:
        logger.warning("x402 storage reaper: failed to release Redis lock", exc_info=True)


def reap_expired(service: BackupService, *, now: datetime | None = None) -> dict[str, int | str]:
    """Delete backups (and, since migration 115, historical versions) past their grace window. Returns counts for the tick.

    `status` is "ok", "busy" (another tick holds the lock), or the walk ran.
    """
    if not _acquire_lock():
        return {
            "status": "busy",
            "reaped": 0,
            "skipped_in_grace": 0,
            "stale_projections": 0,
            "reaped_versions": 0,
            "skipped_versions_in_grace": 0,
            "stale_version_projections": 0,
        }

    moment = now or datetime.now(tz=UTC)
    grace_cutoff = grace_cutoff_epoch(moment)
    lookback = max(0, settings.x402_storage_reaper_lookback_days)
    batch = max(1, settings.x402_storage_reaper_batch)
    reaped = 0
    skipped_in_grace = 0
    stale_projections = 0
    reaped_versions = 0
    skipped_versions_in_grace = 0
    stale_version_projections = 0
    try:
        for day_offset in range(lookback + 1):
            expiry_day = moment.date() - timedelta(days=day_offset)
            rows = service.store.list_by_expiry_day(expiry_day, limit=batch)
            for index_row in rows:
                outcome = _reap_one(service, index_row, grace_cutoff=grace_cutoff)
                if outcome == "reaped":
                    reaped += 1
                elif outcome == "grace":
                    skipped_in_grace += 1
                else:
                    stale_projections += 1
            version_rows = service.store.list_versions_by_expiry_day(expiry_day, limit=batch)
            for version_index_row in version_rows:
                outcome = _reap_one_version(service, version_index_row, grace_cutoff=grace_cutoff)
                if outcome == "reaped":
                    reaped_versions += 1
                elif outcome == "grace":
                    skipped_versions_in_grace += 1
                else:
                    stale_version_projections += 1
    finally:
        _release_lock()
    return {
        "status": "ok",
        "reaped": reaped,
        "skipped_in_grace": skipped_in_grace,
        "stale_projections": stale_projections,
        "reaped_versions": reaped_versions,
        "skipped_versions_in_grace": skipped_versions_in_grace,
        "stale_version_projections": stale_version_projections,
    }


def _reap_one(service: BackupService, index_row: ExpiryIndexRow, *, grace_cutoff: int) -> str:
    """Reap, skip (still in grace), or drop a stale projection row. Never raises past a logged warning."""
    backup = service.get(index_row.wallet, index_row.backup_id)
    if (
        backup is None
        or backup.status == STATUS_DELETED
        or backup.expires_at_epoch != index_row.expires_at_epoch
    ):
        try:
            service.store.delete_expiry_index(index_row)
        except Exception:
            logger.warning(
                "x402 storage reaper: failed to drop stale projection wallet=%s backup_id=%s",
                index_row.wallet,
                index_row.backup_id,
                exc_info=True,
            )
        return "stale"
    if backup.expires_at_epoch > grace_cutoff:
        return "grace"
    try:
        service.delete(backup)
    except Exception:
        logger.warning(
            "x402 storage reaper: delete failed for wallet=%s backup_id=%s",
            backup.wallet,
            backup.backup_id,
            exc_info=True,
        )
        return "stale"
    return "reaped"


def _reap_one_version(
    service: BackupService, index_row: VersionExpiryIndexRow, *, grace_cutoff: int
) -> str:
    """Reap, skip (still in grace, or still the backup's current content), or drop a stale projection row for one historical version. Never raises past a logged warning.

    Unlike `_reap_one`, a due-looking version row is not automatically
    eligible: if it is still the backup's CURRENT content
    (StoredBackup.current_version), its lifecycle is governed by the head's
    own expiry/renew, not this independent per-version schedule -- deleting
    its bytes here would corrupt the head's own GET, so this case NEVER
    reaps, regardless of how due `index_row` looks.

    While current, this row is kept re-keyed to the head's OWN
    `expires_at_epoch` by BackupService.renew()/add_version() (see
    `resync_current_version_projection`'s own docstring, finding #3,
    2026-09-06) -- so under normal operation `index_row.expires_at_epoch`
    always equals `head.expires_at_epoch` here. A mismatch means this
    particular key is a stale leftover from before a re-key (the correct
    key already exists, or will shortly, at the head's real expiry), so it
    is dropped WITHOUT ever touching the version's bytes, same as any other
    stale-key case below.
    """
    version_row = service.store.get_version(
        index_row.wallet, index_row.backup_id, index_row.version
    )
    if version_row is None or version_row.status == STATUS_DELETED:
        _drop_stale_version_index(service, index_row)
        return "stale"

    head = service.get(index_row.wallet, index_row.backup_id)
    if (
        head is not None
        and head.status == STATUS_ACTIVE
        and head.current_version == index_row.version
    ):
        if index_row.expires_at_epoch != head.expires_at_epoch:
            _drop_stale_version_index(service, index_row)
            return "stale"
        return "grace"

    # Not the current version any more (superseded, or the head itself is
    # gone/deleted): governed by its own independent, frozen schedule --
    # same staleness/grace/reap shape as before this fix.
    if version_row.expires_at_epoch != index_row.expires_at_epoch:
        _drop_stale_version_index(service, index_row)
        return "stale"
    if version_row.expires_at_epoch > grace_cutoff:
        return "grace"
    try:
        service.delete_version(version_row)
    except Exception:
        logger.warning(
            "x402 storage reaper: version delete failed for wallet=%s backup_id=%s version=%s",
            version_row.wallet,
            version_row.backup_id,
            version_row.version,
            exc_info=True,
        )
        return "stale"
    return "reaped"


def _drop_stale_version_index(service: BackupService, index_row: VersionExpiryIndexRow) -> None:
    """Best-effort removal of one version-expiry projection row. Logged, never raised."""
    try:
        service.store.delete_version_expiry_index(index_row)
    except Exception:
        logger.warning(
            "x402 storage reaper: failed to drop stale version projection wallet=%s backup_id=%s version=%s",
            index_row.wallet,
            index_row.backup_id,
            index_row.version,
            exc_info=True,
        )
