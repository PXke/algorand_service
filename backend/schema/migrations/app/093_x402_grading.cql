USE algorand_platform;

-- x402 endpoint grading (Algorand Global x402 Challenge, Composite entry).
-- Roadmap item 6 in CLAUDE.md section 9.1: an agent pays a flat x402 fee to
-- grade ANY endpoint 1-5 stars with an optional one-line opinion, and anyone
-- pays a higher fee to read that endpoint's credibility-weighted aggregate
-- score. POST /api/v1/x402/grades and GET /api/v1/x402/grades/score are paid,
-- GET /api/v1/x402/grades is a free index of which endpoints have any grade.
--
-- NOTHING HERE IS CUSTODIAL. The roadmap's "small stake" is the one-time
-- payment itself, settled straight to the receive-only payTo like every other
-- payment in this marketplace. There is no escrow column, no held balance, no
-- refund state and no forfeiture flag in either table, by design -- CLAUDE.md
-- section 9 bars this project from holding user funds, and a real escrow
-- primitive belongs to roadmap item 5's smart contract, which is not started.
-- The credibility weight is likewise not stored anywhere: it is a read-time
-- sum over x402_settlements, never a balance this product controls.
--
-- url_hash is this module's OWN hex SHA-256 of its own normalized URL, not an
-- identifier borrowed from x402_directory. A graded endpoint does not have to
-- be listed with us, or known to us at all -- the grader names any http(s) URL
-- and the flat payment is the whole cost of entry -- so there is nothing to
-- resolve against the directory and this module has no dependency on it.
-- url is denormalized into both tables because the hash is one-way: without it
-- neither the free index nor the paid aggregate could tell a caller WHICH
-- endpoint a row is about.
--
-- PRIMARY KEY ((url_hash), grader) IS the product rule, not just a layout:
-- one grader holds at most one grade per endpoint, so re-grading addresses and
-- replaces the same row instead of stacking a second one. That differs on
-- purpose from the feature board's votes (migration 092), where stacking IS
-- the demand signal -- here the read side is a quality SCORE, and an average is
-- only meaningful while one wallet contributes one data point. Unlimited
-- stacking would let anyone buy an endpoint's rating for the price of a few
-- grades, corrupting the signal rather than expressing it. Credibility
-- weighting does not change that: the weight decides how much a wallet's ONE
-- data point counts, and stacking on top of it would sell influence twice.
--
-- The aggregate (weighted mean, plain mean, count, 1-5 distribution) is
-- computed in Python over a single LIMITed read of one endpoint's partition,
-- not stored. Two reasons, and the second is the decisive one under this
-- design:
--   1. A counter or running-average column cannot be maintained idempotently
--      under the overwrite rule -- a re-grade would have to subtract the
--      previous score before adding the new one, so one retried write would
--      corrupt the average permanently, while re-reading rows and averaging is
--      idempotent by construction. At one row per grader per endpoint,
--      realistically tens, a counter buys nothing anyway.
--   2. The weights are not a property of these rows at all. A grader's weight
--      moves whenever that wallet pays for ANYTHING elsewhere in this
--      marketplace, with no write to this module. Any stored aggregate would
--      be stale the moment any grader spent again, and keeping it fresh would
--      mean recomputing it from every other product's settlements. The
--      weighted number is only correct if it is computed at read time.
-- Revisit only if one endpoint's grader count is ever plausibly in the
-- thousands.
CREATE TABLE IF NOT EXISTS x402_grades (
  url_hash text,
  grader text,
  url text,
  score int,
  comment text,
  settlement_tx_id text,
  created_at timestamp,
  PRIMARY KEY ((url_hash), grader)
) WITH CLUSTERING ORDER BY (grader ASC);

-- Index for the FREE GET /api/v1/x402/grades, which answers "which endpoints
-- have been graded at all" with no scores in it -- free is existence, paid is
-- signal, the same split the feature board draws. x402_grades alone cannot
-- answer it: enumerating distinct url_hash values means a cluster-wide scan,
-- and CLAUDE.md forbids that in favour of a lookup table. Same denormalization
-- precedent as x402_listings_by_recency (090), x402_board_by_recency (091),
-- suggestions_by_txid (087) and suggestions_by_id (089).
--
-- It is also read as a single-row existence check before the PAID score
-- lookup takes payment, so nobody is ever charged for an empty aggregate. That
-- read gives away exactly what this table already serves for free, so it costs
-- nothing to answer.
--
-- registry is a constant partition key ('default'), so the whole index lives
-- in one partition and reads with no fan-out. That bounds the table to a
-- single partition on purpose: it holds one row per GRADED endpoint, and every
-- read is LIMITed. If it ever outgrows one partition, shard the key by month
-- -- do NOT let this become an unbounded hot partition silently.
--
-- Clustered by url_hash rather than by last_graded_at: this is an existence
-- index, so it needs deterministic enumeration, not recency ordering, and
-- keying on a mutable timestamp would force the delete-then-insert dance the
-- recency projections in 090/091 need. url_hash never changes, so one INSERT
-- addresses and replaces the row.
CREATE TABLE IF NOT EXISTS x402_graded_endpoints (
  registry text,
  url_hash text,
  url text,
  last_graded_at timestamp,
  PRIMARY KEY ((registry), url_hash)
) WITH CLUSTERING ORDER BY (url_hash ASC);

-- No settlement table of its own: grading payments land in x402_settlements
-- from migration 090 via the shared modules/x402/settlement.py ledger. That
-- same ledger is also READ here, to total how much each grader has paid this
-- marketplace -- the weight their grade carries in the published average. See
-- modules/x402_grading/services/credibility.py, including why only the current
-- network's rows are summed and why a by-payer projection of x402_settlements
-- is the right long-run shape for that read.
