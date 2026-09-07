USE algorand_platform;

-- Two independent x402 visibility-board gaps closed together (operator ask
-- 2026-09-07): the board has no category concept at all, and its only
-- click data is a single lifetime counter with no time dimension. See
-- app/modules/x402_board/services/board_service.py and api/routes.py.

-- Category (roadmap-adjacent to the directory's own `?category=`, migration
-- 099, but a SEPARATE closed enum -- BOARD_CATEGORIES in
-- app/modules/x402_board/models/domain.py, not an import of the directory's
-- LISTING_CATEGORIES, per this module's own "never depend on the directory
-- module's lifecycle" convention). Nullable, additive -- a pre-120 row reads
-- back null, mapped to DEFAULT_BOARD_CATEGORY ("other") by
-- CassandraPlacementStore._row_to_placement. Only BoardService.create()
-- writes it (validated pre-gate by board_service.validate_category()) -- a
-- boost (renew()) never touches it.
ALTER TABLE x402_board_entries ADD category text;
ALTER TABLE x402_board_by_recency ADD category text;

-- Category lookup projection, same shape/reasoning as x402_listings_by_tag
-- (096): x402_board_entries stores category as a plain column, not part of
-- any key, so filtering by it would need ALLOW FILTERING on a non-key
-- column, which CLAUDE.md section 4 forbids -- "add a lookup table
-- instead." A placement carries exactly one category (unlike the
-- directory's many tags per listing), so there is never more than one row
-- to write or delete per placement here -- see
-- CassandraPlacementStore.upsert's category-projection branch.
CREATE TABLE IF NOT EXISTS x402_board_by_category (
  category text,
  created_at timestamp,
  entry_id text,
  link text,
  name text,
  pitch text,
  payer text,
  term_end timestamp,
  settlement_tx_id text,
  boosted_until timestamp,
  PRIMARY KEY ((category), created_at, entry_id)
) WITH CLUSTERING ORDER BY (created_at DESC, entry_id ASC);

-- Click-event time series for the owner-only click-analytics read (GET
-- /api/v1/x402/board/:entry_id/clicks). Deliberately a SEPARATE table from
-- x402_board_clicks (098, the free feed's lifetime counter column): a
-- counter column cannot share a table with non-counter columns, and this
-- table answers a different question -- click activity OVER TIME, not a
-- running total. One partition per entry_id, clustered newest-first by
-- clicked_at, same (entry_id) -> (clicked_at DESC) shape as
-- x402_uptime_checks_by_url (117) -- two clicks landing in the same
-- millisecond collapse to one stored row, the same accepted precedent that
-- table already sets, and the lifetime total in x402_board_clicks is
-- unaffected either way since that counter is bumped separately. TTL
-- matches x402_uptime_checks_by_url's own 30-day retention -- storage is
-- bounded at the table level, independently of the caller-facing
-- ?days=/row-count caps in app/core/config.py's x402_board_click_history_*
-- settings.
CREATE TABLE IF NOT EXISTS x402_board_click_events (
  entry_id text,
  clicked_at timestamp,
  referrer text,
  PRIMARY KEY ((entry_id), clicked_at)
) WITH CLUSTERING ORDER BY (clicked_at DESC)
  AND default_time_to_live = 2592000;
