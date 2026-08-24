USE algorand_platform;

-- Match-key-based crawl-follow-up edit routing removed 2026-08-24: real prod
-- data showed find_article_for_followup fired legitimately twice ever (both
-- over a month old at removal time) against 1,657 total publish candidates --
-- today's lower crawl volume and pervasive per-source cooldowns mean the
-- scenario it existed for essentially doesn't happen anymore. Explicit-id
-- edits (editorial-brief refreshes, admin recompose) never used these tables.
DROP TABLE IF EXISTS article_match_keys;
DROP TABLE IF EXISTS article_match_keys_by_article;
