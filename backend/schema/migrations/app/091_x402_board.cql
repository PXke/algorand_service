USE algorand_platform;

-- x402 paid visibility board (Algorand Global x402 Challenge, Composite
-- entry). An agent pays a flat x402 fee to place one link plus a short pitch
-- for a fixed term, and the board is then readable free by anyone. Roadmap
-- item 2 in CLAUDE.md section 9.1 -- the "Million Dollar Homepage for bots".
--
-- Deliberately thinner than x402_listings (migration 090): no price, no
-- assets, no schema_json. The directory catalogues endpoints an agent can
-- CALL, so it must describe the call. The board only asserts that something
-- exists and links to it, so it stores a name, a link, a pitch, and who paid.
--
-- entry_id is a hash of (payer, normalized link), NOT of the link alone.
-- Keying on the link alone would let anyone pay the deliberately-small
-- placement fee to overwrite somebody else's live tile with their own pitch
-- text -- a griefing hole a paid advertising surface cannot have. Keying on
-- the pair means a payer can only ever replace their own placement, and two
-- payers advertising the same link each get the tile they each paid for.
-- Re-placing overwrites (latest term wins, not history) -- x402_settlements
-- from migration 090 is what keeps the per-payment history.
CREATE TABLE IF NOT EXISTS x402_board_entries (
  entry_id text PRIMARY KEY,
  link text,
  name text,
  pitch text,
  payer text,
  term_end timestamp,
  settlement_tx_id text,
  created_at timestamp
);

-- Recency projection for GET /api/v1/x402/board, which is a newest-first feed
-- over every placement. x402_board_entries alone cannot answer that:
-- created_at is not part of its key, so ordering by it needs a cluster-wide
-- ALLOW FILTERING scan, which CLAUDE.md forbids on non-key columns -- "add a
-- lookup table instead". Same denormalization precedent as
-- x402_listings_by_recency (090), suggestions_by_txid (087) and
-- suggestions_by_id (089).
--
-- board is a constant partition key ('default'), so the whole feed lives in
-- one partition and reads newest-first with no fan-out. That bounds the table
-- to a single partition on purpose: this is one curated board, expected in the
-- hundreds-to-thousands of rows for the competition, every read is LIMITed,
-- and rows fall out of relevance at term_end. If the board ever outgrows one
-- partition, shard the key by term month -- do NOT let this become an
-- unbounded hot partition silently.
--
-- Expired placements are filtered by BoardService after the LIMITed read, not
-- in CQL: term_end is not part of the key here either. A TTL on this
-- projection, or a sweep, would be the cheaper long-run answer and is not
-- built yet.
CREATE TABLE IF NOT EXISTS x402_board_by_recency (
  board text,
  created_at timestamp,
  entry_id text,
  link text,
  name text,
  pitch text,
  payer text,
  term_end timestamp,
  settlement_tx_id text,
  PRIMARY KEY ((board), created_at, entry_id)
) WITH CLUSTERING ORDER BY (created_at DESC, entry_id ASC);
