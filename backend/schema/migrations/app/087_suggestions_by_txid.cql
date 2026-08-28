USE algorand_platform;

-- Lookup table for the submission_txid duplicate-guard on suggestions_by_status
-- (CassandraSuggestionStore.has_submission_txid, called on every suggestion
-- submission). The old check ran `WHERE submission_txid = ? ALLOW FILTERING`
-- against suggestions_by_status -- a cluster-wide filtering scan on every
-- single submission (schema audit 2026-08-28) -- CLAUDE.md forbids new ALLOW
-- FILTERING on non-key columns and calls for a lookup table instead.
-- submission_txid is the partition key here, so HAS_TXID becomes a direct
-- point lookup, one round trip, no filtering. Populated by a dual-write
-- alongside every suggestions_by_status INSERT (CassandraSuggestionStore.
-- insert), the same reverse-index-table pattern already used elsewhere in
-- this schema (service_sources, article_match_keys).
-- NOTE: pre-existing suggestions_by_status rows written before this
-- migration have no matching row here until a one-off backfill runs --
-- out of scope for this change (no production data migration authorized),
-- flagged for whoever deploys this.
CREATE TABLE IF NOT EXISTS suggestions_by_txid (
  submission_txid text PRIMARY KEY,
  suggestion_id uuid,
  status text,
  created_at timestamp
);

-- suggestions_submission_txid_idx (migration 001, legacy secondary index) had
-- exactly one caller -- the ALLOW FILTERING scan above -- now removed, so the
-- index no longer earns its per-write overhead.
DROP INDEX IF EXISTS suggestions_submission_txid_idx;
