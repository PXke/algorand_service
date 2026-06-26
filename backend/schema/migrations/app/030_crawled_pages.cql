-- Persist crawled website pages so indexing flows Cassandra -> Typesense.
CREATE TABLE IF NOT EXISTS crawled_pages_by_id (
  page_id uuid PRIMARY KEY,
  url text,
  domain text,
  title text,
  description text,
  body text,
  service_id text,
  source text,
  keywords list<text>,
  classifier_score float,
  crawled_at timestamp
);

CREATE TABLE IF NOT EXISTS crawled_pages_by_domain (
  domain text,
  crawled_at timestamp,
  page_id uuid,
  url text,
  title text,
  description text,
  service_id text,
  source text,
  keywords list<text>,
  PRIMARY KEY ((domain), crawled_at, page_id)
) WITH CLUSTERING ORDER BY (crawled_at DESC, page_id ASC);
