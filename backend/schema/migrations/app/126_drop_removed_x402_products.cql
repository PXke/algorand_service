USE algorand_platform;

-- Owner decision 2026-09-10: the x402 marketplace is cut down to the
-- products that do real work for a paying agent (catalog, news, scan,
-- storage). Every table below belonged to a removed product -- the endpoint
-- directory (090/096/099/104/113/114), the probe beat (097/118), the
-- visibility board (091/120), the feature-request board (092/119), endpoint
-- grading (093/103), signed fulfillment receipts (109), the on-demand uptime
-- check history (117), Know Your Agent enrollment (KYA, 089), and the agent
-- social network (105/106/108/110/116/122). Their modules, statements,
-- settings and routes are deleted in the same change, so nothing reads or
-- writes these tables any more.
--
-- Prod data in every one of them was verified to be test data only before
-- this drop: 26 social agents all named ModeratorAgent*/Voter*/*TestAgent,
-- 3 feature requests of which 2 are our own probes, 1 directory listing
-- which is our own search route, 0 board placements, 0.373 USDC lifetime
-- merchant volume all from our own probe wallet.
--
-- KEPT, deliberately, and never touched here: x402_settlements and
-- x402_settlements_by_tx (the bookkeeping ledger, CLAUDE.md section 9),
-- x402_promo_codes, x402_promo_redemptions_v2, and the four x402_storage_*
-- tables.

-- Endpoint directory.
DROP TABLE IF EXISTS x402_listings;
DROP TABLE IF EXISTS x402_listings_by_recency;
DROP TABLE IF EXISTS x402_listings_by_tag;
DROP TABLE IF EXISTS x402_listings_auto_discovered_by_recency;

-- Visibility board.
DROP TABLE IF EXISTS x402_board_entries;
DROP TABLE IF EXISTS x402_board_by_recency;
DROP TABLE IF EXISTS x402_board_clicks;
DROP TABLE IF EXISTS x402_board_by_category;
DROP TABLE IF EXISTS x402_board_click_events;

-- Feature-request board.
DROP TABLE IF EXISTS x402_feature_requests;
DROP TABLE IF EXISTS x402_feature_requests_by_recency;
DROP TABLE IF EXISTS x402_feature_vote_totals;
DROP TABLE IF EXISTS x402_feature_votes;
DROP TABLE IF EXISTS x402_feature_claims;

-- Endpoint grading.
DROP TABLE IF EXISTS x402_grades;
DROP TABLE IF EXISTS x402_graded_endpoints;

-- Scheduled probe of listed endpoints (workers beat, also removed).
DROP TABLE IF EXISTS x402_probe_results;
DROP TABLE IF EXISTS x402_probe_latest;
DROP TABLE IF EXISTS x402_probe_queue;

-- Signed fulfillment receipts.
DROP TABLE IF EXISTS x402_fulfillment_receipts;

-- On-demand uptime check history.
DROP TABLE IF EXISTS x402_uptime_checks_by_url;

-- Know Your Agent (KYA) enrollment and paid lookups.
DROP TABLE IF EXISTS kyc_enrollments;
DROP TABLE IF EXISTS kyc_lookup_events;

-- Agent social network (identity, posts, graph, groups, moderation, DMs).
DROP TABLE IF EXISTS x402_social_agents;
DROP TABLE IF EXISTS x402_social_agents_by_interest;
DROP TABLE IF EXISTS x402_social_agents_by_recency;
DROP TABLE IF EXISTS x402_social_cases;
DROP TABLE IF EXISTS x402_social_case_votes;
DROP TABLE IF EXISTS x402_social_case_vote_totals;
DROP TABLE IF EXISTS x402_social_comments;
DROP TABLE IF EXISTS x402_social_dm_conversations;
DROP TABLE IF EXISTS x402_social_dm_messages;
DROP TABLE IF EXISTS x402_social_followers;
DROP TABLE IF EXISTS x402_social_follows;
DROP TABLE IF EXISTS x402_social_group_feed;
DROP TABLE IF EXISTS x402_social_group_members;
DROP TABLE IF EXISTS x402_social_group_names;
DROP TABLE IF EXISTS x402_social_groups;
DROP TABLE IF EXISTS x402_social_groups_by_recency;
DROP TABLE IF EXISTS x402_social_groups_by_tag;
DROP TABLE IF EXISTS x402_social_memberships;
DROP TABLE IF EXISTS x402_social_open_case_by_target;
DROP TABLE IF EXISTS x402_social_open_cases;
DROP TABLE IF EXISTS x402_social_posts;
DROP TABLE IF EXISTS x402_social_posts_by_author;
DROP TABLE IF EXISTS x402_social_reaction_log;
DROP TABLE IF EXISTS x402_social_reaction_totals;
DROP TABLE IF EXISTS x402_social_removals;
DROP TABLE IF EXISTS x402_social_reporter_slots;
DROP TABLE IF EXISTS x402_social_standing;
