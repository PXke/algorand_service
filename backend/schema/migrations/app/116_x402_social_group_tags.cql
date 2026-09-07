USE algorand_platform;

-- Group Discovery by tag (Algorand Global x402 Challenge -- see CLAUDE.md
-- section 9 and real agent demand via Moltbook/Clawstr feedback,
-- 2026-09-06: "agent discovery by interest tags is a clean coordination
-- primitive"). Agent-tag search already exists (GET /agents/search,
-- migration 110) -- this is the same idea for groups, which had no tag
-- concept at all before this migration.

-- ALTER TABLE ADD tags on the canonical row ONLY, not the recency browse
-- projection (x402_social_groups_by_recency, 106) -- same thin-projection
-- precedent x402_social_agents_by_recency already sets (a profile's own
-- newest-first browse feed never carried bio/location/interests either):
-- the plain, unfiltered GET /groups listing never needed a group's tags
-- before, so that projection's write shape stays unchanged. A pre-115 row
-- reads back tags=null, mapped to an empty list in Python (StoredGroup's
-- own default), never a fabricated tag set.
ALTER TABLE x402_social_groups ADD tags frozen<list<text>>;

-- Tag lookup projection so GET /api/v1/x402/social/groups?tag=... can filter
-- without ALLOW FILTERING on the new (non-key) tags column (CLAUDE.md
-- section 4). One row per (tag, group_id) -- deliberately NOT a
-- denormalized copy of the group's own fields the way x402_listings_by_tag
-- (096) is: a group's hidden_platform (Phase S2, 108) can change after
-- creation and this table has no write path to keep a denormalized copy in
-- sync, so it carries only enough to look the group back up.
-- group_service.list_by_tag reads up to domain.GROUP_TAG_CANDIDATE_CAP
-- candidate rows, then point-reads x402_social_groups (GET_GROUP) for each
-- match -- always the fresh canonical row, never stale on a later
-- hidden_platform flip. Written ONCE, at group creation time, by
-- services/group_service.py -- there is no PATCH /groups to keep this in
-- sync with later (tags are immutable after a group is created, in v1).
CREATE TABLE IF NOT EXISTS x402_social_groups_by_tag (
  tag text,
  group_id text,
  created_at timestamp,
  PRIMARY KEY ((tag), group_id)
);
