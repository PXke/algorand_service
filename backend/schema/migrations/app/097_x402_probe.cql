USE algorand_platform;

-- x402 probe / monitoring + verified badge (roadmap item 7, CLAUDE.md 9.1).
--
-- A workers beat (app.tasks.x402_probe.probe_listed_endpoints) sends one
-- UNPAID request to every live directory listing (090), never follows a
-- redirect into a private range, never reads more than 64 KiB, and never
-- pays. It records whether the endpoint was reachable and whether it served
-- a well-formed 402 offer (PAYMENT-REQUIRED header that parses and carries
-- accepts[]). Probe traffic is unpaid and labelled by its User-Agent, so it
-- can never count as volume anywhere.
--
-- verified_wallet / verified_at: the badge. Set by the probe when the
-- payTo the endpoint actually advertises equals the wallet that paid for
-- the listing (payer, 094) -- proof that the lister controls the endpoint
-- and not merely that they paid to describe it. Cleared when a previously
-- verified listing starts advertising a different payTo. Written with
-- UPDATE ... IF EXISTS on all three tables so the probe can never upsert a
-- phantom projection row for a listing that was deleted mid-sweep.
-- Readers treat the badge as valid only while verified_wallet == payer, so
-- a relist by a new owner never inherits the old owner's badge.
ALTER TABLE x402_listings ADD verified_wallet text;
ALTER TABLE x402_listings ADD verified_at timestamp;
ALTER TABLE x402_listings_by_recency ADD verified_wallet text;
ALTER TABLE x402_listings_by_recency ADD verified_at timestamp;
ALTER TABLE x402_listings_by_tag ADD verified_wallet text;
ALTER TABLE x402_listings_by_tag ADD verified_at timestamp;

-- Probe history, one row per probe, newest first within the endpoint's
-- partition. Write-only on the hot path (the beat never reads it). Bounded
-- by a 30-day table TTL rather than by reads: at one probe per 30 minutes
-- that is at most 1440 rows per listing.
CREATE TABLE IF NOT EXISTS x402_probe_results (
  url_hash text,
  probed_at timestamp,
  url text,
  reachable boolean,
  http_status int,
  latency_ms int,
  served_valid_402 boolean,
  payto_seen text,
  error text,
  PRIMARY KEY ((url_hash), probed_at)
) WITH CLUSTERING ORDER BY (probed_at DESC)
  AND default_time_to_live = 2592000;

-- Point-read projection of the newest probe per endpoint, for the free
-- GET /api/v1/x402/directory/probe?url= route. Same columns, overwritten
-- (full INSERT) on every probe, written AFTER the history row.
CREATE TABLE IF NOT EXISTS x402_probe_latest (
  url_hash text PRIMARY KEY,
  probed_at timestamp,
  url text,
  reachable boolean,
  http_status int,
  latency_ms int,
  served_valid_402 boolean,
  payto_seen text,
  error text
);
