USE algorand_platform;

-- Product depth for three existing x402 modules (CLAUDE.md section 9.1):
-- a click-through counter for the visibility board (091) and a build-claim
-- log for the feature-request board (092). The grading module's two new
-- routes in the same change (free per-URL summary, paid tag leaderboard) need
-- no schema: both read the tables from 093 and the directory's tag projection
-- from 096 through its service.

-- Click-throughs on a board placement, bumped only by the free
-- GET /api/v1/x402/board/:entry_id/go redirect and NEVER on the feed read,
-- which stays a pure read. Its own table because a counter column cannot
-- share a table with non-counter columns (same reason as
-- x402_feature_vote_totals in 092). Same trade-off as that table: a counter
-- update is atomic at the replica but not idempotent under client retry, so
-- the store issues exactly one and never retries -- a lost click is one
-- increment, never a double count. Read back in the feed via concurrent point
-- reads bounded by the page size, never a multi-partition IN.
CREATE TABLE IF NOT EXISTS x402_board_clicks (
  entry_id text PRIMARY KEY,
  clicks counter
);

-- Paid "I am building this" claims against a feature request, one row per
-- settled claim. Append-only and deliberately NOT exclusive: a claim is a
-- costly public statement of intent, and an exclusive lock would let one
-- payment squat a request nobody else may then declare for. Multiple claims
-- per request, and per wallet, are allowed.
--
-- Partitioned by request_id so a request's claims are one contiguous read.
-- Clustered (claimed_at DESC, claimer ASC) so the FIRST row of a LIMITed
-- read is the latest claimer and the row count is the claim count -- which
-- is how both read surfaces get claims_count and latest_claimer with one
-- bounded partition read per request (LIMIT CLAIMS_SCAN_LIMIT, 100, so the
-- count saturates there). Clustering on claimer as well as claimed_at means
-- two wallets claiming in the same millisecond get distinct rows.
-- No counter table: claims are rare next to votes (a builder declares once,
-- not per unit of demand), so a bounded scan is cheaper than a second table
-- and gives the latest claimer for free.
--
-- claimer is the wallet from the settled payment, never a body field.
-- No settlement table of its own: claim payments land in x402_settlements
-- (090) via the shared modules/x402/settlement.py ledger.
CREATE TABLE IF NOT EXISTS x402_feature_claims (
  request_id text,
  claimed_at timestamp,
  claimer text,
  settlement_tx_id text,
  PRIMARY KEY ((request_id), claimed_at, claimer)
) WITH CLUSTERING ORDER BY (claimed_at DESC, claimer ASC);
