USE algorand_platform;

-- Agent Discovery Search (Algorand Global x402 Challenge, roadmap item 8's
-- sibling on the social product -- see CLAUDE.md section 9). An external
-- agent asked, via this same social network, for a way to filter/search
-- the free-text `interests` field on x402_social_agents (105), which has
-- no other way to filter today. CLAUDE.md section 4: no ALLOW FILTERING
-- on non-key columns, so this is a dedicated lookup table, not a scan of
-- x402_social_agents.interests.

-- One row per (interest, wallet): a wallet appears once per distinct
-- interest tag it has declared. `interest` is the SAME normalized form
-- profile_service.normalize_interests already produces for the profile's
-- own `interests` column (trimmed, deduplicated, HTML-rejected -- no new
-- normalization scheme), so a search tag and a stored tag are transformed
-- identically and always agree on what counts as a match. `created_at` is
-- the profile's own REGISTRATION epoch (AgentProfile.created_at_epoch,
-- never touched by a later profile edit -- see that dataclass's own
-- comment), not the edit that most recently touched this row, since
-- "registration recency" is the ranking tiebreak GET /agents/search uses.
-- Kept in sync by app/modules/x402_social/services/profile_service.py: a
-- brand-new registration inserts one row per declared interest, and every
-- profile edit does a full delete of the wallet's OLD tag rows followed by
-- a full re-insert of its CURRENT tag rows (deliberately not diffed --
-- interests is capped at MAX_INTERESTS=10, so the extra writes are cheap).
-- No hidden_platform column: this table only ever reflects a profile's own
-- self-declared interests, so it has nothing of its own to tombstone --
-- an S2-hidden or hard-deleted target's search result is filtered at the
-- application layer, the same "the lookup projection is a hint, the
-- canonical row is authoritative" precedent x402_social_agents_by_recency
-- already sets (see stores/cassandra.py's own class docstring).
CREATE TABLE IF NOT EXISTS x402_social_agents_by_interest (
  interest text,
  wallet text,
  created_at timestamp,
  PRIMARY KEY ((interest), wallet)
);
