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
    StoredBackupVersion,
    VersionExpiryIndexRow,
)
from app.modules.x402_storage.stores.base import BackupStore
from app.modules.x402_storage.stores.factory import get_backup_store

logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1024 * 1024
_BYTES_PER_KB = 1024

# Lower bound for a caller-chosen retention_days (create()/add_version()).
# The upper bound is x402_storage_term_days itself (config has one owner --
# there is no separate "max retention" setting), so it is read from settings
# at call time in validate_retention_days() rather than duplicated here.
MIN_RETENTION_DAYS = 1

# Bound on the free-text label: bounded, not shape-validated.
MAX_LABEL_LENGTH = 256


def _new_backup_id() -> str:
    """A fresh timeuuid-compatible id, with a random node instead of this process's real MAC address.

    Plain uuid.uuid1() embeds the host's NIC MAC address in the node field,
    which a public id should never leak. Forcing
    the multicast bit on a random 48-bit node is the standard RFC 4122 way to
    mint a time-based UUID with no real MAC in it, and Cassandra only
    validates the version nibble server-side, which this preserves.
    """
    return str(uuid.uuid1(node=random.getrandbits(48) | 0x010000000000))


def _version_from_head(head: StoredBackup) -> StoredBackupVersion:
    """Synthesize the implicit version row a pre-migration-115 backup never had written for it.

    Only ever used for a backup whose versions table has zero rows at all
    (see get_version_live/list_versions_live) -- every backup created since
    migration 115 shipped always has a real version-1 row from create(), so
    this fallback is purely a "existing rows keep working with zero
    migration pain" affordance for data that predates the feature.
    """
    return StoredBackupVersion(
        wallet=head.wallet,
        backup_id=head.backup_id,
        version=head.current_version,
        connector=head.connector,
        connector_params=head.connector_params,
        size_bytes=head.size_bytes,
        content_hash=head.content_hash,
        label=head.label,
        created_at_epoch=head.created_at_epoch,
        expires_at_epoch=head.expires_at_epoch,
        status=head.status,
        settlement_tx_id=head.settlement_tx_id,
    )


def _version_is_live(
    version_row: StoredBackupVersion, head: StoredBackup | None, *, now_epoch: int
) -> bool:
    """Whether `version_row` should be treated as live right now.

    Always requires `is_active` (a soft-deleted version is never live). For
    everything else: if `version_row` is the backup's CURRENT content
    (`head.current_version == version_row.version`), liveness defers to the
    HEAD's own `expires_at_epoch`, never the version row's own -- per
    `StoredBackupVersion`'s own docstring, this is the one version whose
    lifecycle the head governs. A non-current (superseded, or pre-migration
    legacy synthesized) version keeps judging itself against its own
    independent `expires_at_epoch` (finding #4, 2026-09-06: before this fix,
    a version's own stale row governed even the CURRENT version, so
    GET/list of the current version by number silently broke as soon as a
    renew extended only the head).
    """
    if not version_row.is_active:
        return False
    if (
        head is not None
        and head.status == STATUS_ACTIVE
        and head.current_version == version_row.version
    ):
        return head.expires_at_epoch > now_epoch
    return version_row.expires_at_epoch > now_epoch


