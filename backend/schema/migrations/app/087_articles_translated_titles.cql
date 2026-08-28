USE algorand_platform;

-- Feed/tag-listing reads (ArticlesStmts.LIST_PUBLISHED_PAGE and
-- ArticleTagIndexStmts.LIST_PAGE/LIST_RECENT) selected the FULL per-language
-- `translations` map -- title+summary+body per stored language -- even
-- though a listing page only ever overlays a locale's title+summary onto a
-- card (NewsService._to_feed_item) or enumerates language codes (the
-- multilingual sitemap, search_service's Typesense-down feed-scan
-- fallback). Feed rows are already body-less by design (see
-- 067_articles_consolidated.cql), so the translated body in that map was
-- never read on this path -- a 2026-08-28 performance audit found a 50-item
-- feed page was shipping up to 50 x len(ARTICLE_TRANSLATION_LANGS) full
-- translated article bodies out of Cassandra just to render 50 headlines
-- (several MB per request for something that should be near-instant).
--
-- translated_titles carries the same map<text, text> shape as translations
-- (lang -> JSON string) but each stored value is only {"title", "summary"}.
-- Written in the SAME UPDATE as translations everywhere a translation
-- lands (translate_article_task / translate_article_batch_task's _persist,
-- shared by DeepSeek- and locally-routed languages -- see
-- ArticlesStmts.UPDATE_TRANSLATIONS) and cleared in the same DELETE
-- everywhere translations is invalidated (draft content edits, recompose
-- re-publish -- new prose invalidates every existing translation, title
-- included -- see ArticlesStmts.CLEAR_TRANSLATIONS).
--
-- Existing published articles already have `translations` populated but
-- have no `translated_titles` until backfilled: see the one-off
-- workers/scratch/backfill_translated_titles.py (not run as part of this
-- migration -- a raw CQL statement can't derive+re-encode JSON per map
-- entry).
ALTER TABLE articles ADD translated_titles map<text, text>;
ALTER TABLE articles_by_tag ADD translated_titles map<text, text>;
