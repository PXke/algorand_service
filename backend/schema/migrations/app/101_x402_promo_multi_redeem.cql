USE algorand_platform;

-- Lets one promo code (100) allow a wallet more than one redemption --
-- owner ask 2026-09-01: "can a wallet redeem more than once? like 4?" for
-- the marketing-testing promo code, generalized into a per-code setting
-- rather than a one-off hack for that single code.
--
-- x402_promo_codes gains max_redemptions_per_wallet, defaulting to 1 so
-- every code created before this migration keeps its exact prior behavior
-- (a null reads back as 1 in Python, see promo.py) -- ADD COLUMN is safe
-- here, no primary key involved.
ALTER TABLE x402_promo_codes ADD max_redemptions_per_wallet int;

-- x402_promo_redemptions' PRIMARY KEY ((code), wallet_hash) (100) can hold
-- at most ONE row per wallet per code -- that was fine when "one row exists"
-- WAS the abuse cap (the INSERT ... IF NOT EXISTS was both the audit write
-- and the atomic once-per-wallet claim, see 100's own migration comment).
-- Once a wallet may redeem N times, one row can no longer mean both "this
-- wallet redeemed" and "here is every time" -- a table needs a NEW primary
-- key to hold multiple rows per wallet, and Cassandra cannot ALTER a
-- primary key in place, so this is a new table, not a column add.
--
-- PRIMARY KEY ((code, wallet_hash), redemption_id): partitioned by
-- (code, wallet_hash) so "how many has this wallet redeemed" is one
-- single-partition read (a bounded SELECT ... LIMIT, capped at the
-- per-code max so it never scans more rows than a redemption could ever
-- need to check). Clustered by redemption_id, a `timeuuid` generated
-- server-side via CQL's `now()` on every insert (modules/x402/promo.py
-- never supplies it) -- NOT a plain `redeemed_at timestamp` clustering key,
-- deliberately: a first attempt at this migration used `redeemed_at`
-- (millisecond-precision `int(datetime.now().timestamp())` from Python) as
-- the clustering/dedup key and it collided under realistic rapid
-- back-to-back redemptions from the same wallet (exactly the use case this
-- migration exists for), wrongly rejecting a genuine 2nd/3rd/4th
-- redemption as an "exact duplicate". `timeuuid` is time-ordered (so
-- CLUSTERING ORDER BY DESC is still newest-first) but collision-proof by
-- construction (embeds a clock sequence + node id), and `now()` is always
-- fresh, so this insert is no longer meaningfully an `IF NOT EXISTS` dedup
-- guard at all -- it always applies. That's fine: the actual abuse cap
-- lives in Redis (a new per-(code, wallet_hash) counter, INCR-with-cap,
-- mirroring the existing global remaining-count DECR pattern), so this
-- table only ever needed to be the durable audit log, the same role
-- x402_promo_codes plays for the total budget -- not a second enforcement
-- mechanism. `redeemed_at` stays as a plain (non-key) timestamp column for
-- human-facing display. The old table (100) is left in place, untouched
-- and unread from here on: it holds exactly the 2 redemption rows written
-- before this migration, the complete real history to date -- nothing is
-- lost, there is just nothing further to append to it.
CREATE TABLE IF NOT EXISTS x402_promo_redemptions_v2 (
  code text,
  wallet_hash text,
  redemption_id timeuuid,
  redeemed_at timestamp,
  resource text,
  PRIMARY KEY ((code, wallet_hash), redemption_id)
) WITH CLUSTERING ORDER BY (redemption_id DESC);
