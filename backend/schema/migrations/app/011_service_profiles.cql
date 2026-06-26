USE algorand_platform;

CREATE TABLE IF NOT EXISTS service_profiles (
  service_id text PRIMARY KEY,
  impressiveness_score int,
  text_chars int,
  reasons text,
  updated_at timestamp
);
