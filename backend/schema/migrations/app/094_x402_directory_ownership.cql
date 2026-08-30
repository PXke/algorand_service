USE algorand_platform;

-- Adds ownership tracking to the x402 endpoint directory (090), closing a
-- real gap: x402_listings is keyed purely by hash(url), with no record of
-- who paid to list it, so ANY payer who pays the listing fee for an
-- already-listed url can silently overwrite its price, description, assets,
-- and schema -- a directory entry makes a factual claim about a real
-- third-party endpoint, so this is not cosmetic griefing the way it would be
-- on an ad board, it is a live misinformation vector on a catalog other
-- agents are meant to trust.
--
-- Not the same fix as x402_board's (payer, link) partition key: the
-- directory's whole point is ONE canonical entry per url (re-listing
-- overwrites, by design, per 090's own comment), not one tile per payer --
-- keying on (payer, url) here would let two payers list the same url as two
-- competing directory entries, fragmenting search results for what is
-- supposed to be a single source of truth about one real endpoint. Instead:
-- keep url_hash as the sole key, but record who currently owns the entry,
-- and refuse a relist by a DIFFERENT payer at the service layer (see
-- listing_service.py) rather than silently overwriting. An empty existing
-- payer (a listing created before this migration) is treated as unowned --
-- the first relist under the new rule claims it, rather than permanently
-- locking out every pre-migration listing.
ALTER TABLE x402_listings ADD payer text;
ALTER TABLE x402_listings_by_recency ADD payer text;
