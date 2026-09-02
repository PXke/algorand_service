USE algorand_platform;

-- x402 agent social network, Phase S0 ONLY: the identity/foundation layer
-- (Algorand Global x402 Challenge, Composite entry -- see CLAUDE.md section 9
-- and docs/x402-social-design.md, sections 1.1 and 7). Phase S1's posts /
-- comments / reactions / follows / groups / trending tables and Phase S2's
-- moderation tables (explicitly NOT approved for implementation -- see the
-- design doc's own section 5 sign-off block) are deliberately NOT created
-- here -- they land in their own later migrations when those phases ship.
--
-- The wallet IS the identity -- there is no separate user/account id
-- anywhere in this module. POST /api/v1/x402/social/register's registered
-- wallet is exactly the payment's payer (app/modules/x402_social/api/
-- routes.py, section 4.1 of the design doc), never a request-body field.

-- Canonical profile, point read by wallet.
CREATE TABLE IF NOT EXISTS x402_social_agents (
  wallet text PRIMARY KEY,
  name text,
  bio text,
  mission text,
  location text,
  interests frozen<list<text>>,
  emoji text,
  created_at timestamp,
  updated_at timestamp,
  -- The registration payment's settlement txid (x402_settlements, migration
  -- 090, via the shared modules/x402/settlement.py ledger). No settlement
  -- table of its own -- same "one shared ledger" precedent every other x402
  -- product in this backend already follows.
  settlement_tx_id text
);

-- Newest-first discovery feed for GET /api/v1/x402/social/agents. Ordering
-- x402_social_agents by a non-key created_at would need ALLOW FILTERING,
-- which CLAUDE.md section 4 forbids -- same denormalization precedent as
-- x402_listings_by_recency (090) / x402_board_by_recency (091) / etc.
--
-- bucket is a constant partition key ('default'), so the whole feed lives in
-- one partition and reads newest-first with no fan-out -- bounded on
-- purpose, expected in the hundreds-to-thousands of agents for the
-- competition, every read LIMITed by x402_social_max_results. Shard by
-- registration month if the agent roster ever outgrows one partition.
--
-- Deliberately thin (wallet/name/mission/emoji only, no bio/location/
-- interests): this is a browse-feed card, not the full profile --
-- GET /api/v1/x402/social/agents/{wallet} is the point read for the rest.
-- created_at is never re-stamped by a profile edit (PATCH /profile is an
-- in-place rewrite of this same row, same key), so this table needs no
-- delete-then-insert dance the way a listing's tag/category projections do
-- when those fields change -- see stores/cassandra.py.
CREATE TABLE IF NOT EXISTS x402_social_agents_by_recency (
  bucket text,
  created_at timestamp,
  wallet text,
  name text,
  mission text,
  emoji text,
  PRIMARY KEY ((bucket), created_at, wallet)
) WITH CLUSTERING ORDER BY (created_at DESC, wallet ASC);
