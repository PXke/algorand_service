USE algorand_platform;

-- Self-declared directory listing flags (owner ask 2026-09-02).
--
-- reimburses: a listing owner's own claim that they refund a payer on a
-- failed delivery. NOT verified by us for third-party listings -- exactly as
-- trustworthy as the endpoint owner is, no more. Nullable; a pre-104 row (or
-- any listing that omits it) reads back as unset/false, never a false claim.
--
-- contact: a bounded free-text point of contact (email, URL, agent
-- identifier -- not shape-validated beyond a length cap, see
-- MAX_CONTACT_LENGTH in x402_directory/services/listing_service.py) so
-- someone with an issue has somewhere to go. Nullable; omitted reads back
-- as empty.
--
-- Both are set only at listing/relist time (POST /api/v1/x402/list), same
-- as every other descriptive field (price, description, tags, category) --
-- renew() deliberately changes nothing about the listing but its term, so
-- these follow that existing precedent rather than becoming the first
-- renew-editable fields.
--
-- Denormalized onto both projections like every other listing column so a
-- feed read (search, listing detail) serves from one table with no join.
ALTER TABLE x402_listings ADD reimburses boolean;
ALTER TABLE x402_listings ADD contact text;
ALTER TABLE x402_listings_by_recency ADD reimburses boolean;
ALTER TABLE x402_listings_by_recency ADD contact text;
ALTER TABLE x402_listings_by_tag ADD reimburses boolean;
ALTER TABLE x402_listings_by_tag ADD contact text;
