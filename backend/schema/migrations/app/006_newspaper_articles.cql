USE algorand_platform;

CREATE TABLE IF NOT EXISTS articles_feed (
  bucket text,
  published_at timestamp,
  article_id uuid,
  service_id text,
  title text,
  summary text,
  PRIMARY KEY ((bucket), published_at, article_id)
) WITH CLUSTERING ORDER BY (published_at DESC);

CREATE TABLE IF NOT EXISTS articles_by_id (
  article_id uuid PRIMARY KEY,
  service_id text,
  title text,
  summary text,
  body text,
  trigger_txid text,
  trigger_round bigint,
  source_url text,
  published_at timestamp
);
