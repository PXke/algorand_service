USE algorand_platform;

-- Versioned agent backup storage (owner ask 2026-09-06, sourced from real
-- Moltbook/Clawstr agent feedback: "can I store multiple versions of a
-- backup and retrieve a specific one?"). See
-- app/modules/x402_storage/services/backup_service.py's `add_version` and
-- models/domain.py's `StoredBackupVersion` / `VersionExpiryIndexRow`.
--
-- current_version on the existing canonical row (migration 111) says which
-- version this row's own connector/size_bytes/content_hash/label currently
-- mirror -- nullable, additive ALTER, safe in place. A pre-115 row reads
-- back current_version=null, mapped to 1 in both stores' row mappers (every
-- row before this migration was, without exception, a single-version
-- backup). The canonical row is ALWAYS what an unversioned GET/list caller
-- sees -- this ALTER changes nothing about its own expiry/renew lifecycle.
ALTER TABLE x402_storage_backups ADD current_version int;

-- Full version history, one row per (wallet, backup_id, version). Written
-- once at creation (version 1 alongside every new canonical row, or version
-- N+1 by add_version), never revised except the one later status flip to
-- 'deleted' the whole-backup delete() and the reaper's own delete_version()
-- make (same full-re-INSERT rule as x402_storage_backups). A pre-115
-- backup has NO rows here until/unless a version is added to it --
-- BackupService synthesizes an implicit version 1 from the canonical row
-- for such a backup rather than requiring a backfill (CLAUDE.md-adjacent
-- "existing rows keep working with zero migration pain").
CREATE TABLE IF NOT EXISTS x402_storage_backup_versions (
  wallet text,
  backup_id timeuuid,
  version int,
  connector text,
  connector_params map<text, text>,
  size_bytes bigint,
  content_hash text,
  label text,
  created_at timestamp,
  expires_at timestamp,
  status text,
  settlement_tx_id text,
  PRIMARY KEY ((wallet, backup_id), version)
) WITH CLUSTERING ORDER BY (version DESC);

-- Expiry projection for the versions table above, one level down from
-- migration 112's x402_storage_by_expiry -- same reason: the canonical
-- versions table is partitioned by (wallet, backup_id), so the reaper
-- cannot find globally-due version rows without ALLOW FILTERING. One row
-- per currently-active version, partitioned by the UTC calendar date of
-- ITS OWN expires_at. The version currently mirrored by its backup's head
-- (x402_storage_backups.current_version) is deliberately left alone even
-- once this projection says it is due -- see
-- app/modules/x402_storage/services/reaper.py's `_reap_one_version`, which
-- checks the head before ever deleting a version's bytes.
CREATE TABLE IF NOT EXISTS x402_storage_version_by_expiry (
  expiry_day date,
  expires_at timestamp,
  wallet text,
  backup_id timeuuid,
  version int,
  PRIMARY KEY ((expiry_day), expires_at, wallet, backup_id, version)
) WITH CLUSTERING ORDER BY (expires_at ASC);
