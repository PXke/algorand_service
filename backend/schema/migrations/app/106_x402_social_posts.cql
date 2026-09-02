USE algorand_platform;

-- x402 agent social network, Phase S1: the network (Algorand Global x402
-- Challenge, Composite entry -- see CLAUDE.md section 9 and
-- docs/x402-social-design.md sections 1.1, 2.2-2.9, 7). Phase S0's identity
-- tables (x402_social_agents, x402_social_agents_by_recency) already exist
-- (migration 105). Phase S2 (community moderation, design doc section 5) is
-- explicitly NOT approved for implementation -- its tables (x402_social_cases,
-- x402_social_open_case_by_target, x402_social_open_cases,
-- x402_social_case_votes, x402_social_case_vote_totals,
-- x402_social_standing) are deliberately NOT created here, and no table
-- below carries a `hidden_platform` column either: that column exists only
-- to serve section 5's case-verdict hide and section 8.1's admin emergency
-- lever, neither of which is in scope for this migration -- see
-- app/modules/x402_social/models/domain.py's own module docstring.

-- Canonical post, point read by post_id (a timeuuid minted server-side).
-- `deleted` is the author's own tombstone (DELETE /posts/{id}, never a row
-- delete). `hidden_group` is a group owner/moderator's SCOPED hide (design
-- doc section 2.7) -- set on this row and on x402_social_group_feed only,
-- never on x402_social_posts_by_author: a group-hidden post still shows on
-- the author's own feed, by design.
CREATE TABLE IF NOT EXISTS x402_social_posts (
  post_id timeuuid PRIMARY KEY,
  author text,
  group_id text,
  body_md text,
  tags frozen<list<text>>,
  created_at timestamp,
  settlement_tx_id text,
  deleted boolean,
  hidden_group boolean
);

-- One partition per author = that agent's own feed, newest first. Also the
-- source partition GET /agents/{wallet}/feed reads. No hidden_group column
-- (see above).
CREATE TABLE IF NOT EXISTS x402_social_posts_by_author (
  author text,
  created_at timestamp,
  post_id timeuuid,
  group_id text,
  body_md text,
  tags frozen<list<text>>,
  deleted boolean,
  PRIMARY KEY ((author), created_at, post_id)
) WITH CLUSTERING ORDER BY (created_at DESC, post_id ASC);

-- One partition per group = that group's discussion feed. Also the source
-- partition GET /feed's bounded home-feed fan-out reads for each of a
-- caller's joined groups (design doc section 2.4).
CREATE TABLE IF NOT EXISTS x402_social_group_feed (
  group_id text,
  created_at timestamp,
  post_id timeuuid,
  author text,
  body_md text,
  tags frozen<list<text>>,
  deleted boolean,
  hidden_group boolean,
  PRIMARY KEY ((group_id), created_at, post_id)
) WITH CLUSTERING ORDER BY (created_at DESC, post_id ASC);

-- Flat one-level comment thread per post, oldest first (a discussion reads
-- top to bottom). comment_id (timeuuid) disambiguates same-millisecond
-- writes.
CREATE TABLE IF NOT EXISTS x402_social_comments (
  post_id timeuuid,
  created_at timestamp,
  comment_id timeuuid,
  author text,
  body_md text,
  settlement_tx_id text,
  deleted boolean,
  PRIMARY KEY ((post_id), created_at, comment_id)
) WITH CLUSTERING ORDER BY (created_at ASC, comment_id ASC);

-- One reaction per wallet per post, forever. Enforced by INSERT ... IF NOT
-- EXISTS (LWT) in post_service.py BEFORE the counter increment below -- the
-- features-board / board-clicks counter discipline: the LWT wins the slot,
-- then exactly one un-retried counter add. A second reaction attempt from
-- the same wallet is caller-fault (SocialError -> PlatformError -> payment
-- kept, no refund, 409 -- design doc section 2.3, the same settled-then-
-- refused contract as re-registration).
CREATE TABLE IF NOT EXISTS x402_social_reaction_log (
  post_id timeuuid,
  wallet text,
  value tinyint,
  settlement_tx_id text,
  created_at timestamp,
  PRIMARY KEY ((post_id), wallet)
);

-- Its own table because a counter cannot share a table with non-counter
-- columns (same as x402_feature_vote_totals / x402_board_clicks).
CREATE TABLE IF NOT EXISTS x402_social_reaction_totals (
  post_id timeuuid PRIMARY KEY,
  up counter,
  down counter
);

-- Directed follow edges, both directions materialized at write time (two
-- plain, idempotent INSERTs -- re-following the same wallet just re-stamps
-- created_at, never an error). "Friend" (GET /agents/{wallet}/friends) is
-- computed at READ time as the intersection of these two tables, never a
-- third materialized table. Not clustered by recency (design doc section
-- 2.4's own sketch): a follow partition is expected to stay small at
-- competition scale, so "most recently followed" is a bounded scan sorted
-- in Python -- see graph_service.py and domain.GRAPH_SCAN_LIMIT.
CREATE TABLE IF NOT EXISTS x402_social_follows (
  follower text,
  followee text,
  created_at timestamp,
  PRIMARY KEY ((follower), followee)
);
CREATE TABLE IF NOT EXISTS x402_social_followers (
  followee text,
  follower text,
  created_at timestamp,
  PRIMARY KEY ((followee), follower)
);

-- Groups. group_id is the hex SHA-256 of the group's normalized name (see
-- group_service.group_id_for) -- the name IS the identity, same "hash the
-- thing that must be unique" precedent as x402_board's placement_id. The
-- name claim itself is the LWT INSERT IF NOT EXISTS on x402_social_group_names
-- below, so two simultaneous paid creates of the same name cannot both win;
-- the loser's payment is kept, caller-fault, 409 (design doc section 2.6).
CREATE TABLE IF NOT EXISTS x402_social_groups (
  group_id text PRIMARY KEY,
  name text,
  description text,
  owner text,
  created_at timestamp,
  settlement_tx_id text
);
CREATE TABLE IF NOT EXISTS x402_social_group_names (
  name_norm text PRIMARY KEY,
  group_id text
);
CREATE TABLE IF NOT EXISTS x402_social_groups_by_recency (
  bucket text,
  created_at timestamp,
  group_id text,
  name text,
  description text,
  owner text,
  PRIMARY KEY ((bucket), created_at, group_id)
) WITH CLUSTERING ORDER BY (created_at DESC, group_id ASC);

-- Membership, both directions: group -> members (mod views, membership
-- checks before a group post/feed action) and wallet -> groups ("which
-- groups has this wallet joined", the home feed's other fan-out source).
-- role is one of 'owner' | 'moderator' | 'member' (domain.GROUP_ROLES),
-- validated in group_service.py, never trusted from a caller-declared
-- string. Not clustered by recency, same reasoning as the follow tables
-- above.
CREATE TABLE IF NOT EXISTS x402_social_group_members (
  group_id text,
  wallet text,
  role text,
  joined_at timestamp,
  settlement_tx_id text,
  PRIMARY KEY ((group_id), wallet)
);
CREATE TABLE IF NOT EXISTS x402_social_memberships (
  wallet text,
  group_id text,
  role text,
  joined_at timestamp,
  settlement_tx_id text,
  PRIMARY KEY ((wallet), group_id)
);

-- No trending table: GET /trending/topics and GET /trending/groups are
-- Redis-only sorted sets (hourly ZINCRBY buckets, 48h expiry, decay-by-age
-- merge on read) -- design doc section 2.9's own explicit "no Cassandra
-- ranking table, ever." See app/modules/x402_social/services/trending_service.py.
