USE algorand_platform;

-- Expiry projection for x402 agent backup storage (migration 111's
-- x402_storage_backups is partitioned by wallet, so a reaper cannot find
-- globally-due rows without ALLOW FILTERING). One row per currently-active
-- backup, partitioned by the UTC calendar date of expires_at, clustered
-- by expires_at itself. The reaper (app/modules/x402_storage/services/reaper.py)
-- reads a bounded lookback of those day partitions, LIMITed, then point-reads
-- the canonical row before deleting -- a stale projection row (left behind
-- by a renew that moved expires_at) is dropped without touching live bytes.
--
-- Kept in sync by CassandraBackupStore.upsert: canonical row first, then
-- this projection (store before mark). A deleted or expired-past-grace
-- backup has its projection row removed as part of BackupService.delete.
CREATE TABLE IF NOT EXISTS x402_storage_by_expiry (
  expiry_day date,
  expires_at timestamp,
  wallet text,
  backup_id timeuuid,
  PRIMARY KEY ((expiry_day), expires_at, wallet, backup_id)
) WITH CLUSTERING ORDER BY (expires_at ASC);
