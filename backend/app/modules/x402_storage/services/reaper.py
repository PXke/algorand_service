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
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from app.core.config import settings
from app.core.redis_client import get_redis
from app.modules.x402_storage.models.domain import STATUS_DELETED, ExpiryIndexRow
from app.modules.x402_storage.services.backup_service import BackupService

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
    """Delete backups past their grace window. Returns counts for the tick.

    `status` is "ok", "busy" (another tick holds the lock), or the walk ran.
    """
    if not _acquire_lock():
        return {"status": "busy", "reaped": 0, "skipped_in_grace": 0, "stale_projections": 0}

    moment = now or datetime.now(tz=UTC)
    grace_cutoff = int(
        (moment - timedelta(days=settings.x402_storage_reaper_grace_days)).timestamp()
    )
    lookback = max(0, settings.x402_storage_reaper_lookback_days)
    batch = max(1, settings.x402_storage_reaper_batch)
    reaped = 0
    skipped_in_grace = 0
    stale_projections = 0
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
    finally:
        _release_lock()
    return {
        "status": "ok",
        "reaped": reaped,
        "skipped_in_grace": skipped_in_grace,
        "stale_projections": stale_projections,
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
