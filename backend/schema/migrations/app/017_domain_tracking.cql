USE algorand_platform;

CREATE TABLE IF NOT EXISTS domain_tracking (
  domain text PRIMARY KEY,
  last_crawled_at timestamp,
  last_online_at timestamp,
  relevance_score float,
  category text,
  is_relevant boolean,
  metadata map<text, text>
);
