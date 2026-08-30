USE algorand_platform;

-- x402 directory category + renew (roadmap item 3, CLAUDE.md 9.1).
--
-- category: one of a fixed enum (data, ai, finance, identity, storage,
-- compute, social, tooling, other -- see x402_directory/models/domain.py),
-- validated before the payment gate, defaulting to other. Denormalized onto
-- both projections like every other listing column so a feed serves from
-- one table. A null (any row written before this migration) reads back as
-- other; no backfill.
--
-- No new table for GET /api/v1/x402/search?category=. The category is
-- projected into the existing x402_listings_by_tag (096) as one extra row
-- per listing under the reserved tag category:<name>, written and deleted
-- alongside the listing's real tag rows, so the category filter is the same
-- single-partition newest-first LIMITed read as ?tag= and needs no ALLOW
-- FILTERING. User-supplied tags in the category: namespace are refused at
-- the service layer so a listing cannot forge its way into a category row.
--
-- POST /api/v1/x402/list/renew (paid, listing price) needs no schema: it
-- rewrites the existing rows with term_end = max(now, term_end) + term,
-- created_at untouched (a renewal buys time, not a jump to the front of the
-- recency feed), settlement_tx_id and payer replaced. Because both
-- projections are keyed on created_at, every projection row is rewritten in
-- place by the same full INSERTs a relist uses, which is how they pick up
-- the new term_end. Only the current owner may renew a live listing; an
-- unowned or expired one may be renewed (and thereby claimed) by anyone,
-- the same ownership rule as a relist (094).
ALTER TABLE x402_listings ADD category text;
ALTER TABLE x402_listings_by_recency ADD category text;
ALTER TABLE x402_listings_by_tag ADD category text;
