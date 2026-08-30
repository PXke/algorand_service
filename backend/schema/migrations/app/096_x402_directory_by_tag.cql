USE algorand_platform;

-- Tag lookup projection for GET /api/v1/x402/search?tag=... over the x402
-- endpoint directory (090). x402_listings stores tags as a set<text> that is
-- not part of any key, so filtering by tag would need ALLOW FILTERING on a
-- non-key column, which CLAUDE.md section 4 forbids -- "add a lookup table
-- instead". Same denormalization precedent as x402_listings_by_recency (090):
-- one row per (tag, listing), written alongside the recency projection on
-- every listing write and removed on delete, so the tag partition is the
-- newest-first feed for that one tag with no fan-out.
--
-- The partition key is the NORMALIZED tag (trimmed, lowercased, the same
-- normalization listing_service applies before storing tags), so a search
-- for "FX" and one for "fx" read the same partition. A partition holds at
-- most one row per listing carrying that tag, so it is bounded by the
-- directory itself (hundreds-to-thousands of rows for the competition), and
-- every read is LIMITed. If one tag ever outgrows a partition, shard the key
-- by term month, same escape hatch 090 names for the recency feed.
--
-- Columns are the full listing, denormalized exactly like the recency
-- projection, so a tag search serves from this table alone. Expiry is
-- filtered in ListingService after the LIMITed read, not in CQL, for the
-- same reason and with the same trade-off as the recency feed.
CREATE TABLE IF NOT EXISTS x402_listings_by_tag (
  tag text,
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
  PRIMARY KEY ((tag), created_at, url_hash)
) WITH CLUSTERING ORDER BY (created_at DESC, url_hash ASC);
