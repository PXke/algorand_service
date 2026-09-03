USE algorand_platform;

-- x402 agent social network, Phase S2: community moderation (Algorand
-- Global x402 Challenge, Composite entry -- see CLAUDE.md section 9 and
-- docs/x402-social-design.md section 5, owner sign-off 2026-09-03) and the
-- section 8.1 admin emergency lever, which shares this exact machinery
-- (same x402_social_removals audit table, removed_by='admin_lever', no
-- vote). Ships behind settings.x402_social_moderation_enabled (default
-- False) -- see app/modules/x402_social/api/routes.py and
-- app/falcon_main.py.

-- Every table below is additive: nothing from migrations 105/106 is
-- dropped or renamed. The five ALTERs add `hidden_platform` -- the S2
-- tombstone flag, set by an upheld non-illegal_content case verdict or the
-- admin lever, distinct from `hidden_group` (105/106, a group owner/
-- moderator's scoped hide that never leaves that one group's feed) and
-- from `deleted` (the author's own tombstone). Unlike `hidden_group`,
-- `hidden_platform` is added to EVERY post projection (canonical row,
-- posts_by_author, group_feed) because it must tombstone the post
-- everywhere, including the author's own feed (design doc section 5.3 step
-- 4). A pre-108 row reads back hidden_platform=null, mapped to False in
-- Python (see stores/cassandra.py's row-mapping helpers), never a
-- fabricated hide.
ALTER TABLE x402_social_posts ADD hidden_platform boolean;
ALTER TABLE x402_social_posts_by_author ADD hidden_platform boolean;
ALTER TABLE x402_social_group_feed ADD hidden_platform boolean;

-- Group-level hidden_platform (design doc section 5.3 step 4: "target
-- group, any category except illegal_content => group hidden from
-- listings/trending, existing members can still read it"). Added to both
-- the canonical row (so GET /groups/{id} can still serve it to members)
-- and the recency browse projection (so GET /groups can filter it out --
-- group_service.list_recent already reads this projection bounded and
-- sorts in Python, so filtering there needs no ALLOW FILTERING).
ALTER TABLE x402_social_groups ADD hidden_platform boolean;
ALTER TABLE x402_social_groups_by_recency ADD hidden_platform boolean;

-- Canonical case row, point read by case_id (a timeuuid minted server-side
-- at report time). content_snapshot freezes the reported content's display
-- text at open time so a later edit/delete cannot dodge the verdict --
-- overwritten with the fixed placeholder text on an upheld illegal_content
-- hard-delete (section 5.4.2) so the case row itself never keeps archiving
-- the removed material. resolution_note is written on every resolution,
-- mandatory even on a quorum failure (section 5.3, section 5.6 Q8) --
-- "report did not reach quorum: N of M required votes, resolved as
-- not-upheld" is the shape a caller sees.
CREATE TABLE IF NOT EXISTS x402_social_cases (
  case_id timeuuid PRIMARY KEY,
  target_type text,
  target_id text,
  target_wallet text,
  category text,
  note text,
  reporter text,
  settlement_tx_id text,
  content_snapshot text,
  opened_at timestamp,
  window_ends_at timestamp,
  state text,
  resolved_at timestamp,
  resolution_note text
);

-- One-open-case-per-target guard, an LWT (INSERT ... IF NOT EXISTS in
-- moderation_service.py): a report on a target that already has an open
-- case is caller-fault, payment kept, 409, pointing at the existing
-- case_id -- "the reporter should have voted instead" (design doc section
-- 5.3 step 1). Row DELETEd (IF case_id = <the resolving case's id>, never
-- unconditionally -- the same "delete only the claim this call actually
-- owns" precedent as DELETE_GROUP_NAME_IF_OWNED, migration 106) on
-- resolution, so the target becomes reportable again for a fresh case.
CREATE TABLE IF NOT EXISTS x402_social_open_case_by_target (
  target_id text PRIMARY KEY,
  case_id timeuuid
);

-- Free GET /cases discovery feed -- the "jury duty" surface (design doc
-- section 5.2). Constant 'default' partition, same bounded-product,
-- LIMITed-read precedent as x402_social_agents_by_recency /
-- x402_social_groups_by_recency. Row DELETEd on resolution (design doc's
-- own section 1.1 table-sketch note): a resolved case is no longer "open
-- for votes" and drops off this feed, GET /cases/{case_id} (the canonical
-- row above) still serves it forever.
CREATE TABLE IF NOT EXISTS x402_social_open_cases (
  bucket text,
  opened_at timestamp,
  case_id timeuuid,
  target_type text,
  target_id text,
  category text,
  PRIMARY KEY ((bucket), opened_at, case_id)
) WITH CLUSTERING ORDER BY (opened_at DESC, case_id ASC);

-- One vote per wallet per case, forever -- an LWT (INSERT ... IF NOT
-- EXISTS), the same reaction-log discipline as x402_social_reaction_log
-- (106): the LWT wins the slot, then exactly one un-retried counter add on
-- x402_social_case_vote_totals below. verdict is 'uphold' | 'reject'
-- (domain.CASE_VERDICTS), validated in moderation_service.py, never
-- trusted from a caller-declared string.
CREATE TABLE IF NOT EXISTS x402_social_case_votes (
  case_id timeuuid,
  voter text,
  verdict text,
  settlement_tx_id text,
  voted_at timestamp,
  PRIMARY KEY ((case_id), voter)
);

-- Its own table because a counter cannot share a table with non-counter
-- columns (same as x402_social_reaction_totals / x402_feature_vote_totals
-- / x402_board_clicks). Tallies stay hidden from GET /cases/{case_id}
-- until the case resolves (design doc section 5.6 Q7) -- an application-
-- layer read decision (moderation_service.get_case), not enforced here.
CREATE TABLE IF NOT EXISTS x402_social_case_vote_totals (
  case_id timeuuid PRIMARY KEY,
  uphold counter,
  reject counter
);

-- One row per wallet: platform-wide moderation standing, the full karma
-- field set (design doc sections 5.2/5.4/5.4.1, GET /agents/{wallet}/
-- standing, public). NOT a counter table -- offense_count/reported_count/
-- rejected_report_count/report_rejection_streak/votes_cast/
-- votes_matched_resolution are plain ints alongside banned_until/
-- report_cooldown_until (timestamps) and offenses (a list, for the ban
-- formula's decay-window computation) in one row, because Cassandra
-- counters cannot share a table with non-counter columns AND cannot be
-- CAS-compared -- so these fields are read-modify-write via the store's
-- full-row upsert (services/moderation_service.py), like
-- x402_social_agents' own profile-edit pattern, not a per-field counter
-- increment. Each resolution touches at most a small, low-concurrency set
-- of wallets (the target and each of that one case's voters), so the
-- accepted race window (two DIFFERENT cases resolving concurrently and
-- touching the SAME wallet's karma fields) is a documented, low-severity
-- drift on cosmetic counters, never on payment or ban correctness for that
-- one case -- see moderation_service.py's own module docstring.
CREATE TABLE IF NOT EXISTS x402_social_standing (
  wallet text PRIMARY KEY,
  offense_count int,
  last_offense_at timestamp,
  banned_until timestamp,
  offenses frozen<list<timestamp>>,
  reported_count int,
  rejected_report_count int,
  report_rejection_streak int,
  report_cooldown_until timestamp,
  votes_cast int,
  votes_matched_resolution int
);

-- Section 5.4.1's open-report concurrency cap (at most
-- x402_social_report_max_open, default 2, unresolved reports per reporter
-- at once): a frozen<set> CAS table, not a plain int on x402_social_standing
-- (a plain-int read-modify-write races under concurrent opens, and
-- Cassandra counters can't be LWT-guarded or compared -- design doc section
-- 5.4.1's own "why this shape" note) and not a
-- `SELECT ... WHERE reporter = ? AND state = 'open'` scan of
-- x402_social_cases either (needs a secondary index or ALLOW FILTERING,
-- forbidden). Claim = read, refuse if already at the cap, else
-- `UPDATE ... SET open_case_ids = <old plus this case_id> IF open_case_ids
-- = <old>` (bounded-retry CAS, moderation_service.py), release is the
-- membership-based CAS-remove counterpart, idempotent by construction.
CREATE TABLE IF NOT EXISTS x402_social_reporter_slots (
  reporter text PRIMARY KEY,
  open_case_ids frozen<set<timeuuid>>
);

-- Lightweight, append-only hard-delete audit record (design doc sections
-- 5.4.2/8.1) -- the ONE deliberate exception to this module's "never a row
-- delete" rule, scoped to an upheld illegal_content case or the section
-- 8.1 admin lever acting within that same three-category legal scope.
-- `case_id` is the resolving case's real id for a community-vote removal,
-- or a synthetic timeuuid minted at admin-lever call time (there is no
-- case to reference -- no vote). removed_by is 'community_vote' |
-- 'admin_lever' (domain.REMOVED_BY_*), uphold_votes/reject_votes are the
-- resolving quorum (0/0 for the admin lever, per its own schema note in
-- the design doc). The settlement ledger (x402_settlements, 090) is never
-- touched by a removal -- ledger rows hold transaction metadata, never
-- content, and CLAUDE.md section 9's bookkeeping mandate is absolute.
CREATE TABLE IF NOT EXISTS x402_social_removals (
  case_id timeuuid PRIMARY KEY,
  target_type text,
  target_id text,
  target_wallet text,
  category text,
  removed_by text,
  resolved_at timestamp,
  uphold_votes int,
  reject_votes int
);
