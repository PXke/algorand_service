USE algorand_platform;

CREATE TABLE IF NOT EXISTS feed_placements (
  placement_id uuid PRIMARY KEY,
  slot text,
  sponsor_name text,
  headline text,
  body text,
  image_url text,
  target_url text,
  priority int,
  enabled boolean,
  active_from timestamp,
  active_until timestamp,
  created_at timestamp
);

CREATE TABLE IF NOT EXISTS feed_placements_by_slot (
  slot text,
  priority int,
  placement_id uuid,
  sponsor_name text,
  headline text,
  body text,
  image_url text,
  target_url text,
  enabled boolean,
  active_from timestamp,
  active_until timestamp,
  PRIMARY KEY ((slot), priority, placement_id)
) WITH CLUSTERING ORDER BY (priority DESC);
