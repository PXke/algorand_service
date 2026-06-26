USE algorand_platform;

-- Link follow-up ingest signals to an existing article (edit-within-window).
CREATE TABLE IF NOT EXISTS article_match_keys (
  key_type text,
  key_value text,
  article_id uuid,
  linked_at timestamp,
  edit_window_closes_at timestamp,
  PRIMARY KEY ((key_type, key_value), article_id)
);

CREATE TABLE IF NOT EXISTS article_match_keys_by_article (
  article_id uuid,
  key_type text,
  key_value text,
  linked_at timestamp,
  PRIMARY KEY (article_id, key_type, key_value)
);

-- Version history for agent/human edits.
CREATE TABLE IF NOT EXISTS article_versions (
  article_id uuid,
  version int,
  title text,
  summary text,
  body text,
  edit_reason text,
  editor text,
  edited_at timestamp,
  PRIMARY KEY (article_id, version)
) WITH CLUSTERING ORDER BY (version DESC);

-- Admin "suggestion box" — ideas for the writer agent (not final copy).
CREATE TABLE IF NOT EXISTS editorial_briefs (
  brief_id uuid PRIMARY KEY,
  title text,
  body_markdown text,
  keywords text,
  status text,
  wallet_address text,
  created_at timestamp,
  updated_at timestamp
);
