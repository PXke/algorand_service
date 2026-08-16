-- One-day-ahead burst compose: instead of composing on-demand at publish
-- time, a daily selection step picks the human pick (Lane 1) + top
-- discovery (Lane 3) + top scale (Lane 2) candidates up front and marks
-- them here. A separate off-peak task then composes all of them in one
-- batch, so the (relatively expensive) DeepSeek calls land inside the
-- cheap off-peak window regardless of when today's publish slots actually
-- fire -- publishing itself keeps its existing paced cadence, fed from the
-- pre-composed, admin-reviewed batch instead of triggering fresh composes.
--
-- Sentinel is a UTC "YYYY-MM-DD" day string, same convention as
-- human_pick_day (060): a selection left over from an earlier day must be
-- distinguishable from today's, so it can't silently carry forward.
-- NULL/absent reads as "not part of any burst" for all existing rows.
ALTER TABLE publish_queue ADD burst_day text;

-- Carried through to the composed article's review-queue metadata (see
-- classifier_review_queue.metadata) and, for an approved article, needs to
-- be checkable again at PUBLISH time -- a burst-composed article must never
-- auto-publish same-day just because the daily cap/pacing happened to have
-- room, since the whole point is it starts going out tomorrow. Same
-- sentinel convention.
ALTER TABLE articles_by_id ADD burst_day text;
