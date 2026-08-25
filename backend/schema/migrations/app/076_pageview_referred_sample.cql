USE algorand_platform;

-- Extends the '(direct)'-bucket retroactive-purge mechanism (see
-- analytics_store._purge_direct_sample_ua) to '(internal)'- and external-
-- referred hits. Previously pageview_direct_sample was the ONLY raw
-- per-request log, so the moment a repeat-offender UA crossed
-- _UA_FREQ_THRESHOLD, only its earlier '(direct)' hits today could be clawed
-- back -- a UA farming hits via internal navigation or an external referrer
-- was caught going FORWARD once detected, but everything counted before
-- that point stayed permanently inflated with no raw log to unwind it from.
--
-- Deliberately a SEPARATE table rather than widening pageview_direct_sample
-- itself: the admin "recent direct requests" panel reads that table straight
-- through and labels every row as a direct hit (AnalyticsTab.svelte), so
-- mixing bucket kinds into it would mislabel admin diagnostics. `bucket`
-- carries the referrer_host() classification ('(internal)' or an external
-- host) so the retroactive-purge scan can decrement the right REFERRER_BUMP
-- counter per matched row. Same privacy posture as pageview_direct_sample:
-- no client IP is stored, 7-day TTL, newest-first clustering.
-- NOTE: no semicolons in comments (the migrator splits on the first one).
CREATE TABLE IF NOT EXISTS pageview_referred_sample (
  day text,
  ts timeuuid,
  path text,
  bucket text,
  referer text,
  user_agent text,
  ua_class text,
  PRIMARY KEY (day, ts)
) WITH CLUSTERING ORDER BY (ts DESC)
  AND default_time_to_live = 604800;
