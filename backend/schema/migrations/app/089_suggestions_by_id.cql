USE algorand_platform;

-- Lookup table for id-based suggestion reads (CassandraSuggestionStore.get,
-- the single-suggestion detail read). The old query ran `WHERE status = ?
-- AND suggestion_id = ? ALLOW FILTERING` against suggestions_by_status:
-- status is the partition key, but the clustering columns are (created_at,
-- suggestion_id) in that order, so filtering on suggestion_id without also
-- constraining created_at skips a clustering column and forces ALLOW
-- FILTERING even though the read is scoped to a single partition (schema
-- audit 2026-08-28, same audit that produced suggestions_by_txid in
-- migration 087) -- CLAUDE.md forbids new ALLOW FILTERING on non-key
-- columns and calls for a lookup table instead.
-- suggestion_id is the partition key here, and the full row is denormalized
-- onto it (same call as suggestions_by_txid storing more than a bare
-- key->key pointer) so SuggestionStmts.GET becomes a single direct point
-- lookup with no second read against suggestions_by_status. Populated by a
-- dual-write alongside every suggestions_by_status INSERT
-- (CassandraSuggestionStore.insert), the same reverse-index-table pattern
-- suggestions_by_txid already established.
-- NOTE: pre-existing suggestions_by_status rows written before this
-- migration have no matching row here until a one-off backfill runs --
-- out of scope for this change (no production data migration authorized),
-- flagged for whoever deploys this.
CREATE TABLE IF NOT EXISTS suggestions_by_id (
  suggestion_id uuid PRIMARY KEY,
  status text,
  created_at timestamp,
  wallet_address text,
  title text,
  body text,
  submission_txid text
);
