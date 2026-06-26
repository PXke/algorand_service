USE algorand_platform;

-- Per-crawler enable flags (news ingest vs metrics vs chain).
CREATE TABLE IF NOT EXISTS crawler_config (
  crawler_type text PRIMARY KEY,
  display_name text,
  description text,
  enabled boolean,
  updated_at timestamp
);

INSERT INTO crawler_config (crawler_type, display_name, description, enabled, updated_at)
VALUES (
  'web',
  'Web (HTML + SPA)',
  'Static HTTPS pages and optional Playwright for SPAs (browser://, allowlisted hosts).',
  true,
  toTimestamp(now())
);

INSERT INTO crawler_config (crawler_type, display_name, description, enabled, updated_at)
VALUES (
  'reddit',
  'Reddit',
  'reddit:// subreddit polls via public JSON or OAuth API.',
  false,
  toTimestamp(now())
);

INSERT INTO crawler_config (crawler_type, display_name, description, enabled, updated_at)
VALUES (
  'telegram',
  'Telegram',
  'telegram:// and t.me/s public preview (bot mode optional).',
  false,
  toTimestamp(now())
);

INSERT INTO crawler_config (crawler_type, display_name, description, enabled, updated_at)
VALUES (
  'discord',
  'Discord',
  'discord:// bot API or web Playwright (official servers usually need push/extension).',
  false,
  toTimestamp(now())
);

INSERT INTO crawler_config (crawler_type, display_name, description, enabled, updated_at)
VALUES (
  'mail',
  'Email (IMAP)',
  'Foundation/partner mailbox polling.',
  true,
  toTimestamp(now())
);

INSERT INTO crawler_config (crawler_type, display_name, description, enabled, updated_at)
VALUES (
  'chain',
  'On-chain',
  'Conduit round tail → match registry → optional scrape_url publish.',
  true,
  toTimestamp(now())
);

INSERT INTO crawler_config (crawler_type, display_name, description, enabled, updated_at)
VALUES (
  'metrics',
  'Market metrics',
  'Price, TVL, node stats for charts (not editorial news body).',
  true,
  toTimestamp(now())
);
