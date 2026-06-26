USE algorand_platform;

-- Durable per-article read tally. A Cassandra counter handles concurrent
-- increments. "Trending" is derived by scanning recent articles and reading
-- their counts (small audience, no ranking index needed).
CREATE TABLE IF NOT EXISTS article_view_counts (
  article_id uuid PRIMARY KEY,
  views counter
);
