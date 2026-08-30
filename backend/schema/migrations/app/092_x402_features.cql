USE algorand_platform;

-- x402 feature-request board (Algorand Global x402 Challenge, Composite
-- entry). Roadmap item 4 in CLAUDE.md section 9.1: agents pay to request an
-- endpoint and to vote demand onto one, builders pay to read the ranked
-- demand, and anyone browses the bare list of requests for free.
--
-- Four tables rather than the board's two, because this product has a second
-- write path (voting) that the directory and the board do not: a canonical
-- request table, its newest-first projection for the free browse feed, an
-- atomic per-request vote counter, and an append-only vote audit log.
--
-- request_id is a hash of the SETTLING PAYMENT'S TXID, not of the submitter
-- and title the way x402_board_entries keys on (payer, link). A board
-- placement is a rented slot the same payer renews in place. A feature
-- request is an event. The same wallet asking for the same thing twice has
-- paid twice and stated its demand twice, and folding those onto one row would silently
-- delete a paid request. Keying on the txid also means any row in
-- x402_settlements (migration 090) traces forward to the request it bought.
CREATE TABLE IF NOT EXISTS x402_feature_requests (
  request_id text PRIMARY KEY,
  title text,
  description text,
  submitter text,
  settlement_tx_id text,
  created_at timestamp
);

-- Recency projection for the FREE GET /api/v1/x402/features, a newest-first
-- feed over every request. x402_feature_requests alone cannot answer that:
-- created_at is not part of its key, so ordering by it needs a cluster-wide
-- ALLOW FILTERING scan, which CLAUDE.md forbids on non-key columns -- "add a
-- lookup table instead". Same denormalization precedent as
-- x402_board_by_recency (091), x402_listings_by_recency (090) and
-- suggestions_by_id (089).
--
-- board is a constant partition key ('default'), so the whole feed lives in
-- one partition and reads newest-first with no fan-out. That bounds the table
-- to a single partition on purpose: this is one curated board, expected in the
-- hundreds for the competition, and every read is LIMITed. If it ever outgrows
-- one partition, shard the key by filing month -- do NOT let this become an
-- unbounded hot partition silently.
--
-- Unlike the board's projection there is no expiry and no delete-then-reinsert
-- on renewal: a feature request is created once, never re-stamped, and never
-- expires. A board tile is rented advertising that must lapse, whereas a
-- feature request's whole value is the demand it accumulates over time, so
-- expiring one would destroy signal its voters paid to build.
CREATE TABLE IF NOT EXISTS x402_feature_requests_by_recency (
  board text,
  created_at timestamp,
  request_id text,
  title text,
  description text,
  submitter text,
  settlement_tx_id text,
  PRIMARY KEY ((board), created_at, request_id)
) WITH CLUSTERING ORDER BY (created_at DESC, request_id ASC);

-- The demand total, one counter row per request.
--
-- Its own table because Cassandra forbids mixing counter and non-counter
-- columns outside the primary key, so vote_total cannot be a column on
-- x402_feature_requests.
--
-- A counter, not a read-modify-write and not an LWT. Two votes settling at the
-- same instant are two separate payments, and a demand board that silently
-- merged them into one increment would under-report paid signal -- the exact
-- thing this product sells. A counter update is a true atomic add-one at the
-- replica, so neither vote can be lost. A read-modify-write would lose one,
-- and an LWT compare-and-set would need Paxos on every vote to achieve the
-- same thing more slowly. The trade-off accepted in exchange: counter updates
-- are not idempotent under a client-side retry, so the store issues exactly
-- one and never retries it -- a failed vote surfaces as an error rather than
-- risking a double count.
--
-- There is deliberately NO by-demand ranking projection. A rank table
-- clustered on vote_total would have to be delete-then-reinserted on every
-- vote, and it clusters on precisely the value the counter makes atomic --
-- two concurrent votes would interleave their read-old/delete-old/insert-new
-- sequences and leave stale or duplicate rank rows. The paid demand read
-- instead scans this board's single bounded partition (LIMITed by
-- x402_features_demand_scan_limit), reads these counters concurrently, and
-- sorts in memory. That is exact while the board fits in the scan window. The
-- fix when it does not is a rank projection rebuilt by a periodic sweep, which
-- can read the counters at rest, never a live-updated one.
CREATE TABLE IF NOT EXISTS x402_feature_vote_totals (
  request_id text PRIMARY KEY,
  vote_total counter
);

-- Append-only audit log, one row per settled vote: who voted, when, and which
-- payment paid for it.
--
-- Has no public read surface and is never read on a request path. It exists
-- for abuse forensics -- "did one wallet manufacture this request's entire
-- demand?" -- which the counter alone cannot answer, and CLAUDE.md section 9
-- requires per-settlement bookkeeping anyway (the money side of each vote is
-- in x402_settlements from migration 090, and this is the product side).
--
-- Partitioned by request_id so one request's whole voting history is one
-- contiguous read when someone does investigate. Clustered on
-- (voted_at DESC, settlement_tx_id) so the newest votes come first and two
-- votes landing in the same millisecond still get distinct rows -- clustering
-- on voted_at alone would make the second silently overwrite the first, losing
-- exactly the paid vote this table exists to record.
--
-- A wallet may appear here many times. Voting is deliberately not capped at
-- one per wallet: this is a costly-signal board where the demand number is the
-- money staked, and a per-wallet cap would cost a sybil nothing to route
-- around (wallets are free, only the payment is scarce) while costing an
-- honest agent the ability to express intensity. See
-- FeatureService.vote for the full reasoning.
CREATE TABLE IF NOT EXISTS x402_feature_votes (
  request_id text,
  voted_at timestamp,
  settlement_tx_id text,
  voter text,
  PRIMARY KEY ((request_id), voted_at, settlement_tx_id)
) WITH CLUSTERING ORDER BY (voted_at DESC, settlement_tx_id ASC);
