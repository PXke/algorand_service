"""Central registry of prepared CQL statements for the backend service.

Every fixed statement lives here as a named entry on a registry class grouped by
table / domain. Statements use `?` placeholders and are **prepared lazily on first
access** (no Cassandra session exists at import time) then cached for the process
lifetime via `prepare_cached`. At call sites do:

    from app.core.statements import NewsStmts
    row = session.execute(NewsStmts.GET_ARTICLE, (aid,)).one()

Identical CQL used from several stores should collapse to a single named entry.
Statements whose table or column identifiers are chosen at runtime (e.g. the
analytics per-table aggregates) cannot be a fixed named entry — those build the
CQL string dynamically and pass it through `prepare_cached(...)` directly, which
still prepares each distinct string once. TRUNCATE / DDL and
`SELECT now() FROM system.local` cannot be prepared and stay plain.
"""

from __future__ import annotations


class _Stmt:
    """Descriptor holding CQL; resolves to the (cached) PreparedStatement on access.

    Preparation is delegated to `app.core.cassandra.prepare_cached`, so there is a
    single process-wide cache keyed by CQL string."""

    def __init__(self, cql: str) -> None:
        self.cql = cql

    def __get__(self, obj, owner):
        from app.core.cassandra import prepare_cached

        return prepare_cached(self.cql)


# --------------------------------------------------------------------------- #
# articles_by_id (read-only here; written by the workers service)
# --------------------------------------------------------------------------- #
class ArticleStmts:
    GET_TAGS = _Stmt("SELECT tags FROM algorand_platform.articles_by_id WHERE article_id = ?")
    GET_CARD = _Stmt(
        "SELECT title, published_at, tags FROM algorand_platform.articles_by_id "
        "WHERE article_id = ?"
    )
    GET_PUBLISHED_AT = _Stmt(
        "SELECT published_at FROM algorand_platform.articles_by_id WHERE article_id = ?"
    )
    GET_FEED_ROW = _Stmt(
        "SELECT article_id, service_id, title, summary, published_at, tags, "
        "image_url, source_url FROM algorand_platform.articles_by_id WHERE article_id = ?"
    )
    GET_SUMMARY_CARD = _Stmt(
        "SELECT article_id, title, summary, service_id FROM algorand_platform.articles_by_id "
        "WHERE article_id = ?"
    )
    UPDATE_CONTENT = _Stmt(
        "UPDATE algorand_platform.articles_by_id SET title = ?, summary = ?, body = ?, tags = ? "
        "WHERE article_id = ?"
    )
    UPDATE_TAGS = _Stmt("UPDATE algorand_platform.articles_by_id SET tags = ? WHERE article_id = ?")
    DELETE = _Stmt("DELETE FROM algorand_platform.articles_by_id WHERE article_id = ?")


# --------------------------------------------------------------------------- #
# articles_feed (projection)
# --------------------------------------------------------------------------- #
class DeletedArticleStmts:
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.deleted_articles (article_id, deleted_at, title) "
        "VALUES (?, ?, ?)"
    )
    GET = _Stmt(
        "SELECT article_id FROM algorand_platform.deleted_articles WHERE article_id = ?"
    )


