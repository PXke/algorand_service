USE algorand_platform;

-- Turns editorial_briefs from a passive keyword-matched hint into an active
-- assignment: refresh_every_days sets an optional recurring cadence, last_run_at
-- and linked_article_id let the scheduler tell whether a brief still needs its
-- first article (linked_article_id empty) or is due for an in-place refresh.
ALTER TABLE editorial_briefs ADD refresh_every_days int;
ALTER TABLE editorial_briefs ADD last_run_at timestamp;
ALTER TABLE editorial_briefs ADD linked_article_id uuid;