def kb_units(size_bytes: int) -> int:
    """ceil(size_bytes / 1KB), minimum 1 -- a 1-byte backup still buys at least one priced unit."""
    return max(1, -(-max(0, size_bytes) // _BYTES_PER_KB))


def validate_retention_days(retention_days: int) -> None:
    """Refuse a retention_days outside [MIN_RETENTION_DAYS, x402_storage_term_days].

    Called on the FREE initial request, before the payment gate -- same
    "cheap early rejection nobody pays for" contract as
    validate_declared_size (retention_days must be stable across the 402
    offer and the paid retry, so this applies identically to both, reading
    the same query param both times).
    """
    ceiling = settings.x402_storage_term_days
    if retention_days < MIN_RETENTION_DAYS or retention_days > ceiling:
        raise StorageError(
            "invalid_request",
            f"retention_days must be between {MIN_RETENTION_DAYS} and {ceiling}",
        )


def compute_price(declared_size_bytes: int, retention_days: int | None = None) -> str:
    """The Money string to charge: max(x402_storage_price_floor, ceil(size/1KB) * x402_storage_price_per_kb_per_90d * retention_days/x402_storage_term_days).

    `retention_days` defaults to the full x402_storage_term_days when not
    given -- renew() always charges for one full fresh term this way (see
    its own docstring), the same shape create()/add_version() had before
    `retention_days` became a real caller input. Billed by the KB actually
    used (kb_units), never rounded up to a whole MB, so a small file at a
    short retention is never charged as if it were a full-size, full-term
    backup. The floor exists because the linear KB*day math would otherwise
    round a tiny short-retention request down to an unsettleable fraction of
    a cent.
    """
    days = retention_days if retention_days is not None else settings.x402_storage_term_days
    per_kb_per_term = Decimal(
        str(parse_money_to_decimal(settings.x402_storage_price_per_kb_per_90d))
    )
    floor_price = Decimal(str(parse_money_to_decimal(settings.x402_storage_price_floor)))
    scaled = (
        per_kb_per_term
        * kb_units(declared_size_bytes)
        * Decimal(days)
        / Decimal(settings.x402_storage_term_days)
    )
    total = max(floor_price, scaled)
    return f"${total:.6f}"


def max_remaining_epoch(now: datetime) -> int:
    """UTC epoch of now + x402_storage_max_remaining_days -- the hard remaining-term ceiling."""
    return int((now + timedelta(days=settings.x402_storage_max_remaining_days)).timestamp())


def compute_expiry_epoch(
    now: datetime, *, current_expires_at: int | None, term_days: int | None = None
) -> int:
    """The expires_at to write for a create/add_version (current=None) or renew.

    Adds `term_days` (defaults to the full x402_storage_term_days when not
    given -- renew()'s own call site never passes it, so a renewal always
    tries to add one full fresh term) onto max(now, current expiry), then
    caps at now + x402_storage_max_remaining_days so remaining storage can
    never exceed that ceiling. create()/add_version() pass the caller's own
    validated `retention_days` here, so a shorter chosen retention writes a
    correspondingly shorter expiry -- consistent with compute_price() having
    charged less for it.
    """
    now_epoch = int(now.timestamp())
    base = now_epoch if current_expires_at is None else max(now_epoch, current_expires_at)
    days = term_days if term_days is not None else settings.x402_storage_term_days
    proposed = int((datetime.fromtimestamp(base, tz=UTC) + timedelta(days=days)).timestamp())
    return min(proposed, max_remaining_epoch(now))


def grace_cutoff_epoch(now: datetime) -> int:
    """UTC epoch of now - x402_storage_reaper_grace_days -- an expiry at or before this is past its grace window and eligible to be reaped.

    The single source for this computation (CLAUDE.md section 3): shared by
    reaper.py's own periodic sweep and this module's `_retire_superseded_version`,
    which needs the identical cutoff to decide whether a just-superseded
    version must be reaped immediately rather than merely re-keyed (finding
    #3, 2026-09-06) -- a day-partition scan can never rescue a day already
    past x402_storage_reaper_lookback_days, so that one transition has to
    make the call itself instead of waiting for a tick.
    """
    return int((now - timedelta(days=settings.x402_storage_reaper_grace_days)).timestamp())


def at_remaining_cap(backup: StoredBackup, *, now: datetime | None = None) -> bool:
    """True when this backup already has the maximum remaining retrievable life, so a renew would not extend it."""
    moment = now or datetime.now(tz=UTC)
    return backup.expires_at_epoch >= max_remaining_epoch(moment)


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

    def _write_bytes(self, data: bytes) -> tuple[str, dict[str, str]]:
        """Resolve the configured connector, capacity-check, and write `data`. Returns (connector, connector_params).

        Shared by create() and add_version() -- the only two call sites that
        ever write NEW bytes to the connector.

        Raises StorageCapacityUnavailable -- refunded, see that exception's
        own docstring -- when the configured connector has no usable backend
        (unconfigured root, or an unimplemented connector name), when its
        usage_bytes() call itself fails, or when writing `data` would push
        usage_bytes() past x402_storage_local_max_total_mb. This is OUR
        capacity/configuration problem, not the payer's fault, so it is
        deliberately NOT a StorageError.
        """
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

        return connector, backend.put(data)

    def create(
        self,
        *,
        wallet: str,
        data: bytes,
        declared_size_bytes: int,
        label: str,
        settlement_tx_id: str,
        retention_days: int | None = None,
        now: datetime | None = None,
    ) -> StoredBackup:
        """Store a paid backup and return its metadata row.

        `retention_days` (validated by the caller via validate_retention_days
        before the payment gate) sets how long this term's expiry actually
        runs -- defaults to the full x402_storage_term_days when not given,
        the same "always the max term" shape this had before retention_days
        became a real caller input.

        Raises StorageError("size_mismatch") -- payment-kept, no-refund, a
        caller-fault rejection (see StorageError's own docstring) -- when the
        actual decoded byte count exceeds `declared_size_bytes`, which is
        what the payer was charged for.

        Raises StorageCapacityUnavailable, see _write_bytes's own docstring.

        Bytes are written to the connector BEFORE the metadata row is stored
        (store the durable artifact, then the row that says "this exists") --
        if the metadata write then fails, the bytes are an orphan the caller
        cannot yet reach, not a row pointing at bytes that were never written.

        Also writes this backup's implicit version 1 into the versions
        table (migration 115) -- every backup created from here on has a
        real version-1 row from day one, so list_versions_live never has to
        fall back to synthesizing one except for a pre-115 legacy backup.
        """
        if len(data) > declared_size_bytes:
            raise StorageError(
                "size_mismatch",
                f"Actual upload size ({len(data)} bytes) exceeds declared_size_bytes "
                f"({declared_size_bytes}), which is what was charged. Payment has "
                "settled but nothing was stored.",
                http_status=422,
            )

        connector, connector_params = self._write_bytes(data)
        moment = now or datetime.now(tz=UTC)
        created_at_epoch = int(moment.timestamp())
        expires_at_epoch = compute_expiry_epoch(
            moment, current_expires_at=None, term_days=retention_days
        )
        content_hash = hashlib.sha256(data).hexdigest()
        trimmed_label = label.strip()[:MAX_LABEL_LENGTH]
        backup = StoredBackup(
            wallet=wallet,
            backup_id=_new_backup_id(),
            connector=connector,
            connector_params=connector_params,
            size_bytes=len(data),
            content_hash=content_hash,
            label=trimmed_label,
            created_at_epoch=created_at_epoch,
            expires_at_epoch=expires_at_epoch,
            status=STATUS_ACTIVE,
            settlement_tx_id=settlement_tx_id,
            current_version=1,
        )
        self.store.upsert(backup)
        self.store.upsert_version(
            StoredBackupVersion(
                wallet=wallet,
                backup_id=backup.backup_id,
                version=1,
                connector=connector,
                connector_params=connector_params,
                size_bytes=len(data),
                content_hash=content_hash,
                label=trimmed_label,
                created_at_epoch=created_at_epoch,
                expires_at_epoch=expires_at_epoch,
                status=STATUS_ACTIVE,
                settlement_tx_id=settlement_tx_id,
            )
        )
        return backup

    def add_version(
        self,
        backup: StoredBackup,
        *,
        data: bytes,
        declared_size_bytes: int,
        label: str,
        settlement_tx_id: str,
        retention_days: int | None = None,
        now: datetime | None = None,
    ) -> StoredBackupVersion:
        """Store a new version's bytes under an EXISTING backup_id and make it the head's current content.

        Priced and capacity-checked exactly like create() -- same
        size_mismatch / StorageCapacityUnavailable contract, see create()'s
        own docstring; the caller (api/routes.py) computes the price from
        `declared_size_bytes` (and this same `retention_days`) the same way
        create()'s route does, never from `backup`'s existing stored size.

        This version's own `expires_at_epoch` is computed fresh from ITS OWN
        created_at using `retention_days` (current_expires_at=None, exactly
        like a brand-new create(), including the same "cheaper for a shorter
        chosen retention" pricing shape) -- deliberately NEVER derived from
        `backup`'s current remaining time or from an older version's expiry,
        so adding a version can never silently stack extra retention onto
        the backup_id as a whole. Older versions keep expiring on their own
        original schedule, untouched by this call -- see StoredBackupVersion's
        own docstring and reaper.py's `_reap_one_version`.

        The HEAD's own `expires_at_epoch` is UNAFFECTED by this call (fixed
        2026-09-06, finding #5 -- it used to be silently overwritten with
        this version's fresh schedule, contradicting the very docstring on
        `StoredBackup.current_version` that already documented "unaffected"
        as the intended behavior). Only the CURRENT version's projection
        row is re-keyed to track the head's real, unchanged expiry (see
        `resync_current_version_projection`'s own docstring) -- its
        canonical row keeps its own fresh schedule, dormant until it is
        later superseded (see `_retire_superseded_version`).

        Raises StorageError("version_conflict") -- payment kept, no refund,
        same caller-controlled-rejection contract as size_mismatch (see
        StorageError's own docstring) -- when a concurrent add_version call
        already claimed this exact next version number first (finding #5,
        2026-09-06): `backup.current_version` was read by both callers
        before either wrote, so two concurrent calls compute the identical
        next_version. Racing your own concurrent writes against the same
        backup_id is squarely within the caller's own control, the same
        reasoning StorageError's docstring already applies to size_mismatch
        and this route's own ownership-mismatch rejections.

        Store-before-mark ordering: the new version row (the durable
        artifact) is written before the head is updated to point at it (the
        flag that says "this is now current"), same reasoning as create()'s
        own bytes-before-row ordering. The head repoint itself now happens
        immediately once the version row is durably claimed (see
        `_finish_promoting_version`), and a losing `insert_new_version_if_absent`
        call first checks whether it lost to a genuine concurrent winner or
        to an earlier caller's own write that got interrupted between those
        two steps -- and if the latter, finishes that interrupted repoint
        before rejecting itself (see `_recover_orphaned_head_if_needed`) --
        so a transient failure in that narrow window can never permanently
        wedge this backup_id at 409 (second-round adversarial review,
        2026-09-06). Retiring whatever version this call supersedes always
        happens AFTER the head is repointed, never before: deleting the old
        version's bytes while the head might still be pointing at them
        would risk a live GET 500ing `backup_unreadable` (same finding).
        """
        if len(data) > declared_size_bytes:
            raise StorageError(
                "size_mismatch",
                f"Actual upload size ({len(data)} bytes) exceeds declared_size_bytes "
                f"({declared_size_bytes}), which is what was charged. Payment has "
                "settled but nothing was stored.",
                http_status=422,
            )

        connector, connector_params = self._write_bytes(data)
        moment = now or datetime.now(tz=UTC)
        expected_current_version = backup.current_version
        next_version = expected_current_version + 1
        content_hash = hashlib.sha256(data).hexdigest()
        trimmed_label = label.strip()[:MAX_LABEL_LENGTH]
        version_row = StoredBackupVersion(
            wallet=backup.wallet,
            backup_id=backup.backup_id,
            version=next_version,
            connector=connector,
            connector_params=connector_params,
            size_bytes=len(data),
            content_hash=content_hash,
            label=trimmed_label,
            created_at_epoch=int(moment.timestamp()),
            expires_at_epoch=compute_expiry_epoch(
                moment, current_expires_at=None, term_days=retention_days
            ),
            status=STATUS_ACTIVE,
            settlement_tx_id=settlement_tx_id,
        )
        if not self.store.insert_new_version_if_absent(version_row):
            # Lost the race. Two possible causes: (1) a genuine concurrent
            # winner already finished the whole sequence below, in which
            # case the head already reflects next_version and there is
            # nothing to do; or (2) an earlier caller's own LWT succeeded
            # but it crashed/failed before ever repointing the head, in
            # which case every future add_version would recompute this same
            # next_version and lose the identical race forever -- a
            # permanent 409 wedge (second-round adversarial review,
            # 2026-09-06). Distinguish and repair case (2) before rejecting
            # this call, so the backup_id is never left permanently stuck.
            self._recover_orphaned_head_if_needed(
                wallet=backup.wallet,
                backup_id=backup.backup_id,
                expected_current_version=expected_current_version,
                next_version=next_version,
                now=moment,
            )
            # This call's own bytes are unreachable by anyone either way --
            # its version row was never actually created (the LWT rejected
            # it), so there is no row for these bytes to ever hang off of.
            # Clean them up rather than leaving a second, differently-shaped
            # orphan behind the very rejection meant to prevent one.
            self._delete_connector_bytes(
                connector,
                connector_params,
                wallet=backup.wallet,
                backup_id=backup.backup_id,
                reason=(
                    "lost the add_version version-number race before any row for "
                    "these bytes was ever created -- this blob is now a permanent, "
                    "unreconciled orphan with nothing left pointing at it"
                ),
                level=logging.ERROR,
            )
            raise StorageError(
                "version_conflict",
                f"Another request already added version {next_version} to this backup "
                "concurrently. Payment has settled but nothing new was stored -- list "
                "versions and retry against the current one.",
                http_status=409,
            )

        self._finish_promoting_version(head=backup, new_version_row=version_row, now=moment)
        return version_row

    def _recover_orphaned_head_if_needed(
        self,
        *,
        wallet: str,
        backup_id: str,
        expected_current_version: int,
        next_version: int,
        now: datetime,
    ) -> None:
        """After losing insert_new_version_if_absent's LWT, finish an earlier caller's interrupted write if that -- rather than a genuine live conflict -- is why it lost.

        A losing LWT has exactly two possible causes: (1) a genuine
        concurrent winner already completed the full add_version sequence,
        in which case the head's own `current_version` already reflects
        `next_version` (or later) -- an ordinary conflict, nothing to
        repair; or (2) some earlier caller's LWT succeeded but a transient
        failure struck before that caller ever repointed the head, leaving
        a real `next_version` row that nothing points at and that no future
        LWT can ever displace -- every future add_version recomputes this
        same next_version and loses the identical race forever, wedging the
        backup_id at 409 permanently (bug found in the 2026-09-06
        second-round adversarial review). Case (2) is repaired here:
        whatever row is actually sitting at `next_version` is promoted to
        head in the earlier caller's stead, via the exact same
        `_finish_promoting_version` sequence a successful add_version call
        itself runs.

        Never raises past a logged error: this is a best-effort repair of
        someone else's abandoned write, not a step the CURRENT caller's own
        rejection depends on. If it fails too, the next add_version attempt
        against this backup_id gets another chance to run it.
        """
        try:
            current_head = self.store.get(wallet, backup_id)
            if current_head is None or current_head.current_version != expected_current_version:
                return  # already resolved (case 1), or the backup vanished under us
            orphaned_version_row = self.store.get_version(wallet, backup_id, next_version)
            if orphaned_version_row is None or not orphaned_version_row.is_active:
                return  # nothing sitting at next_version to repair
            logger.warning(
                "x402 storage: repairing an orphaned add_version write for wallet=%s "
                "backup_id=%s -- version %s exists but the head still pointed at "
                "version %s, completing the interrupted head repoint now",
                wallet,
                backup_id,
                next_version,
                expected_current_version,
            )
            self._finish_promoting_version(
                head=current_head, new_version_row=orphaned_version_row, now=now
            )
        except Exception:
            logger.error(
                "x402 storage: failed to repair an orphaned add_version write for "
                "wallet=%s backup_id=%s next_version=%s -- future add_version calls "
                "for this backup_id will keep losing the same race until this is "
                "fixed (by an operator, or by this same repair succeeding on a "
                "later attempt)",
                wallet,
                backup_id,
                next_version,
                exc_info=True,
            )

    def _finish_promoting_version(
        self, *, head: StoredBackup, new_version_row: StoredBackupVersion, now: datetime
    ) -> None:
        """Repoint `head` to `new_version_row` FIRST, then best-effort retire whatever version it superseded.

        Head-first ordering (bug found in the 2026-09-06 second-round
        adversarial review): before this fix, the OLD version could be
        reaped -- its bytes deleted -- while the head still pointed at it,
        because `_retire_superseded_version` ran before the head was
        repointed. Anything failing in that window left a `status=active`
        head pointing at bytes that were already gone: a live GET on this
        backup would 500 `backup_unreadable`, the exact failure mode this
        module's own docstrings name as the one to never hit. Repointing the
        head durably FIRST means the old version's bytes are never touched
        while the head might still be serving them as current.

        Once the head is durably repointed, the projection resync and
        old-version retirement are best-effort cleanup only: failures there
        are logged and swallowed, never raised, because by this point the
        payer's purchase is already fully delivered -- letting an exception
        escape here would incorrectly run `run_with_refund`'s refund path
        against a request that already succeeded.
        """
        old_version = head.current_version
        self.store.upsert(
            replace(
                head,
                connector=new_version_row.connector,
                connector_params=new_version_row.connector_params,
                size_bytes=new_version_row.size_bytes,
                content_hash=new_version_row.content_hash,
                label=new_version_row.label,
                current_version=new_version_row.version,
                settlement_tx_id=new_version_row.settlement_tx_id,
            )
        )
        try:
            # The new version is now current: its reachability must track
            # the head's real (unchanged) governing expiry, not the fresh
            # schedule insert_new_version_if_absent just gave its own
            # projection row.
            self.store.resync_current_version_projection(
                wallet=head.wallet,
                backup_id=head.backup_id,
                version=new_version_row.version,
                previous_epoch=new_version_row.expires_at_epoch,
                new_epoch=head.expires_at_epoch,
            )
            old_version_row = self.store.get_version(head.wallet, head.backup_id, old_version)
            if old_version_row is not None and old_version_row.is_active:
                self._retire_superseded_version(
                    old_version_row, previous_projection_epoch=head.expires_at_epoch, now=now
                )
        except Exception:
            logger.error(
                "x402 storage: post-promotion cleanup (projection resync / "
                "old-version retirement) failed for wallet=%s backup_id=%s "
                "new_current_version=%s -- the new version is already live and "
                "correctly the head, this is bookkeeping debt only",
                head.wallet,
                head.backup_id,
                new_version_row.version,
                exc_info=True,
            )

    def _retire_superseded_version(
        self,
        old_version_row: StoredBackupVersion,
        *,
        previous_projection_epoch: int,
        now: datetime,
    ) -> None:
        """Hand a just-superseded version back to its own independent lifecycle, reaping it immediately if that schedule is already overdue.

        While `old_version_row` was current, its x402_storage_version_by_expiry
        projection was kept re-keyed to track the HEAD's own expiry (see
        `resync_current_version_projection`), not its own frozen schedule --
        `previous_projection_epoch` is wherever that tracking currently
        sits. Once superseded, only its own independent `expires_at_epoch`
        (frozen since creation, see StoredBackupVersion's own docstring)
        governs it again -- but that frozen day can already be arbitrarily
        far in the past if the head kept renewing while this version stayed
        current well past its own original term. The periodic reaper
        day-partition scan can never rescue an already-past-lookback day
        (finding #3, 2026-09-06: this is exactly the orphan fable
        reproduced), so if this version's own schedule is already past its
        grace window BY NOW, reap it immediately instead of re-keying the
        projection and hoping a future scan finds it -- for a day this far
        gone, it never would.
        """
        if old_version_row.expires_at_epoch <= grace_cutoff_epoch(now):
            try:
                self.delete_version(old_version_row)
            except Exception:
                logger.warning(
                    "x402 storage: immediate reap of a just-superseded version failed for "
                    "wallet=%s backup_id=%s version=%s",
                    old_version_row.wallet,
                    old_version_row.backup_id,
                    old_version_row.version,
                    exc_info=True,
                )
            # delete_version()'s own upsert_version call only drops the
            # projection key at THIS row's own (canonical) epoch -- not
            # necessarily wherever it was actually parked while current.
            if previous_projection_epoch != old_version_row.expires_at_epoch:
                self.store.delete_version_expiry_index(
                    VersionExpiryIndexRow(
                        wallet=old_version_row.wallet,
                        backup_id=old_version_row.backup_id,
                        version=old_version_row.version,
                        expires_at_epoch=previous_projection_epoch,
                    )
                )
            return
        self.store.resync_current_version_projection(
            wallet=old_version_row.wallet,
            backup_id=old_version_row.backup_id,
            version=old_version_row.version,
            previous_epoch=previous_projection_epoch,
            new_epoch=old_version_row.expires_at_epoch,
        )

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

        Filtered after the LIMITed read -- an accepted tradeoff: a page can
        come back short when the front of this wallet's own history is full
        of deleted/expired rows.
        """
        moment = now or datetime.now(tz=UTC)
        cutoff = int(moment.timestamp())
        clamped = max(1, min(limit, settings.x402_storage_max_results))
        rows = self.store.list_recent(wallet, limit=clamped)
        return [row for row in rows if row.is_live(now_epoch=cutoff)]

    def get_version_live(
        self, wallet: str, backup_id: str, version: int, *, now: datetime | None = None
    ) -> StoredBackupVersion | None:
        """One version row, or None if it does not exist, is soft-deleted, or has expired.

        Falls back to synthesizing version 1 from the live head row when the
        versions table has no row at all for this backup_id -- the ONLY way
        that happens is a backup created before migration 115 shipped (see
        create()'s own docstring: every backup created since always writes
        its version 1 row). A backup that HAS real version rows never hits
        this fallback, even for version 1.

        Liveness for whichever version is the backup's CURRENT content
        defers to the head's own `expires_at_epoch`, not this row's own --
        see `_version_is_live`'s own docstring (finding #4, 2026-09-06).
        """
        moment = now or datetime.now(tz=UTC)
        now_epoch = int(moment.timestamp())
        found = self.store.get_version(wallet, backup_id, version)
        if found is not None:
            head = self.store.get(wallet, backup_id)
            return found if _version_is_live(found, head, now_epoch=now_epoch) else None
        if version != 1:
            return None
        head = self.get_live(wallet, backup_id, now=moment)
        if head is None or head.current_version != 1:
            return None
        return _version_from_head(head)

    def list_versions_live(
        self, wallet: str, backup_id: str, *, limit: int, now: datetime | None = None
    ) -> list[StoredBackupVersion]:
        """This backup's active, unexpired versions, newest-version first, clamped to x402_storage_max_results.

        Same legacy fallback as get_version_live: a pre-115 backup with no
        rows at all in the versions table synthesizes its implicit version 1
        from the live head rather than listing empty.

        Same current-version-defers-to-head liveness rule as
        get_version_live -- see `_version_is_live`'s own docstring (finding
        #4, 2026-09-06).
        """
        moment = now or datetime.now(tz=UTC)
        cutoff = int(moment.timestamp())
        clamped = max(1, min(limit, settings.x402_storage_max_results))
        rows = self.store.list_versions(wallet, backup_id, limit=clamped)
        if not rows:
            head = self.get_live(wallet, backup_id, now=moment)
            if head is not None:
                return [_version_from_head(head)]
            return []
        head = self.store.get(wallet, backup_id)
        return [row for row in rows if _version_is_live(row, head, now_epoch=cutoff)]

    def _connector_bytes(self, connector: str, connector_params: dict[str, str]) -> bytes | None:
        """The stored bytes behind one (connector, connector_params) pair, or None if the connector cannot produce them.

        Shared by read_bytes/read_version_bytes -- the only difference
        between reading a head's current content and reading a specific
        historical version is which row's connector/connector_params is
        passed in.
        """
        backend = self.backend_for(connector)
        if backend is None:
            return None
        return backend.get(connector_params)

    def read_bytes(self, backup: StoredBackup) -> bytes | None:
        """The stored bytes for one backup row's current content, or None if the connector cannot produce them."""
        return self._connector_bytes(backup.connector, backup.connector_params)

    def read_version_bytes(self, version_row: StoredBackupVersion) -> bytes | None:
        """The stored bytes for one specific version, or None if the connector cannot produce them."""
        return self._connector_bytes(version_row.connector, version_row.connector_params)

    def _delete_connector_bytes(
        self,
        connector: str,
        connector_params: dict[str, str],
        *,
        wallet: str,
        backup_id: str,
        reason: str = "row already marked deleted",
        level: int = logging.WARNING,
    ) -> None:
        """Best-effort connector delete, logged (never raised) on failure -- shared by delete()/delete_version()/add_version()'s LWT-loser cleanup.

        `reason` names what state the row was actually in when this delete
        was attempted, since that differs by caller: delete()/delete_version()
        have already flipped the row to STATUS_DELETED before calling this
        (the default), but add_version()'s LWT-loser cleanup never had a row
        to begin with -- passing an accurate `reason` keeps the log line
        honest instead of claiming a delete state that never applied
        (mislabeled-log finding, second-round adversarial review,
        2026-09-06). `level` lets a caller escalate past WARNING when the
        failure has genuinely no reconciliation path: a leaked orphan blob
        with nothing left pointing at it will never be found again except by
        an operator reading this exact log line, so it must be loud enough
        to actually surface via log aggregation.
        """
        backend = self.backend_for(connector)
        if backend is None:
            return
        try:
            backend.delete(connector_params)
        except Exception:
            logger.log(
                level,
                "x402 storage: connector delete failed for wallet=%s backup_id=%s (%s)",
                wallet,
                backup_id,
                reason,
                exc_info=True,
            )

    def delete(self, backup: StoredBackup) -> bool:
        """Mark the row deleted, then delete the connector bytes. Returns True once the row is marked.

        Row-then-bytes ordering: a crash between the two steps leaves a
        `status="deleted"` row (GET/list 404) whose bytes may still sit on
        disk as an orphan counted by usage_bytes() -- a clean failure mode
        -- rather than a `status="active"` row whose bytes silently vanished
        (GET 500 backup_unreadable, still listed). The reaper also walks
        due rows through this same method.

        Also cascades to every version row this backup_id has (including
        the current one, whose bytes are the same blob the head just
        deleted -- deleting an already-gone path is a documented no-op on
        the local backend): each is marked deleted and has its own
        connector bytes removed, so an outright delete reclaims disk for
        every historical version, not just the head's current content.
        Bounded to one x402_storage_max_results page of versions, same cap
        as every other list read in this module -- a backup with more
        versions than that leaves its oldest version blobs as orphans until
        the version-reaper's own independent per-version expiry sweep
        reclaims them on their normal schedule (flagged, not a correctness
        bug: nothing can read a deleted backup's versions again either way).

        The CURRENT version's own x402_storage_version_by_expiry row may be
        re-keyed to the head's own (possibly far-future) expiry rather than
        its own frozen schedule (see `resync_current_version_projection`'s
        own docstring) -- `upsert_version`'s status-flip-to-deleted branch
        only ever drops the projection key at the row's OWN canonical
        `expires_at_epoch`, so that re-keyed entry is dropped explicitly
        here instead, using `backup`'s own (pre-delete) expiry, which is
        where the invariant this module maintains guarantees it currently
        sits.
        """
        self.store.upsert(replace(backup, status=STATUS_DELETED))
        self._delete_connector_bytes(
            backup.connector,
            backup.connector_params,
            wallet=backup.wallet,
            backup_id=backup.backup_id,
        )
        for version_row in self.store.list_versions(
            backup.wallet, backup.backup_id, limit=settings.x402_storage_max_results
        ):
            if version_row.status == STATUS_DELETED:
                continue
            if (
                version_row.version == backup.current_version
                and backup.expires_at_epoch != version_row.expires_at_epoch
            ):
                self.store.delete_version_expiry_index(
                    VersionExpiryIndexRow(
                        wallet=backup.wallet,
                        backup_id=backup.backup_id,
                        version=version_row.version,
                        expires_at_epoch=backup.expires_at_epoch,
                    )
                )
            self.store.upsert_version(replace(version_row, status=STATUS_DELETED))
            self._delete_connector_bytes(
                version_row.connector,
                version_row.connector_params,
                wallet=backup.wallet,
                backup_id=backup.backup_id,
            )
        return True

    def delete_version(self, version_row: StoredBackupVersion) -> bool:
        """Mark one historical (non-current) version row deleted, then delete its own connector bytes.

        Same row-then-bytes ordering and reasoning as delete(). Called by
        reaper._reap_one_version and by this module's own
        `_retire_superseded_version` (from `_finish_promoting_version`'s
        promotion and orphaned-head-repair paths in add_version) -- every
        caller has already confirmed this version is NOT the backup's
        current content, and that the head has already been durably
        repointed away from it, before calling this: deleting a version's
        bytes while the head might still be pointing at it would corrupt
        the head's own GET (second-round adversarial review, 2026-09-06).
        """
        self.store.upsert_version(replace(version_row, status=STATUS_DELETED))
        self._delete_connector_bytes(
            version_row.connector,
            version_row.connector_params,
            wallet=version_row.wallet,
            backup_id=version_row.backup_id,
        )
        return True

    def renew(
        self, backup: StoredBackup, *, settlement_tx_id: str, now: datetime | None = None
    ) -> StoredBackup:
        """Extend a backup's expiry by one more configured term, capped at x402_storage_max_remaining_days.

        Base is max(now, current expires_at) so an already-expired backup
        (still inside the reaper grace window) starts a fresh term from now,
        and a still-live backup is refreshed rather than shortened. The cap
        is applied after that add: remaining storage can never exceed
        three months from this call's `now`.

        Also re-keys the CURRENT version's own x402_storage_version_by_expiry
        projection row to this new expiry (finding #3, 2026-09-06): the
        head's own expiry projection (x402_storage_by_expiry) is already
        kept in sync by `store.upsert` itself, but nothing previously
        touched the one-level-down version projection when only the head
        moved, so a version that stayed current across enough renewals
        could see its own projection day permanently fall outside
        x402_storage_reaper_lookback_days -- unreachable by the reaper's
        periodic day-partition scan forever after, even while its bytes were
        still the backup's live, served content. See
        `resync_current_version_projection`'s own docstring and
        reaper.py's `_reap_one_version`.
        """
        moment = now or datetime.now(tz=UTC)
        renewed = replace(
            backup,
            expires_at_epoch=compute_expiry_epoch(
                moment, current_expires_at=backup.expires_at_epoch
            ),
            settlement_tx_id=settlement_tx_id,
        )
        self.store.upsert(renewed)
        self.store.resync_current_version_projection(
            wallet=backup.wallet,
            backup_id=backup.backup_id,
            version=backup.current_version,
            previous_epoch=backup.expires_at_epoch,
            new_epoch=renewed.expires_at_epoch,
        )
        return renewed
