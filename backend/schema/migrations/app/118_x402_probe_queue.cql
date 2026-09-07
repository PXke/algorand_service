USE algorand_platform;

-- x402 probe fairness queue (same-night adversarial review, finding #3,
-- 2026-09-06): the probe sweep's old read (x402_listings_by_recency via
-- algorand_shared.x402_statements.X402ProbeStmts.LIST_LIVE_LISTINGS, 097)
-- was LIMITed and newest-created-first, and that feed's expired rows are
-- never pruned (see listing_service.py's search() docstring). Once the
-- directory held more than X402_PROBE_MAX_LISTINGS rows total, everything
-- past the newest N permanently fell outside the sweep's read window and
-- silently stopped having its term_end extended -- a perfectly healthy,
-- still-paying listing would then expire on schedule and become reclaimable
-- by a new payer for the listing fee, undermining the "stays live forever
-- while healthy" pitch at exactly the scale that pitch needs to hold up at.
--
-- This table is the sweep's new read source, ordered by LEAST-RECENTLY-
-- PROBED instead of most-recently-created, so the sweep round-robins across
-- every live listing over successive runs instead of permanently starving
-- whichever ones are oldest. Same denormalized-row shape as its siblings
-- (090/096) so the sweep never needs a second canonical read per listing.
--
-- Seeded by backend at listing create()/relist() time
-- (CassandraListingStore._write_projections, PAID listings only -- an
-- auto-discovered stub is never probed, same rule the old read already
-- followed) at last_probed_at = the epoch sentinel
-- (algorand_shared.x402_statements.PROBE_QUEUE_NEVER_PROBED, 1970-01-01),
-- which sorts to the front of the ASC clustering order so a fresh or
-- freshly-relisted listing gets its first health check promptly. This
-- insert is idempotent (same partition/clustering key every time a given
-- url_hash is seeded pre-probe), so a listing relisted more than once before
-- ever being probed does not accumulate duplicate rows here.
--
-- Repositioned by workers (x402_probe/service.py's CassandraProbeRepository
-- .mark_probed) after every actually-attempted probe -- healthy or not, so
-- an unhealthy listing cannot camp at the front of the queue forever and
-- starve everyone else -- by deleting the row at the position it was just
-- read at and inserting a fresh one at last_probed_at = the probe's own
-- timestamp. A listing found expired on read is deleted outright instead of
-- carried forward, which prunes a delisted or lapsed listing's row out of
-- this table within one pass instead of letting it clog the front of every
-- future LIMITed read forever -- see list_live_listings' own comment for the
-- bounded window of redundant probing this leaves against an entry deleted
-- between seedings.
--
-- One-off backfill for every listing that predates this migration:
-- workers/scratch/backfill_x402_probe_queue.py (idempotent, safe to re-run).
--
-- term_end here is NOT authoritative (second same-night adversarial-review
-- finding, still 2026-09-06): it is written once at seed/reposition time and
-- never refreshed by extend_term(), which only ever touches x402_listings
-- and its recency/by-tag projections. workers/app/modules/x402_probe
-- /service.py's CassandraProbeRepository.list_live_listings() reads this
-- table for ordering/position only and judges expiry off a fresh, bounded,
-- concurrent batch read of the canonical x402_listings.term_end instead --
-- see that module's docstring for the incident this avoids. This column is
-- kept only as an informational snapshot for operators inspecting the table
-- directly.
CREATE TABLE IF NOT EXISTS x402_probe_queue (
  directory text,
  last_probed_at timestamp,
  url_hash text,
  url text,
  created_at timestamp,
  tags set<text>,
  term_end timestamp,
  payer text,
  verified_wallet text,
  category text,
  PRIMARY KEY ((directory), last_probed_at, url_hash)
) WITH CLUSTERING ORDER BY (last_probed_at ASC, url_hash ASC);
