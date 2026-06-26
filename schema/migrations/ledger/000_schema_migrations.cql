-- Migration ledger: records which versioned CQL files have been applied per stream.
-- Applied by: deploy/scripts/cql-migrate.sh apply (always first).

USE algorand_platform;

CREATE TABLE IF NOT EXISTS schema_migrations (
  stream text,
  version text,
  applied_at timestamp,
  checksum text,
  tier text,
  description text,
  applied_by text,
  PRIMARY KEY ((stream), version)
);
