USE algorand_platform;

-- x402 endpoint directory (Algorand Global x402 Challenge, Composite entry).
-- Anyone pays a flat x402 fee to list a paid endpoint for a fixed term, and
-- the listing is then served free from search. Canonical record, keyed by a
-- hash of the normalized endpoint URL: every write path is "create or replace
-- the listing for THIS url", and every point read is "what is listed for this
-- url", so the url hash is the natural partition key. Re-listing the same url
-- overwrites (latest term wins, not history) -- the settlement ledger below is
-- what keeps the per-payment history.
--
-- schema_json, not schema: SCHEMA is a reserved CQL keyword and CREATE TABLE
-- rejects it as a bare column name (same trap share_links hit with `token`,
-- see ShareLinkStmts). The field is still called "schema" in the JSON API.
CREATE TABLE IF NOT EXISTS x402_listings (
  url_hash text PRIMARY KEY,
  url text,
  price text,
  assets set<text>,
  description text,
  schema_json text,
  tags set<text>,
  term_end timestamp,
  settlement_tx_id text,
  created_at timestamp
);

-- Recency projection for GET /x402/search, which is a newest-first feed over
-- every listing. x402_listings alone cannot answer that: created_at is not
-- part of its key, so ordering by it needs a cluster-wide ALLOW FILTERING
-- scan, which CLAUDE.md forbids on non-key columns -- "add a lookup table
-- instead". Same denormalization precedent as suggestions_by_txid (087) and
-- suggestions_by_id (089).
--
-- directory is a constant partition key ('default'), so the whole feed lives
-- in one partition and reads newest-first with no fan-out. That bounds the
-- table to a single partition on purpose: this is a curated endpoint
-- directory, expected in the hundreds-to-thousands of rows for the
-- competition, every read is LIMITed, and rows expire from relevance at
-- term_end. If the directory ever outgrows one partition, shard the key by
-- term month -- do NOT let this become an unbounded hot partition silently.
CREATE TABLE IF NOT EXISTS x402_listings_by_recency (
  directory text,
  created_at timestamp,
  url_hash text,
  url text,
  price text,
  assets set<text>,
  description text,
  schema_json text,
  tags set<text>,
  term_end timestamp,
  settlement_tx_id text,
  PRIMARY KEY ((directory), created_at, url_hash)
) WITH CLUSTERING ORDER BY (created_at DESC, url_hash ASC);

-- The bookkeeping ledger required by CLAUDE.md section 9: every settled x402
-- payment, with the asset/amount actually paid, the settlement txid, who paid,
-- which resource they paid for, when, and its EUR value at settlement time.
-- Append-only, never read on a request path -- this exists for accounting and
-- for auditing our own leaderboard volume against the facilitator's.
--
-- day ('YYYY-MM-DD', UTC) is the partition key so partitions stay bounded and
-- an accounting period is one contiguous read, clustered newest-first within
-- the day. network is recorded per row because TestNet and Mainnet settlements
-- share this table across the mainnet flip and must never be summed together.
-- eur_value is written as 0.0 until an FX lookup exists -- a placeholder that
-- is deliberately visible in the data rather than a missing column.
CREATE TABLE IF NOT EXISTS x402_settlements (
  day text,
  settled_at timestamp,
  tx_id text,
  asset_id text,
  amount_atomic text,
  payer text,
  resource text,
  network text,
  eur_value double,
  PRIMARY KEY ((day), settled_at, tx_id)
) WITH CLUSTERING ORDER BY (settled_at DESC, tx_id ASC);
