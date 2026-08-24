USE algorand_platform;

-- Article data-model consolidation, step 4b (part 2): remove the default TTL
-- on session/diagnostic history tables. Two problems converge: this session
-- repeatedly needed history older than the TTL window and it was already
-- gone, and a TTL doesn't just delete data quietly -- every expiry writes a
-- Cassandra tombstone, so a table rolling off continuously degrades read
-- performance on it indefinitely. Explicit tradeoff: these tables now grow
-- unbounded instead of self-pruning -- acceptable given per-row size is
-- small, worth an application-level archival job later if it ever becomes a
-- real concern, rather than solving it with a TTL again.
--
-- Sequenced together with the bucket-scheme fix (application-code change,
-- no DDL -- `bucket`/`day` were already generic partition-key columns, only
-- the VALUES written into them change going forward): compose_sessions,
-- translation_sessions, compose_feedback, tool_suggestions previously used a
-- single hardcoded bucket="all" partition, which was only safe because the
-- TTL kept it bounded. Removing the TTL without also fixing that would trade
-- a tombstone-stream problem for an unbounded-single-partition problem.
-- gatekeeper_decisions/gatekeeper_audit are already day-bucketed and need no
-- code change, just this ALTER.
--
-- pageview_direct_sample (7-day TTL) is deliberately excluded -- aggregate
-- analytics, not session/diagnostic history, a separate judgment call.
ALTER TABLE compose_sessions WITH default_time_to_live = 0;
ALTER TABLE translation_sessions WITH default_time_to_live = 0;
ALTER TABLE compose_feedback WITH default_time_to_live = 0;
ALTER TABLE tool_suggestions WITH default_time_to_live = 0;
ALTER TABLE gatekeeper_decisions WITH default_time_to_live = 0;
ALTER TABLE gatekeeper_audit WITH default_time_to_live = 0;
