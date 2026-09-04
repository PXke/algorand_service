USE algorand_platform;

-- x402 agent backup storage (Algorand Global x402 Challenge, Composite
-- entry). Roadmap item 12 in CLAUDE.md section 9.1 ("pay-per-MB storage"),
-- owner design decision made 2026-09-03: an agent pays to upload an opaque
-- blob (its own "backup") and later retrieves or deletes it, authenticated
-- purely by proving control of its Algorand wallet at request time -- no
-- session dependency, since a disaster-recovery restore must not depend on
-- session state that might be exactly what was lost. See
-- app/modules/x402_storage/.
--
-- One table, partitioned by wallet -- every access pattern this product
-- needs (list a wallet's own backups, point-read one by id) is
-- single-partition and always LIMITed, so unlike the directory/board's
-- global feeds, no by-recency projection table is needed here: this
-- product's own routes never list across wallets.
--
-- connector / connector_params record which StorageBackend wrote the bytes
-- and how to find them again (app/modules/x402_storage/backends/base.py's
-- Protocol) -- "local" is the only connector implemented today
-- (backends/local.py) -- a second cloud connector (Wasabi) is a documented
-- future addition behind the same Protocol, not built now. connector_params
-- is an internal implementation detail, never served on the wire.
--
-- content_hash is a sha256 hex digest of the actual uploaded bytes, checked
-- again on every restore so silent corruption is caught rather than served.
--
-- status is a soft-delete flag ("active" | "deleted"): DELETE removes the
-- connector's bytes first, then marks the row deleted, so a crash mid-delete
-- leaves a row pointing at already-gone bytes (a safe 404) rather than
-- silently orphaned bytes nobody's metadata references (a real disk leak).
--
-- settlement_tx_id traces the row to the payment that bought its current
-- term, replaced by a renewal's own txid -- same convention every other
-- paid x402 product's stored row keeps (x402_listings 090, x402_board_entries
-- 091).
CREATE TABLE IF NOT EXISTS x402_storage_backups (
  wallet text,
  backup_id timeuuid,
  connector text,
  connector_params map<text, text>,
  size_bytes bigint,
  content_hash text,
  label text,
  created_at timestamp,
  expires_at timestamp,
  status text,
  settlement_tx_id text,
  PRIMARY KEY (wallet, backup_id)
) WITH CLUSTERING ORDER BY (backup_id DESC);
