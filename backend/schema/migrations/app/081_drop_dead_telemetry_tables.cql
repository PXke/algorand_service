USE algorand_platform;

-- Drop four confirmed-dead tables (schema audit 2026-08-26/27). All four have
-- ZERO live rows (verified via count(*) on prod before this migration was
-- written) and ZERO code references in backend, workers, shared, tools or any
-- test file -- checked for dynamic name construction too. Only remaining
-- mentions are their own CREATE migrations and a scratch backup manifest
-- (data snapshot, not code).
--
-- pageview_path_daily (migration 035): per-path daily pageview counters,
-- superseded by pageview_path_kind_daily (migration 037, which split the
-- counter by human|bot kind) -- nothing ever wrote the old table after that.
-- pageview_bot_daily (migration 039): per-identified-crawler daily counter
-- from the analytics expansion -- no code ever wrote or read it (the live
-- analytics flush uses pageview_path_kind_daily's kind column instead).
-- gatekeeper_decisions / gatekeeper_audit (migration 031): MTTH gatekeeper
-- telemetry streams A/B -- the gatekeeper's Celery wiring never landed, so
-- neither stream ever received a row. The gatekeeper model code itself
-- (corruptor/telemetry/drift modules) is untouched by this drop, it can
-- recreate its telemetry tables in a fresh migration if it ever ships.
-- NOTE: no semicolons in comments (the migrator splits on the first one).

DROP TABLE IF EXISTS algorand_platform.pageview_path_daily;

DROP TABLE IF EXISTS algorand_platform.pageview_bot_daily;

DROP TABLE IF EXISTS algorand_platform.gatekeeper_decisions;

DROP TABLE IF EXISTS algorand_platform.gatekeeper_audit;
