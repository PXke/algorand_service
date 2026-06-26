USE algorand_platform;

-- Official source allowlist managed via admin API (not only env vars).
-- kind: discord | telegram | mail_domain
CREATE TABLE IF NOT EXISTS official_channels (
  kind text,
  channel_id text,
  label text,
  added_by text,
  created_at timestamp,
  PRIMARY KEY ((kind), channel_id)
);
