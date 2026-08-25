USE algorand_platform;

-- Several ingest lanes mint a synthetic service_id PER ITEM, never per
-- venue: forum_poll_tasks.py's "forum-topic:<id>" (one per hot thread),
-- xgov_watch.py's "xgov-proposal:<id>:<phase>" (one per proposal phase),
-- youtube_poll_tasks.py's "<channel>:<videoId>" (one per video), and
-- bluesky_poll_tasks.py's "<account>:<rkey>" (one per post). Because
-- article_matching.service_has_article / to_compose_selection._artifact_pool
-- match articles.service_id as a literal string, every item from these
-- lanes looks like a brand-new, never-covered service forever -- even when
-- the underlying venue (the forum, the xGov program, the channel, the
-- account) is well covered and has published articles under its own
-- service_id. venue_service_id is the STABLE service_id for the item's
-- underlying venue, set at insert time by the four lanes above (see
-- ingest_signal.ingest_publish_signal's venue_service_id kwarg) -- nullable,
-- most artifacts (a plain web crawl diff, a brief, a mail message) have no
-- venue distinct from their own service_id and leave this null.
-- _artifact_pool checks service_has_article(artifact.venue_service_id or
-- artifact.service_id) so these lanes correctly land in the UPDATE_POOL
-- once their venue has ever published, instead of permanently occupying the
-- guaranteed NEW_SERVICE_POOL floor.
ALTER TABLE artifacts ADD venue_service_id text;
ALTER TABLE artifacts_pending ADD venue_service_id text;
