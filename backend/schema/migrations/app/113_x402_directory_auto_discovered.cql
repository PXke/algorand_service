USE algorand_platform;

-- Auto-discovered ("unclaimed") directory listings (owner ask 2026-09-06):
-- the directory product looked empty (2 real paid listings) next to
-- GoPlausible's own facilitator, which already publishes a large public
-- catalog of live Algorand-network x402 resources
-- (GET {x402_facilitator_url}discovery/resources). This migration lets the
-- directory republish that ALREADY-PUBLIC information as free, clearly
-- labelled, unclaimed listings without ever letting one be mistaken for a
-- real paid listing -- see app/modules/x402_directory/services/discovery_import.py.
--
-- source: 'paid' | 'auto_discovered' (StoredListing.source /
-- LISTING_SOURCES). Nullable -- a pre-113 row reads back null, which is
-- SOURCE_PAID by definition -- every row before this migration was, without
-- exception, a real paid listing. Only listing_service.create() writes
-- 'paid' -- only discovery_import.py writes 'auto_discovered'. Added to the
-- canonical table and to BOTH existing paid projections (090, 096) so a paid
-- read never has to special-case a missing column.
--
-- discovered_from: attribution for an auto-discovered row -- which public
-- feed it came from (discovery_import.DISCOVERY_SOURCE_LABEL). Empty for a
-- real paid listing.
ALTER TABLE x402_listings ADD source text;
ALTER TABLE x402_listings ADD discovered_from text;
ALTER TABLE x402_listings_by_recency ADD source text;
ALTER TABLE x402_listings_by_recency ADD discovered_from text;
ALTER TABLE x402_listings_by_tag ADD source text;
ALTER TABLE x402_listings_by_tag ADD discovered_from text;

-- A SEPARATE recency feed for auto-discovered listings, constant-partitioned
-- exactly like x402_listings_by_recency (090) but never sharing its
-- partition (AUTO_DISCOVERED_PARTITION, distinct from DIRECTORY_PARTITION):
-- an import of hundreds or thousands of free stubs must never crowd a real
-- paid listing out of the paid recency feed's own LIMITed read. Same column
-- shape as x402_listings_by_recency. ListingService.search() reads this as a
-- second, independently-bounded source and only ever appends its rows AFTER
-- every live paid listing already found -- see that method's docstring.
CREATE TABLE x402_listings_auto_discovered_by_recency (
    directory text,
    created_at timestamp,
    url_hash text,
    url text,
    price text,
    assets set<text>,
    description text,
    schema_json text,
    tags set<text>,
    term_end timestamp,
    settlement_tx_id text,
    payer text,
    category text,
    verified_wallet text,
    verified_at timestamp,
    reimburses boolean,
    contact text,
    source text,
    discovered_from text,
    PRIMARY KEY ((directory), created_at, url_hash)
) WITH CLUSTERING ORDER BY (created_at DESC, url_hash ASC);
