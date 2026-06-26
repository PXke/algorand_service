USE algorand_platform;

-- Promote frontier_status to a real column (was only in metadata) so it can be
-- queried directly, and add SAI indexes for the Domains tab filters instead of
-- scanning the whole table.
ALTER TABLE domain_tracking ADD frontier_status text;

CREATE CUSTOM INDEX IF NOT EXISTS domain_frontier_status_sai
  ON domain_tracking (frontier_status) USING 'sai';

CREATE CUSTOM INDEX IF NOT EXISTS domain_is_relevant_sai
  ON domain_tracking (is_relevant) USING 'sai';
