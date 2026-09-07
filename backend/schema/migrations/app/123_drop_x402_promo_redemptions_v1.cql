USE algorand_platform;

-- x402_promo_redemptions (v1, created in 100_x402_promo_codes.cql) was
-- superseded by x402_promo_redemptions_v2 in 101_x402_promo_multi_redeem.cql
-- (owner ask 2026-09-01, "can a wallet redeem more than once") -- Cassandra
-- can't ALTER a primary key, so the multi-redeem design needed a new table
-- with (code, wallet_hash), redemption_id clustering. 101 deliberately left
-- v1 in place, untouched and unread from then on, per this repo's own
-- retire-then-drop-later pattern (069/074/078/079/081/082/085/086).
--
-- Confirmed dead 2026-09-07: no INSERT/SELECT statement targeting this table
-- exists anywhere in current code (grep across backend/app and git history
-- of promo.py) -- every bare "x402_promo_redemptions" hit left in the
-- codebase is prose in a comment/docstring, not a live query. All 5 rows
-- (101's own comment expected 2, live count was 5 -- discrepancy is a stale
-- authoring-time snapshot, not evidence of a live write path) are operator
-- test data (LIVETEST-*, MARKETING-SCAN-TEST, wallet_hash matching the known
-- probe/operator wallet) -- zero real customer redemptions. Full row export
-- archived at docs/adr/archived-data/x402_promo_redemptions_v1_final_export_20260907.txt
-- before this drop.

DROP TABLE IF EXISTS x402_promo_redemptions;
