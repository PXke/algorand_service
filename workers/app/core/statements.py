"""Central registry of prepared CQL statements for the workers service.

Every statement lives here as a named entry on a registry class grouped by table /
domain. Statements use `?` placeholders and are **prepared lazily on first access**
(no Cassandra session exists at import time) then cached for the process lifetime via
`prepare_cached`. At call sites do:

    from app.core.statements import ArticleStmts
    row = session.execute(ArticleStmts.GET_BY_ID, (aid,)).one()

Identical CQL used from several stores should collapse to a single named entry.
TRUNCATE / DDL and `SELECT now() FROM system.local` cannot be prepared and stay as
plain statements at their call sites.
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
# articles_by_id / articles_feed / service_events
# --------------------------------------------------------------------------- #
class ArticleStmts:
    GET_BY_ID = _Stmt(
        "SELECT article_id, service_id, title, summary, body, "
        "trigger_txid, trigger_round, source_url, published_at, prompt_version "
        "FROM algorand_platform.articles_by_id WHERE article_id = ?"
    )
    EXISTS = _Stmt("SELECT article_id FROM algorand_platform.articles_by_id WHERE article_id = ?")
    GET_TAGS = _Stmt("SELECT tags FROM algorand_platform.articles_by_id WHERE article_id = ?")
    GET_PUBLISHED_AT = _Stmt(
        "SELECT published_at FROM algorand_platform.articles_by_id "
        "WHERE article_id = ?"
    )
    GET_IMAGE_META = _Stmt(
        "SELECT service_id, source_url, image_url FROM algorand_platform.articles_by_id "
        "WHERE article_id = ?"
    )
    GET_IMAGE = _Stmt("SELECT image_url FROM algorand_platform.articles_by_id WHERE article_id = ?")
    GET_FOR_FEED = _Stmt(
        "SELECT article_id, service_id, title, summary, published_at, tags "
        "FROM algorand_platform.articles_by_id WHERE article_id = ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.articles_by_id ("
        "article_id, service_id, title, summary, body, "
        "trigger_txid, trigger_round, source_url, published_at, tags, image_url, "
        "prompt_version"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    UPDATE = _Stmt(
        "UPDATE algorand_platform.articles_by_id SET title = ?, summary = ?, body = ?, tags = ? "
        "WHERE article_id = ?"
    )
    UPDATE_IMAGE = _Stmt(
        "UPDATE algorand_platform.articles_by_id SET image_url = ? "
        "WHERE article_id = ?"
    )


class FeedStmts:
    BY_BUCKET = _Stmt(
        "SELECT article_id, service_id, title, summary, published_at "
        "FROM algorand_platform.articles_feed WHERE bucket = ? LIMIT ?"
    )
    BY_BUCKET_TAGS = _Stmt(
        "SELECT published_at, tags FROM algorand_platform.articles_feed WHERE bucket = ? LIMIT ?"
    )
    BY_BUCKET_RECENT = _Stmt(
        "SELECT article_id, title, tags, published_at FROM algorand_platform.articles_feed "
        "WHERE bucket = ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.articles_feed ("
        "bucket, published_at, article_id, service_id, title, summary, tags, "
        "image_url, source_url"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_BASIC = _Stmt(
        "INSERT INTO algorand_platform.articles_feed ("
        "bucket, published_at, article_id, service_id, title, summary, tags"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    UPDATE_IMAGE = _Stmt(
        "UPDATE algorand_platform.articles_feed SET image_url = ? "
        "WHERE bucket = ? AND published_at = ? AND article_id = ?"
    )
    SCAN_ALL = _Stmt(
        "SELECT bucket, published_at, article_id, "
        "service_id, title FROM algorand_platform.articles_feed"
    )
    DELETE = _Stmt(
        "DELETE FROM algorand_platform.articles_feed "
        "WHERE bucket = ? AND published_at = ? AND article_id = ?"
    )


class ServiceEventStmts:
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.service_events ("
        "service_id, occurred_at, event_id, txid, round, match_kind, match_value"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# publish_queue / publish_queue_pending / publish_queue_dedupe
# --------------------------------------------------------------------------- #
class PublishQueueStmts:
    DEDUPE_GET = _Stmt(
        "SELECT queue_id FROM algorand_platform.publish_queue_dedupe WHERE dedupe_key = ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.publish_queue ("
        "queue_id, status, priority, topic, publish_kind, "
        "service_id, display_name, scrape_url, dedupe_key, "
        "payload, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_PENDING = _Stmt(
        "INSERT INTO algorand_platform.publish_queue_pending ("
        "status, priority, created_at, queue_id, service_id, topic, publish_kind"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_DEDUPE = _Stmt(
        "INSERT INTO algorand_platform.publish_queue_dedupe (dedupe_key, queue_id, created_at) "
        "VALUES (?, ?, ?)"
    )
    LIST_PENDING = _Stmt(
        "SELECT queue_id, priority, topic, publish_kind, service_id, created_at "
        "FROM algorand_platform.publish_queue_pending WHERE status = ? LIMIT ?"
    )
    GET_DETAIL = _Stmt(
        "SELECT display_name, scrape_url, payload, created_at "
        "FROM algorand_platform.publish_queue WHERE queue_id = ?"
    )
    COUNT_PENDING = _Stmt(
        "SELECT COUNT(*) AS n FROM algorand_platform.publish_queue_pending WHERE status = ?"
    )
    GET_STATUS_ROW = _Stmt(
        "SELECT status, priority, created_at, dedupe_key "
        "FROM algorand_platform.publish_queue WHERE queue_id = ?"
    )
    UPDATE_STATUS = _Stmt(
        "UPDATE algorand_platform.publish_queue SET status = ?, updated_at = ? WHERE queue_id = ?"
    )
    DELETE_PENDING = _Stmt(
        "DELETE FROM algorand_platform.publish_queue_pending "
        "WHERE status = ? AND priority = ? AND created_at = ? AND queue_id = ?"
    )
    DELETE_DEDUPE = _Stmt(
        "DELETE FROM algorand_platform.publish_queue_dedupe WHERE dedupe_key = ?"
    )


# --------------------------------------------------------------------------- #
# url_queue / url_queue_by_url / url_queue_pending
# --------------------------------------------------------------------------- #
class UrlQueueStmts:
    BY_URL = _Stmt(
        "SELECT queue_id, status FROM algorand_platform.url_queue_by_url WHERE url = ?"
    )
    GET_STATUS = _Stmt("SELECT status FROM algorand_platform.url_queue WHERE queue_id = ?")
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.url_queue ("
        "queue_id, url, source, priority, enqueued_at, status, metadata"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_BY_URL = _Stmt(
        "INSERT INTO algorand_platform.url_queue_by_url (url, queue_id, enqueued_at) "
        "VALUES (?, ?, ?)"
    )
    INSERT_PENDING = _Stmt(
        "INSERT INTO algorand_platform.url_queue_pending ("
        "status, priority, enqueued_at, queue_id, url, source"
        ") VALUES (?, ?, ?, ?, ?, ?)"
    )
    PEEK_PENDING = _Stmt(
        "SELECT queue_id, url, source, priority, enqueued_at "
        "FROM algorand_platform.url_queue_pending WHERE status = ? LIMIT 1"
    )
    UPDATE_STATUS = _Stmt("UPDATE algorand_platform.url_queue SET status = ? WHERE queue_id = ?")
    DELETE_PENDING = _Stmt(
        "DELETE FROM algorand_platform.url_queue_pending "
        "WHERE status = ? AND priority = ? AND enqueued_at = ? AND queue_id = ?"
    )
    GET_METADATA = _Stmt("SELECT metadata FROM algorand_platform.url_queue WHERE queue_id = ?")
    LIST_PENDING_IDS = _Stmt(
        "SELECT queue_id FROM algorand_platform.url_queue_pending WHERE status = ? LIMIT 10000"
    )


# --------------------------------------------------------------------------- #
# classifier_review_queue / classifier_review_pending
# --------------------------------------------------------------------------- #
class ClassifierReviewStmts:
    INSERT_QUEUE = _Stmt(
        "INSERT INTO algorand_platform.classifier_review_queue ("
        "review_id, url, page_text, page_title, category, "
        "storage_score, status, created_at, metadata"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_PENDING = _Stmt(
        "INSERT INTO algorand_platform.classifier_review_pending ("
        "status, created_at, review_id, url, category"
        ") VALUES (?, ?, ?, ?, ?)"
    )
    COUNT_PENDING = _Stmt(
        "SELECT review_id FROM algorand_platform.classifier_review_pending WHERE status = ? LIMIT ?"
    )
    LIST_PENDING_URLS = _Stmt(
        "SELECT url FROM algorand_platform.classifier_review_pending WHERE status = ? LIMIT ?"
    )
    LIST_PENDING = _Stmt(
        "SELECT review_id, url, category, created_at "
        "FROM algorand_platform.classifier_review_pending WHERE status = ? LIMIT ?"
    )
    GET_DETAIL = _Stmt(
        "SELECT review_id, url, page_title, page_text, category, storage_score, metadata "
        "FROM algorand_platform.classifier_review_queue WHERE review_id = ?"
    )
    GET_FULL = _Stmt(
        "SELECT review_id, url, page_text, page_title, category, storage_score, "
        "status, created_at, metadata "
        "FROM algorand_platform.classifier_review_queue WHERE review_id = ?"
    )
    GET_FOR_PUBLISH = _Stmt(
        "SELECT url, page_text, page_title, category, storage_score, metadata "
        "FROM algorand_platform.classifier_review_queue WHERE review_id = ?"
    )
    DELETE_PENDING = _Stmt(
        "DELETE FROM algorand_platform.classifier_review_pending "
        "WHERE status = ? AND created_at = ? AND review_id = ?"
    )


# --------------------------------------------------------------------------- #
# domain_tracking
# --------------------------------------------------------------------------- #
class DomainTrackingStmts:
    GET_STATUS = _Stmt(
        "SELECT domain, last_crawled_at, last_online_at, relevance_score, "
        "category, is_relevant, metadata, frontier_status "
        "FROM algorand_platform.domain_tracking WHERE domain = ?"
    )
    GET_FOR_UPDATE = _Stmt(
        "SELECT last_online_at, category, is_relevant, metadata, frontier_status "
        "FROM algorand_platform.domain_tracking WHERE domain = ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.domain_tracking ("
        "domain, last_crawled_at, last_online_at, relevance_score, "
        "category, is_relevant, metadata, frontier_status"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    LIST = _Stmt(
        "SELECT domain, frontier_status, is_relevant, metadata "
        "FROM algorand_platform.domain_tracking LIMIT ?"
    )
    UPDATE_METADATA = _Stmt(
        "UPDATE algorand_platform.domain_tracking SET metadata = ? WHERE domain = ?"
    )
    GET_METADATA = _Stmt(
        "SELECT metadata FROM algorand_platform.domain_tracking WHERE domain = ?"
    )


# --------------------------------------------------------------------------- #
# crawled_pages_by_id / crawled_pages_by_domain
# --------------------------------------------------------------------------- #
class CrawledPageStmts:
    INSERT_BY_ID = _Stmt(
        "INSERT INTO algorand_platform.crawled_pages_by_id ("
        "page_id, url, domain, title, description, body, service_id, source, "
        "keywords, classifier_score, crawled_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_BY_DOMAIN = _Stmt(
        "INSERT INTO algorand_platform.crawled_pages_by_domain ("
        "domain, crawled_at, page_id, url, title, description, service_id, source, keywords"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    COUNT_BY_DOMAIN = _Stmt(
        "SELECT COUNT(*) AS c FROM algorand_platform.crawled_pages_by_domain WHERE domain = ?"
    )
    # Service-context aggregation: newest harvested pages per host (clustering
    # is crawled_at DESC), bodies fetched by id in a parallel second pass.
    LIST_BY_DOMAIN = _Stmt(
        "SELECT crawled_at, page_id, url, title "
        "FROM algorand_platform.crawled_pages_by_domain WHERE domain = ? LIMIT ?"
    )
    GET_BODY = _Stmt(
        "SELECT url, title, body, crawled_at "
        "FROM algorand_platform.crawled_pages_by_id WHERE page_id = ?"
    )


# --------------------------------------------------------------------------- #
# classifier_feedback / classifier_feedback_by_time
# --------------------------------------------------------------------------- #
class ClassifierFeedbackStmts:
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
    LIST_IDS = _Stmt(
        "SELECT feedback_id FROM algorand_platform.classifier_feedback_by_time "
        "WHERE bucket = ? LIMIT ?"
    )
    GET = _Stmt(
        "SELECT url, text_sample, category, predicted_category, quality, approved "
        "FROM algorand_platform.classifier_feedback WHERE feedback_id = ?"
    )
    GET_GRADE = _Stmt(
        "SELECT url, approved, metadata FROM algorand_platform.classifier_feedback "
        "WHERE feedback_id = ?"
    )


# --------------------------------------------------------------------------- #
# price_metric_samples / price_metrics_brief
# --------------------------------------------------------------------------- #
class PriceMetricsStmts:
    INSERT_SAMPLE = _Stmt(
        "INSERT INTO algorand_platform.price_metric_samples ("
        "asset_id, collected_at, price_usd, currency, "
        "change_24h_pct, market_cap_usd, volume_24h_usd, source"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?) USING TTL 31536000"
    )
    LIST_SAMPLES = _Stmt(
        "SELECT asset_id, collected_at, price_usd, currency, "
        "change_24h_pct, market_cap_usd, volume_24h_usd, source "
        "FROM algorand_platform.price_metric_samples "
        "WHERE asset_id = ? AND collected_at >= ? LIMIT ?"
    )
    INSERT_BRIEF = _Stmt(
        "INSERT INTO algorand_platform.price_metrics_brief ("
        "asset_id, prepared_at, asset_name, currency, current_price_usd, "
        "change_24h_pct, sample_count_24h, sample_count_7d, mistral_context"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    GET_BRIEF = _Stmt(
        "SELECT asset_id, prepared_at, asset_name, currency, current_price_usd, "
        "change_24h_pct, sample_count_24h, sample_count_7d, mistral_context "
        "FROM algorand_platform.price_metrics_brief WHERE asset_id = ?"
    )


# --------------------------------------------------------------------------- #
# page_snapshots / page_sources
# --------------------------------------------------------------------------- #
class SnapshotStmts:
    GET_LATEST = _Stmt(
        "SELECT content_hash, title, body FROM algorand_platform.page_snapshots "
        "WHERE source_id = ? LIMIT 1"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.page_snapshots "
        "(source_id, captured_at, content_hash, title, body) "
        "VALUES (?, ?, ?, ?, ?) USING TTL 3888000"
    )
    INSERT_SOURCE = _Stmt(
        "INSERT INTO algorand_platform.page_sources "
        "(source_id, service_id, url, enabled, updated_at) "
        "VALUES (?, ?, ?, ?, ?)"
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
        "SELECT version, title, summary, body, edit_reason, editor, edited_at "
        "FROM algorand_platform.article_versions WHERE article_id = ? LIMIT ?"
    )


# --------------------------------------------------------------------------- #
# article_match_keys / article_match_keys_by_article
# --------------------------------------------------------------------------- #
class ArticleMatchStmts:
    FIND_BY_KEY = _Stmt(
        "SELECT article_id, edit_window_closes_at FROM algorand_platform.article_match_keys "
        "WHERE key_type = ? AND key_value = ?"
    )
    INSERT_KEY = _Stmt(
        "INSERT INTO algorand_platform.article_match_keys ("
        "key_type, key_value, article_id, linked_at, edit_window_closes_at"
        ") VALUES (?, ?, ?, ?, ?)"
    )
    INSERT_KEY_BY_ARTICLE = _Stmt(
        "INSERT INTO algorand_platform.article_match_keys_by_article ("
        "article_id, key_type, key_value, linked_at"
        ") VALUES (?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# service_intelligence
# --------------------------------------------------------------------------- #
class IntelligenceStmts:
    GET = _Stmt(
        "SELECT primary_domain, intelligence_json, first_seen_at "
        "FROM algorand_platform.service_intelligence WHERE service_id = ?"
    )
    GET_FIRST_SEEN = _Stmt(
        "SELECT first_seen_at FROM algorand_platform.service_intelligence WHERE service_id = ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.service_intelligence ("
        "service_id, primary_domain, intelligence_json, first_seen_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# tool_suggestions / compose_sessions / tool_usage_stats
# --------------------------------------------------------------------------- #
class ToolInsightStmts:
    INSERT_SUGGESTION = _Stmt(
        "INSERT INTO algorand_platform.tool_suggestions ("
        "bucket, created_at, suggestion_id, capability, reason, "
        "service_id, source_url, model, resolved"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_COMPOSE_SESSION = _Stmt(
        "INSERT INTO algorand_platform.compose_sessions ("
        "bucket, created_at, session_id, service_id, source_url, model, "
        "status, rounds, tool_calls, duration_ms, messages, final_output"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    BUMP_USAGE = _Stmt(
        "UPDATE algorand_platform.tool_usage_stats SET calls = calls + ?, errors = errors + ? "
        "WHERE day = ? AND tool = ?"
    )


# --------------------------------------------------------------------------- #
# service_profiles
# --------------------------------------------------------------------------- #
class ServiceProfileStmts:
    GET_WEIGHT = _Stmt(
        "SELECT impressiveness_score FROM algorand_platform.service_profiles WHERE service_id = ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.service_profiles ("
        "service_id, impressiveness_score, text_chars, reasons, updated_at"
        ") VALUES (?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# investigation_findings
# --------------------------------------------------------------------------- #
class InvestigationStmts:
    LIST = _Stmt(
        "SELECT tool, arguments, result_json FROM algorand_platform.investigation_findings "
        "WHERE service_id = ? LIMIT ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.investigation_findings ("
        "service_id, created_at, finding_id, source_url, tool, arguments, result_json"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# pending_feed_queue
# --------------------------------------------------------------------------- #
class PendingFeedStmts:
    PEEK = _Stmt(
        "SELECT bucket, interest_score, approved_at, article_id "
        "FROM algorand_platform.pending_feed_queue WHERE bucket = ? LIMIT 1"
    )
    DELETE = _Stmt(
        "DELETE FROM algorand_platform.pending_feed_queue "
        "WHERE bucket = ? AND interest_score = ? AND approved_at = ? AND article_id = ?"
    )
    PEEK_ID = _Stmt(
        "SELECT article_id FROM algorand_platform.pending_feed_queue WHERE bucket = ? LIMIT 1"
    )


# --------------------------------------------------------------------------- #
# conduit_meta / transactions_by_round (chain tables written by Conduit)
# --------------------------------------------------------------------------- #
class ChainStmts:
    CONDUIT_HEAD = _Stmt("SELECT value FROM algorand_platform.conduit_meta WHERE id = ?")
    TXNS_BY_ROUND = _Stmt(
        "SELECT txid, round, sender, txn_type, txn_json, receiver, amount_microalgos "
        "FROM algorand_platform.transactions_by_round WHERE round = ?"
    )


# --------------------------------------------------------------------------- #
# article_view_counts (counter table; read-only here, IN-bind on partition key)
# --------------------------------------------------------------------------- #
class ViewCountStmts:
    GET_BULK = _Stmt(
        "SELECT article_id, views FROM algorand_platform.article_view_counts WHERE article_id IN ?"
    )


# --------------------------------------------------------------------------- #
# service_registry
# --------------------------------------------------------------------------- #
class ServiceRegistryStmts:
    LIST_ALL = _Stmt(
        "SELECT service_id, display_name, match_kind, match_value, scrape_url, enabled "
        "FROM algorand_platform.service_registry"
    )
    GET_ID = _Stmt(
        "SELECT service_id FROM algorand_platform.service_registry WHERE service_id = ?"
    )
    # Mirrors the backend admin store's UPSERT — keep the column lists in sync.
    UPSERT = _Stmt(
        "INSERT INTO algorand_platform.service_registry ("
        "service_id, display_name, match_kind, match_value, scrape_url, enabled, "
        "updated_at, origin"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# service_sources / service_by_domain (service layer — one service, N sources)
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
# gatekeeper_validation_report
# --------------------------------------------------------------------------- #
class GatekeeperStmts:
    INSERT_REPORT = _Stmt(
        "INSERT INTO algorand_platform.gatekeeper_validation_report "
        "(bucket, computed_at, report_json, n_anchors, trusted_count) "
        "VALUES ('main', ?, ?, ?, ?)"
    )
    LIST_ANCHORS = _Stmt(
        "SELECT anchor_id, article_id, url, source_text, article_text, "
        "factuality_fail, tone_fail, error_types FROM algorand_platform.gatekeeper_anchors "
        "WHERE bucket = 'main' LIMIT ?"
    )


# --------------------------------------------------------------------------- #
# editorial_briefs
# --------------------------------------------------------------------------- #
class EditorialBriefStmts:
    LIST = _Stmt(
        "SELECT brief_id, title, body_markdown, keywords, status, "
        "refresh_every_days, last_run_at, linked_article_id "
        "FROM algorand_platform.editorial_briefs LIMIT ?"
    )
    GET = _Stmt(
        "SELECT brief_id, title, body_markdown, keywords, status, "
        "refresh_every_days, last_run_at, linked_article_id "
        "FROM algorand_platform.editorial_briefs WHERE brief_id = ?"
    )
    UPDATE_LAST_RUN = _Stmt(
        "UPDATE algorand_platform.editorial_briefs SET last_run_at = ? WHERE brief_id = ?"
    )
    UPDATE_LINK = _Stmt(
        "UPDATE algorand_platform.editorial_briefs SET last_run_at = ?, linked_article_id = ? "
        "WHERE brief_id = ?"
    )


# --------------------------------------------------------------------------- #
# crawler_config
# --------------------------------------------------------------------------- #
class CrawlerConfigStmts:
    LIST_ALL = _Stmt(
        "SELECT crawler_type, display_name, "
        "description, enabled FROM algorand_platform.crawler_config"
    )


# --------------------------------------------------------------------------- #
# official_channels
# --------------------------------------------------------------------------- #
class OfficialChannelStmts:
    BY_KIND = _Stmt(
        "SELECT channel_id FROM algorand_platform.official_channels WHERE kind = ?"
    )
