USE algorand_platform;

CREATE TABLE IF NOT EXISTS classifier_feedback (
  feedback_id uuid PRIMARY KEY,
  url text,
  text_sample text,
  category text,
  predicted_publish boolean,
  approved boolean,
  admin_wallet text,
  created_at timestamp,
  metadata map<text, text>
);

CREATE TABLE IF NOT EXISTS classifier_feedback_by_time (
  bucket text,
  created_at timestamp,
  feedback_id uuid,
  url text,
  approved boolean,
  PRIMARY KEY ((bucket), created_at, feedback_id)
) WITH CLUSTERING ORDER BY (created_at DESC);

CREATE TABLE IF NOT EXISTS classifier_review_queue (
  review_id uuid PRIMARY KEY,
  url text,
  page_text text,
  page_title text,
  category text,
  storage_score float,
  status text,
  created_at timestamp,
  metadata map<text, text>
);

CREATE TABLE IF NOT EXISTS classifier_review_pending (
  status text,
  created_at timestamp,
  review_id uuid,
  url text,
  category text,
  PRIMARY KEY ((status), created_at, review_id)
) WITH CLUSTERING ORDER BY (created_at ASC);
