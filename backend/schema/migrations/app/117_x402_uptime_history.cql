USE algorand_platform;

-- x402 uptime-check history (2026-09-06, agent feedback via Moltbook: "show
-- me uptime over the last 30 days, not just the latest probe"). Distinct
-- from x402_probe_results (097, the DIRECTORY's own scheduled, unpaid probe
-- fleet over LISTED endpoints) -- this table is written by the PAID,
-- on-demand POST /api/v1/x402/uptime/check route (app/modules/x402_uptime/)
-- over an arbitrary CALLER-SUPPLIED url, never a listed one, and only on a
-- genuine cache-miss real check. A served cache hit, or a budget-exhausted
-- stale-cache serve, is a re-serving of an OLD measurement, not a new one --
-- see app/modules/x402_uptime/models/domain.py's StoredUptimeCheck
-- docstring for why recording those too would duplicate and skew a 30-day
-- uptime percentage.
--
-- url_hash reuses app.modules.x402_directory.services.listing_service.
-- url_hash's own sha256 hex of the normalized url rather than reimplementing
-- it -- the same fixed-length partition-key hash this codebase already uses
-- for "a normalized url as a Cassandra key," even though this table has
-- nothing to do with directory listings.
--
-- 30-day table TTL, matching x402_probe_results (097)'s own retention.
-- default_time_to_live is baked in at CREATE time -- changing
-- settings.x402_uptime_history_retention_days-equivalent later would not
-- retroactively change this table's TTL, only the caller-facing `days=`
-- cap on a read (see settings.x402_uptime_history_max_days) -- an actual
-- retention change needs a new forward migration.
CREATE TABLE IF NOT EXISTS x402_uptime_checks_by_url (
    url_hash text,
    checked_at timestamp,
    url text,
    reachable boolean,
    http_status int,
    response_time_ms int,
    error text,
    PRIMARY KEY ((url_hash), checked_at)
) WITH CLUSTERING ORDER BY (checked_at DESC)
  AND default_time_to_live = 2592000;