class FeedStmts:
    INSERT_FULL = _Stmt(
        "INSERT INTO algorand_platform.articles_feed ("
        "bucket, published_at, article_id, service_id, title, summary, tags, "
        "image_url, source_url"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    DELETE = _Stmt(
        "DELETE FROM algorand_platform.articles_feed "
        "WHERE bucket = ? AND published_at = ? AND article_id = ?"
    )
    COUNT_TODAY = _Stmt(
        "SELECT article_id FROM algorand_platform.articles_feed "
        "WHERE bucket = ? AND published_at >= ? AND published_at < ?"
    )


# --------------------------------------------------------------------------- #
# article_versions
# --------------------------------------------------------------------------- #
class ArticleVersionStmts:
    LATEST = _Stmt(
        "SELECT version FROM algorand_platform.article_versions "
        "WHERE article_id = ? ORDER BY version DESC LIMIT 1"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.article_versions ("
        "article_id, version, title, summary, body, edit_reason, editor, edited_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    LIST = _Stmt(
        "SELECT version, title, summary, edit_reason, editor, edited_at "
        "FROM algorand_platform.article_versions WHERE article_id = ? LIMIT ?"
    )
    LIST_VERSIONS = _Stmt(
        "SELECT version FROM algorand_platform.article_versions WHERE article_id = ?"
    )
    DELETE = _Stmt(
        "DELETE FROM algorand_platform.article_versions WHERE article_id = ? AND version = ?"
    )


# --------------------------------------------------------------------------- #
# article_match_keys / article_match_keys_by_article
# --------------------------------------------------------------------------- #
class ArticleMatchStmts:
    LIST_BY_ARTICLE = _Stmt(
        "SELECT key_type, key_value FROM algorand_platform.article_match_keys_by_article "
        "WHERE article_id = ?"
    )
    DELETE_KEY = _Stmt(
        "DELETE FROM algorand_platform.article_match_keys "
        "WHERE key_type = ? AND key_value = ? AND article_id = ?"
    )
    DELETE_KEY_BY_ARTICLE = _Stmt(
        "DELETE FROM algorand_platform.article_match_keys_by_article "
        "WHERE article_id = ? AND key_type = ? AND key_value = ?"
    )


# --------------------------------------------------------------------------- #
# editorial_briefs
# --------------------------------------------------------------------------- #
class EditorialBriefStmts:
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.editorial_briefs ("
        "brief_id, title, body_markdown, keywords, status, "
        "wallet_address, created_at, updated_at, refresh_every_days"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    LIST = _Stmt(
        "SELECT brief_id, title, keywords, status, wallet_address, created_at, updated_at, "
        "refresh_every_days, last_run_at, linked_article_id "
        "FROM algorand_platform.editorial_briefs LIMIT ?"
    )
    GET = _Stmt(
        "SELECT brief_id, title, body_markdown, keywords, status, "
        "wallet_address, created_at, updated_at, "
        "refresh_every_days, last_run_at, linked_article_id "
        "FROM algorand_platform.editorial_briefs WHERE brief_id = ?"
    )


# --------------------------------------------------------------------------- #
# official_channels
# --------------------------------------------------------------------------- #
class OfficialChannelStmts:
    LIST_BY_KIND = _Stmt(
        "SELECT kind, channel_id, label, added_by, created_at "
        "FROM algorand_platform.official_channels WHERE kind = ? LIMIT ?"
    )
    LIST_ALL = _Stmt(
        "SELECT kind, channel_id, label, added_by, created_at "
        "FROM algorand_platform.official_channels LIMIT ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.official_channels "
        "(kind, channel_id, label, added_by, created_at) "
        "VALUES (?, ?, ?, ?, ?)"
    )
    DELETE = _Stmt(
        "DELETE FROM algorand_platform.official_channels WHERE kind = ? AND channel_id = ?"
    )


# --------------------------------------------------------------------------- #
# classifier_feedback / classifier_feedback_by_time
# --------------------------------------------------------------------------- #
class ClassifierFeedbackStmts:
    GET_GRADE = _Stmt(
        "SELECT approved, metadata FROM algorand_platform.classifier_feedback WHERE feedback_id = ?"
    )
    LIST_BY_TIME = _Stmt(
        "SELECT feedback_id, approved FROM algorand_platform.classifier_feedback_by_time "
        "WHERE bucket = ? LIMIT 5000"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.classifier_feedback ("
        "feedback_id, url, text_sample, category, predicted_category, quality, "
        "predicted_publish, approved, admin_wallet, created_at, metadata"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_BY_TIME = _Stmt(
        "INSERT INTO algorand_platform.classifier_feedback_by_time ("
        "bucket, created_at, feedback_id, url, approved"
        ") VALUES (?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# gatekeeper_anchors / gatekeeper_validation_report
# --------------------------------------------------------------------------- #
class GatekeeperStmts:
    INSERT_ANCHOR = _Stmt(
        "INSERT INTO algorand_platform.gatekeeper_anchors ("
        "bucket, created_at, anchor_id, article_id, url, source_text, "
        "article_text, factuality_fail, tone_fail, error_types, admin_wallet"
        ") VALUES ('main', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    LIST_ANCHORS = _Stmt(
        "SELECT created_at, anchor_id, article_id, url, factuality_fail, "
        "tone_fail, error_types FROM algorand_platform.gatekeeper_anchors "
        "WHERE bucket = 'main' LIMIT ?"
    )
    GET_REPORT = _Stmt(
        "SELECT computed_at, report_json, n_anchors, trusted_count "
        "FROM algorand_platform.gatekeeper_validation_report WHERE bucket = 'main' LIMIT 1"
    )


# --------------------------------------------------------------------------- #
# domain_tracking
# --------------------------------------------------------------------------- #
class DomainTrackingStmts:
    GET_FOR_CORRECTION = _Stmt(
        "SELECT domain, last_crawled_at, last_online_at, relevance_score, "
        "category, is_relevant, metadata FROM algorand_platform.domain_tracking WHERE domain = ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.domain_tracking ("
        "domain, last_crawled_at, last_online_at, relevance_score, "
        "category, is_relevant, metadata, frontier_status"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    LIST_ALL = _Stmt(
        "SELECT domain, last_crawled_at, last_online_at, relevance_score, "
        "category, is_relevant, metadata, frontier_status FROM algorand_platform.domain_tracking"
    )
    DELETE = _Stmt("DELETE FROM algorand_platform.domain_tracking WHERE domain = ?")
    LIST_BY_STATUS = _Stmt(
        "SELECT domain, last_crawled_at, relevance_score, category, "
        "is_relevant, metadata, frontier_status "
        "FROM algorand_platform.domain_tracking WHERE frontier_status = ? LIMIT 500"
    )
    LIST_BRIEF = _Stmt(
        "SELECT domain, last_crawled_at, relevance_score, category, "
        "is_relevant, metadata, frontier_status FROM algorand_platform.domain_tracking LIMIT 500"
    )


# --------------------------------------------------------------------------- #
# crawled_pages_by_domain (read-only here)
# --------------------------------------------------------------------------- #
class CrawledPageStmts:
    COUNT_BY_DOMAIN = _Stmt(
        "SELECT COUNT(*) AS c FROM algorand_platform.crawled_pages_by_domain WHERE domain = ?"
    )


# --------------------------------------------------------------------------- #
# tool_suggestions / compose_sessions (writer introspection, read-only here)
# --------------------------------------------------------------------------- #
class ToolInsightStmts:
    LIST_SUGGESTIONS = _Stmt(
        "SELECT created_at, capability, reason, service_id, source_url, model "
        "FROM algorand_platform.tool_suggestions WHERE bucket = ? LIMIT 300"
    )
    LIST_COMPOSE_SESSIONS = _Stmt(
        "SELECT created_at, session_id, service_id, source_url, model, status, "
        "rounds, tool_calls, duration_ms, messages, final_output "
        "FROM algorand_platform.compose_sessions WHERE bucket = ? LIMIT 20"
    )
    # Summary-only variant for the polled list view — skips messages/final_output,
    # which can be up to ~140KB per row (see tool_insights_store.record_compose_session).
    LIST_COMPOSE_SESSIONS_SUMMARY = _Stmt(
        "SELECT created_at, session_id, service_id, source_url, model, status, "
        "rounds, tool_calls, duration_ms "
        "FROM algorand_platform.compose_sessions WHERE bucket = ? LIMIT 20"
    )
    GET_COMPOSE_SESSION_DETAIL = _Stmt(
        "SELECT messages, final_output FROM algorand_platform.compose_sessions "
        "WHERE bucket = ? AND created_at = ? AND session_id = ?"
    )


# --------------------------------------------------------------------------- #
# url_queue / url_queue_by_url / url_queue_pending (admin frontier-approval seed)
# --------------------------------------------------------------------------- #
class UrlQueueStmts:
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.url_queue ("
        "queue_id, url, source, priority, enqueued_at, status, metadata"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_BY_URL = _Stmt(
        "INSERT INTO algorand_platform.url_queue_by_url (url, queue_id, enqueued_at, status) "
        "VALUES (?, ?, ?, ?)"
    )
    INSERT_PENDING = _Stmt(
        "INSERT INTO algorand_platform.url_queue_pending ("
        "status, priority, enqueued_at, queue_id, url, source"
        ") VALUES (?, ?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# investigation_findings (evidence trail, read-only here)
# --------------------------------------------------------------------------- #
class InvestigationStmts:
    LIST = _Stmt(
        "SELECT created_at, tool, arguments, result_json "
        "FROM algorand_platform.investigation_findings WHERE service_id = ? LIMIT 50"
    )


# --------------------------------------------------------------------------- #
# classifier_review_queue / classifier_review_pending
# --------------------------------------------------------------------------- #
class ClassifierReviewStmts:
    GET_METADATA = _Stmt(
        "SELECT metadata FROM algorand_platform.classifier_review_queue WHERE review_id = ?"
    )
    GET_FULL = _Stmt(
        "SELECT review_id, url, page_text, page_title, category, storage_score, "
        "created_at, metadata FROM algorand_platform.classifier_review_queue WHERE review_id = ?"
    )
    GET_DETAIL = _Stmt(
        "SELECT review_id, url, page_title, page_text, category, storage_score, metadata "
        "FROM algorand_platform.classifier_review_queue WHERE review_id = ?"
    )
    INSERT_QUEUE = _Stmt(
        "INSERT INTO algorand_platform.classifier_review_queue ("
        "review_id, url, page_text, page_title, category, "
        "storage_score, status, created_at, metadata"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    LIST_PENDING = _Stmt(
        "SELECT review_id, url, category, created_at "
        "FROM algorand_platform.classifier_review_pending WHERE status = ? LIMIT ?"
    )
    DELETE_PENDING = _Stmt(
        "DELETE FROM algorand_platform.classifier_review_pending "
        "WHERE status = ? AND created_at = ? AND review_id = ?"
    )


# --------------------------------------------------------------------------- #
# pending_feed_queue
# --------------------------------------------------------------------------- #
class PendingFeedStmts:
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.pending_feed_queue "
        "(bucket, interest_score, approved_at, article_id) "
        "VALUES (?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# News article store (articles_by_id / articles_feed) — the read/write path the
# public feed uses.
# --------------------------------------------------------------------------- #
class NewsStmts:
    INSERT_BY_ID = _Stmt(
        "INSERT INTO algorand_platform.articles_by_id ("
        "article_id, service_id, title, summary, body, "
        "trigger_txid, trigger_round, source_url, published_at, tags, image_url"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_FEED = _Stmt(
        "INSERT INTO algorand_platform.articles_feed ("
        "bucket, published_at, article_id, service_id, title, summary, tags, image_url"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    FEED_PAGE = _Stmt(
        "SELECT article_id, service_id, title, summary, published_at, tags, "
        "image_url, source_url FROM algorand_platform.articles_feed "
        "WHERE bucket = ? AND published_at < ? LIMIT ?"
    )
    GET_FULL = _Stmt(
        "SELECT article_id, service_id, title, summary, body, "
        "trigger_txid, trigger_round, source_url, published_at, tags, image_url "
        "FROM algorand_platform.articles_by_id WHERE article_id = ?"
    )


# --------------------------------------------------------------------------- #
# article_view_counts (counter table)
# --------------------------------------------------------------------------- #
class ViewCountStmts:
    BUMP = _Stmt(
        "UPDATE algorand_platform.article_view_counts SET views = views + 1 WHERE article_id = ?"
    )
    GET = _Stmt("SELECT views FROM algorand_platform.article_view_counts WHERE article_id = ?")


# --------------------------------------------------------------------------- #
# Chain tables written by the Conduit exporter (read-only here)
# --------------------------------------------------------------------------- #
class ChainStmts:
    GET_TXN = _Stmt(
        "SELECT txid, round, intra, sender, txn_type, txn_json, receiver, amount_microalgos "
        "FROM algorand_platform.transactions_by_id WHERE txid = ?"
    )
    CONDUIT_HEAD = _Stmt("SELECT value FROM algorand_platform.conduit_meta WHERE id = ?")
    TXNS_BY_ROUND = _Stmt(
        "SELECT txid, round, intra, sender, txn_type, txn_json, receiver, amount_microalgos "
        "FROM algorand_platform.transactions_by_round WHERE round = ?"
    )


# --------------------------------------------------------------------------- #
# suggestions_by_status / upvotes_by_suggestion
# --------------------------------------------------------------------------- #
class SuggestionStmts:
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.suggestions_by_status ("
        "status, created_at, suggestion_id, wallet_address, title, body, submission_txid"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    LIST_OPEN = _Stmt(
        "SELECT suggestion_id, wallet_address, title, body, submission_txid, created_at "
        "FROM algorand_platform.suggestions_by_status WHERE status = ? LIMIT 200"
    )
    GET = _Stmt(
        "SELECT suggestion_id, wallet_address, title, body, submission_txid, created_at, status "
        "FROM algorand_platform.suggestions_by_status "
        "WHERE status = ? AND suggestion_id = ? ALLOW FILTERING"
    )
    HAS_TXID = _Stmt(
        "SELECT suggestion_id FROM algorand_platform.suggestions_by_status "
        "WHERE submission_txid = ? LIMIT 1 ALLOW FILTERING"
    )


# --------------------------------------------------------------------------- #
# contact_messages (public /contact form → admin inbox)
# --------------------------------------------------------------------------- #
class ContactStmts:
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.contact_messages ("
        "bucket, created_at, message_id, name, email, message"
        ") VALUES (?, ?, ?, ?, ?, ?)"
    )
    LIST_BUCKET = _Stmt(
        "SELECT created_at, message_id, name, email, message "
        "FROM algorand_platform.contact_messages WHERE bucket = ? LIMIT 200"
    )


class UpvoteStmts:
    GET = _Stmt(
        "SELECT wallet_address FROM algorand_platform.upvotes_by_suggestion "
        "WHERE suggestion_id = ? AND wallet_address = ?"
    )
    INSERT = _Stmt(
        "INSERT INTO "
        "algorand_platform.upvotes_by_suggestion (suggestion_id, wallet_address, created_at) "
        "VALUES (?, ?, ?)"
    )
    COUNT = _Stmt(
        "SELECT COUNT(*) FROM algorand_platform.upvotes_by_suggestion WHERE suggestion_id = ?"
    )


# --------------------------------------------------------------------------- #
# service_registry
# --------------------------------------------------------------------------- #
class ServiceRegistryStmts:
    LIST_ALL = _Stmt(
        "SELECT service_id, display_name, match_kind, match_value, scrape_url, enabled, origin "
        "FROM algorand_platform.service_registry"
    )
    UPSERT = _Stmt(
        "INSERT INTO algorand_platform.service_registry ("
        "service_id, display_name, match_kind, match_value, scrape_url, enabled, "
        "updated_at, origin"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    DELETE = _Stmt("DELETE FROM algorand_platform.service_registry WHERE service_id = ?")
    GET_ID = _Stmt("SELECT service_id FROM algorand_platform.service_registry WHERE service_id = ?")
    SET_ENABLED = _Stmt(
        "UPDATE algorand_platform.service_registry SET enabled = ?, updated_at = ? "
        "WHERE service_id = ?"
    )
    GET_SCRAPE_URL = _Stmt(
        "SELECT scrape_url FROM algorand_platform.service_registry WHERE service_id = ?"
    )


# --------------------------------------------------------------------------- #
# service_sources / service_by_domain (service layer — one service, N sources)
# Keep in sync with the workers' ServiceSourceStmts.
# --------------------------------------------------------------------------- #
class ServiceSourceStmts:
    UPSERT = _Stmt(
        "INSERT INTO algorand_platform.service_sources ("
        "service_id, source_id, source_type, url, domain, enabled, added_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    LIST_FOR_SERVICE = _Stmt(
        "SELECT source_id, source_type, url, domain, enabled "
        "FROM algorand_platform.service_sources WHERE service_id = ?"
    )
    DELETE_FOR_SERVICE = _Stmt(
        "DELETE FROM algorand_platform.service_sources WHERE service_id = ?"
    )
    UPSERT_BY_DOMAIN = _Stmt(
        "INSERT INTO algorand_platform.service_by_domain (domain, service_id) VALUES (?, ?)"
    )
    GET_BY_DOMAIN = _Stmt(
        "SELECT service_id FROM algorand_platform.service_by_domain WHERE domain = ?"
    )


# --------------------------------------------------------------------------- #
# feed_placements / feed_placements_by_slot
# --------------------------------------------------------------------------- #
class PlacementStmts:
    LIST_BY_SLOT = _Stmt(
        "SELECT placement_id, slot, sponsor_name, headline, body, "
        "image_url, target_url, priority, enabled, active_from, active_until "
        "FROM algorand_platform.feed_placements_by_slot WHERE slot = ? LIMIT ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.feed_placements ("
        "placement_id, slot, sponsor_name, headline, body, image_url, target_url, "
        "priority, enabled, active_from, active_until, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_BY_SLOT = _Stmt(
        "INSERT INTO algorand_platform.feed_placements_by_slot ("
        "slot, priority, placement_id, sponsor_name, headline, body, "
        "image_url, target_url, enabled, active_from, active_until"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# price_metrics_brief / price_metric_samples (read-only here)
# --------------------------------------------------------------------------- #
class PriceMetricsStmts:
    GET_BRIEF = _Stmt(
        "SELECT asset_id, asset_name, currency, current_price_usd, change_24h_pct, "
        "sample_count_24h, prepared_at FROM algorand_platform.price_metrics_brief "
        "WHERE asset_id = ?"
    )
    SAMPLE_MARKET_CAP = _Stmt(
        "SELECT market_cap_usd FROM algorand_platform.price_metric_samples "
        "WHERE asset_id = ? LIMIT 1"
    )
    LATEST_SAMPLES = _Stmt(
        "SELECT market_cap_usd, volume_24h_usd, collected_at "
        "FROM algorand_platform.price_metric_samples WHERE asset_id = ? LIMIT 20"
    )


# --------------------------------------------------------------------------- #
# First-party pageview analytics (counters + samples). Every statement is fully
# defined here — the per-day aggregate reads are NOT built from runtime fragments.
# --------------------------------------------------------------------------- #
class AnalyticsStmts:
    # -- write path (fire-and-forget counter bumps) --
    SESSION_BUMP = _Stmt(
        "UPDATE algorand_platform.session_daily SET sessions = sessions + 1 "
        "WHERE day = ? AND vtype = ?"
    )
    DIRECT_UACLASS_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_direct_uaclass_daily SET views = views + 1 "
        "WHERE day = ? AND ua_class = ?"
    )
    DIRECT_SAMPLE_INSERT = _Stmt(
        "INSERT INTO algorand_platform.pageview_direct_sample "
        "(day, ts, path, referer, user_agent, ua_class) "
        "VALUES (?, now(), ?, ?, ?, ?)"
    )
    PAGEVIEW_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_daily SET views = views + 1 WHERE kind = ? AND day = ?"
    )
    PATH_KIND_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_path_kind_daily SET views = views + 1 "
        "WHERE day = ? AND path = ? AND kind = ?"
    )
    BOT_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_bot_daily SET views = views + 1 "
        "WHERE day = ? AND bot = ?"
    )
    GEO_BUMP = _Stmt(
        "UPDATE algorand_platform.geo_country_daily SET views = views + 1 "
        "WHERE day = ? AND country = ?"
    )
    CAMPAIGN_BUMP = _Stmt(
        "UPDATE algorand_platform.campaign_daily SET views = views + 1 "
        "WHERE day = ? AND campaign = ?"
    )
    DEVICE_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_device_daily SET views = views + 1 "
        "WHERE day = ? AND device = ?"
    )
    BROWSER_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_browser_daily SET views = views + 1 "
        "WHERE day = ? AND browser = ?"
    )
    HOUR_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_hour_daily SET views = views + 1 "
        "WHERE day = ? AND hour = ?"
    )
    REFERRER_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_referrer_daily SET views = views + 1 "
        "WHERE day = ? AND referrer = ?"
    )
    REFERRER_PATH_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_referrer_path_daily SET views = views + 1 "
        "WHERE day = ? AND referrer = ? AND path = ?"
    )
    REFERRER_URL_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_referrer_url_daily SET views = views + 1 "
        "WHERE day = ? AND referrer_url = ?"
    )
    SEARCH_BUMP = _Stmt(
        "UPDATE algorand_platform.search_query_daily SET searches = searches + 1 "
        "WHERE day = ? AND query = ?"
    )
    SEARCH_ZERO_BUMP = _Stmt(
        "UPDATE algorand_platform.search_zero_daily SET searches = searches + 1 "
        "WHERE day = ? AND query = ?"
    )
    NOTFOUND_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_notfound_daily SET views = views + 1 "
        "WHERE day = ? AND path = ?"
    )

    # -- read path (one partition per day; aggregated in-app over the window) --
    PAGEVIEW_SERIES_BY_KIND = _Stmt(
        "SELECT day, views FROM algorand_platform.pageview_daily WHERE kind = ?"
    )
    SESSION_BY_DAY = _Stmt(
        "SELECT vtype, sessions FROM algorand_platform.session_daily WHERE day = ?"
    )
    DIRECT_SAMPLE_BY_DAY = _Stmt(
        "SELECT path, referer, user_agent, ua_class FROM algorand_platform.pageview_direct_sample "
        "WHERE day = ? LIMIT ?"
    )
    REFERRER_PATH_BY_DAY = _Stmt(
        "SELECT referrer, path, views FROM algorand_platform.pageview_referrer_path_daily "
        "WHERE day = ?"
    )
    PATH_KIND_BY_DAY = _Stmt(
        "SELECT path, kind, views FROM algorand_platform.pageview_path_kind_daily WHERE day = ?"
    )
    HOUR_BY_DAY = _Stmt(
        "SELECT hour, views FROM algorand_platform.pageview_hour_daily WHERE day = ?"
    )
    # -- single-column-keyed aggregates (one fully-defined statement each) --
    AGG_REFERRER = _Stmt(
        "SELECT referrer, views FROM algorand_platform.pageview_referrer_daily WHERE day = ?"
    )
    AGG_DIRECT_UACLASS = _Stmt(
        "SELECT ua_class, views FROM algorand_platform.pageview_direct_uaclass_daily WHERE day = ?"
    )
    AGG_SEARCH = _Stmt(
        "SELECT query, searches FROM algorand_platform.search_query_daily WHERE day = ?"
    )
    AGG_SEARCH_ZERO = _Stmt(
        "SELECT query, searches FROM algorand_platform.search_zero_daily WHERE day = ?"
    )
    AGG_BOT = _Stmt("SELECT bot, views FROM algorand_platform.pageview_bot_daily WHERE day = ?")
    AGG_NOTFOUND = _Stmt(
        "SELECT path, views FROM algorand_platform.pageview_notfound_daily WHERE day = ?"
    )
    AGG_DEVICE = _Stmt(
        "SELECT device, views FROM algorand_platform.pageview_device_daily WHERE day = ?"
    )
    AGG_BROWSER = _Stmt(
        "SELECT browser, views FROM algorand_platform.pageview_browser_daily WHERE day = ?"
    )
    AGG_REFERRER_URL = _Stmt(
        "SELECT referrer_url, views FROM algorand_platform.pageview_referrer_url_daily "
        "WHERE day = ?"
    )
    AGG_GEO = _Stmt("SELECT country, views FROM algorand_platform.geo_country_daily WHERE day = ?")
    AGG_CAMPAIGN = _Stmt(
        "SELECT campaign, views FROM algorand_platform.campaign_daily WHERE day = ?"
    )
