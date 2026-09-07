USE algorand_platform;

-- Boost pricing model (owner decision 2026-09-06, same-day competitive
-- pricing study): the directory's $0.10 recurring listing fee was the
-- highest in the entire x402-directory ecosystem, and no comparable
-- marketplace charges a fee just to STAY listed (Bazaar prunes after 30
-- days with no settlement, x402-list decays after 7 days of failed probes).
-- Paid renewal-to-survive is removed -- a listing now survives for as long
-- as it keeps passing health probes (workers/app/modules/x402_probe extends
-- term_end on every healthy probe -- see settings.x402_listing_term_days'
-- updated comment) -- and the now-redundant POST /list/renew /
-- POST /board/:entry_id/renew routes are repurposed into a NEW paid "boost"
-- feature: priority placement in search/board results for a fixed window,
-- never survival. See ListingService.renew() / BoardService.renew() and
-- ListingService.search() / BoardService.list_active()'s boosted-first sort.
--
-- boosted_until: 0/null means not boosted. Same timestamp type as term_end
-- on each table it is added to, for the same reason term_end is a
-- timestamp -- nullable, no primary key involved, safe to ALTER in place.
-- Only ListingService.renew() / BoardService.renew() ever write a non-null
-- value -- a pre-114 row reads back null, which is "not boosted," exactly
-- right since boosting did not exist before this migration.
--
-- Directory: added to the canonical table and to BOTH existing paid
-- projections (090, 096), same three tables migration 113 touched for its
-- own two new columns -- NOT x402_listings_auto_discovered_by_recency: an
-- auto-discovered ("unclaimed") stub has no real payer and can never be
-- boosted, so that table has nothing to gain from the column.
ALTER TABLE x402_listings ADD boosted_until timestamp;
ALTER TABLE x402_listings_by_recency ADD boosted_until timestamp;
ALTER TABLE x402_listings_by_tag ADD boosted_until timestamp;

-- Board: added to the canonical table and its own recency projection (091).
ALTER TABLE x402_board_entries ADD boosted_until timestamp;
ALTER TABLE x402_board_by_recency ADD boosted_until timestamp;
