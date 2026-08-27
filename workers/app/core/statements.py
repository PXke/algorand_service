"""Central registry of prepared CQL statements for the workers service.

Every statement lives here as a named entry on a registry class grouped by table /
domain. Statements use `?` placeholders and are **prepared lazily on first access**
(no Cassandra session exists at import time) then cached for the process lifetime via
`prepare_cached`. At call sites do:

    from app.core.statements import ServiceRegistryStmts
    row = session.execute(ServiceRegistryStmts.GET_ID, (service_id,)).one()

Identical CQL used from several stores should collapse to a single named entry.
TRUNCATE / DDL and `SELECT now() FROM system.local` cannot be prepared and stay as
plain statements at their call sites.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from algorand_shared.article_statements import (
    ARTICLE_VERSION_INSERT,
    ARTICLE_VERSION_LATEST,
)
from algorand_shared.artifact_statements import ArtifactStmts as ArtifactStmts
from algorand_shared.artifact_statements import ToComposeStmts as ToComposeStmts
from algorand_shared.chain_statements import CHAIN_CONDUIT_HEAD, CHAIN_TXNS_BY_ROUND
from algorand_shared.platform_statements import (
    CLASSIFIER_FEEDBACK_INSERT_BY_TIME,
    DOMAIN_TRACKING_INSERT,
    DOMAIN_TRACKING_LIST,
    GLOSSARY_UPDATE_TRANSLATIONS,
    PAGE_SNAPSHOT_GET_LATEST,
    PAGE_SNAPSHOT_INSERT,
    SERVICE_SOURCE_DELETE_FOR_SERVICE,
    SERVICE_SOURCE_GET_BY_DOMAIN,
    SERVICE_SOURCE_LIST_FOR_SERVICE,
    SERVICE_SOURCE_UPSERT,
    SERVICE_SOURCE_UPSERT_BY_DOMAIN,
)

if TYPE_CHECKING:
    from cassandra.query import PreparedStatement


class _Stmt:
    """Descriptor holding CQL; resolves to the (cached) PreparedStatement on access.

    Preparation is delegated to `app.core.cassandra.prepare_cached`, so there is a
    single process-wide cache keyed by CQL string.
    """

    def __init__(self, cql: str) -> None:
        self.cql = cql

    def __get__(self, obj: object | None, owner: type | None) -> PreparedStatement:
        from app.core.cassandra import prepare_cached

        return prepare_cached(self.cql)


# --------------------------------------------------------------------------- #
# articles_by_slug / service_events
# --------------------------------------------------------------------------- #
class ArticleStmts:
    """Prepared statements for articles_by_slug, the reverse slug-uniqueness index.

    Article-table consolidation Phase 5: every read/write against
    articles_by_id/articles_feed proper has moved onto the consolidated
    `articles` table (see ArticlesStmts in algorand_shared.article_statements
    and transition_article_status in algorand_shared.article_transitions),
    and articles_by_id itself is dropped (migration 074). `articles_by_slug`
    is a separate reverse-index table, untouched by that migration, still the
    durable slug-uniqueness claim. The two former manual-tool translation-
    clear statements that used to live here (workers/scratch/
    backfill_stale_translations.py, fix_corrupted_pashto.py) targeted
    articles_by_id and are gone with it -- a future similar incident needs a
    new recipe against `articles`' own `translations` map column.
    """

    # Slug claim (migration 056) moved to
    # algorand_shared.article_statements.ArticlesStmts (2026-08-27), shared
    # with backend's review-approval publish path -- see
    # algorand_shared.slugs.ensure_article_slug.


class ServiceEventStmts:
    """Prepared statements for service-triggered events."""

    INSERT = _Stmt(
        "INSERT INTO algorand_platform.service_events ("
        "service_id, occurred_at, event_id, txid, round, match_kind, match_value"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# url_queue / url_queue_by_url / url_queue_pending
# --------------------------------------------------------------------------- #
class UrlQueueStmts:
    """Prepared statements for the crawl frontier URL queue."""

    BY_URL = _Stmt("SELECT queue_id, status FROM algorand_platform.url_queue_by_url WHERE url = ?")
    GET_STATUS = _Stmt("SELECT status FROM algorand_platform.url_queue WHERE queue_id = ?")
    # All writes bind a trailing/leading TTL param (URL_QUEUE_ROW_TTL_SECONDS)
    # so terminal rows can expire instead of accumulating forever. Binding 0 is
    # the documented CQL "no TTL" value — identical to the pre-TTL statements —
    # so one statement shape serves both the enabled and disabled config.
    # UPDATE_STATUS carries the same TTL: Cassandra TTLs are per-cell, so a
    # status update without one would leave a cell outliving the insert's
    # cells, surfacing a phantom queue_id+status row after the rest expires.
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.url_queue ("
        "queue_id, url, source, priority, enqueued_at, status, metadata"
        ") VALUES (?, ?, ?, ?, ?, ?, ?) USING TTL ?"
    )
    INSERT_BY_URL = _Stmt(
        "INSERT INTO algorand_platform.url_queue_by_url (url, queue_id, enqueued_at) "
        "VALUES (?, ?, ?) USING TTL ?"
    )
    INSERT_PENDING = _Stmt(
        "INSERT INTO algorand_platform.url_queue_pending ("
        "status, priority, enqueued_at, queue_id, url, source"
        ") VALUES (?, ?, ?, ?, ?, ?) USING TTL ?"
    )
    PEEK_PENDING = _Stmt(
        "SELECT queue_id, url, source, priority, enqueued_at "
        "FROM algorand_platform.url_queue_pending WHERE status = ? LIMIT 1"
    )
    # Wider slice for random pick — see dequeue_url(): always taking row 0 means
    # a large same-priority run (e.g. one backfill batch) drains in strict
    # insertion order, hammering one domain's URLs back-to-back before moving
    # on to the next domain.
    PEEK_PENDING_BATCH = _Stmt(
        "SELECT queue_id, url, source, priority, enqueued_at "
        "FROM algorand_platform.url_queue_pending WHERE status = ? LIMIT ?"
    )
    UPDATE_STATUS = _Stmt(
        "UPDATE algorand_platform.url_queue USING TTL ? SET status = ? WHERE queue_id = ?"
    )
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
    """Prepared statements for the pending classifier-review queue."""

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
    DELETE_PENDING = _Stmt(
        "DELETE FROM algorand_platform.classifier_review_pending "
        "WHERE status = ? AND created_at = ? AND review_id = ?"
    )


# --------------------------------------------------------------------------- #
# domain_tracking
# --------------------------------------------------------------------------- #
class DomainTrackingStmts:
    """Prepared statements for per-domain crawl/frontier tracking."""

    GET_STATUS = _Stmt(
        "SELECT domain, last_crawled_at, last_online_at, relevance_score, "
        "category, is_relevant, metadata, frontier_status "
        "FROM algorand_platform.domain_tracking WHERE domain = ?"
    )
    GET_FOR_UPDATE = _Stmt(
        "SELECT last_online_at, category, is_relevant, metadata, frontier_status, relevance_score "
        "FROM algorand_platform.domain_tracking WHERE domain = ?"
    )
    INSERT = DOMAIN_TRACKING_INSERT
    LIST = DOMAIN_TRACKING_LIST
    UPDATE_METADATA = _Stmt(
        "UPDATE algorand_platform.domain_tracking SET metadata = ? WHERE domain = ?"
    )
    GET_METADATA = _Stmt("SELECT metadata FROM algorand_platform.domain_tracking WHERE domain = ?")


# --------------------------------------------------------------------------- #
# crawled_pages_by_id / crawled_pages_by_domain
# --------------------------------------------------------------------------- #
class CrawledPageStmts:
    """Prepared statements for crawled_pages."""

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
    """Prepared statements for classifier training feedback."""

    INSERT = _Stmt(
        "INSERT INTO algorand_platform.classifier_feedback ("
        "feedback_id, url, text_sample, category, predicted_category, quality, "
        "predicted_publish, approved, admin_wallet, created_at, metadata"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_BY_TIME = CLASSIFIER_FEEDBACK_INSERT_BY_TIME
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
    """Prepared statements for price-metrics samples and briefs."""

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
    """Prepared statements for service-source content snapshots."""

    GET_LATEST = PAGE_SNAPSHOT_GET_LATEST
    INSERT = PAGE_SNAPSHOT_INSERT
    INSERT_SOURCE = _Stmt(
        "INSERT INTO algorand_platform.page_sources "
        "(source_id, service_id, url, enabled, updated_at) "
        "VALUES (?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# article_versions
# --------------------------------------------------------------------------- #
class ArticleVersionStmts:
    """Prepared statements for article edit-history versions."""

    LATEST = ARTICLE_VERSION_LATEST
    INSERT = ARTICLE_VERSION_INSERT
    LIST = _Stmt(
        "SELECT version, title, summary, body, edit_reason, editor, edited_at "
        "FROM algorand_platform.article_versions WHERE article_id = ? LIMIT ?"
    )


# --------------------------------------------------------------------------- #
# service_intelligence
# --------------------------------------------------------------------------- #
class IntelligenceStmts:
    """Prepared statements for a service's accumulated writer intelligence."""

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
# x_search_weekly (weekly X/Twitter sweep cache, read by the search_x tool)
# --------------------------------------------------------------------------- #
class XSearchWeeklyStmts:
    """Prepared statements for the weekly per-service X search cache.

    One row per service_id, overwritten by each sweep run (see x_search_store.py).
    """

    LIST_ALL = _Stmt(
        "SELECT service_id, display_name, query, posts_json, post_count, swept_at, error "
        "FROM algorand_platform.x_search_weekly"
    )
    GET = _Stmt(
        "SELECT service_id, display_name, query, posts_json, post_count, swept_at, error "
        "FROM algorand_platform.x_search_weekly WHERE service_id = ?"
    )
    UPSERT = _Stmt(
        "INSERT INTO algorand_platform.x_search_weekly ("
        "service_id, display_name, query, posts_json, post_count, swept_at, error"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# tool_suggestions / compose_sessions / tool_usage_stats
# --------------------------------------------------------------------------- #
class ToolInsightStmts:
    """Prepared statements for writer tool-usage insights."""

    INSERT_SUGGESTION = _Stmt(
        "INSERT INTO algorand_platform.tool_suggestions ("
        "bucket, created_at, suggestion_id, capability, reason, "
        "service_id, source_url, model, resolved"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_COMPOSE_FEEDBACK = _Stmt(
        "INSERT INTO algorand_platform.compose_feedback ("
        "bucket, created_at, feedback_id, category, severity, summary, detail, "
        "related_tool, service_id, source_url, model"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    INSERT_COMPOSE_SESSION = _Stmt(
        "INSERT INTO algorand_platform.compose_sessions ("
        "bucket, created_at, session_id, service_id, source_url, model, "
        "status, rounds, tool_calls, duration_ms, messages, final_output, "
        "prompt_tokens, completion_tokens, total_tokens, cached_tokens"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    BUMP_USAGE = _Stmt(
        "UPDATE algorand_platform.tool_usage_stats SET calls = calls + ?, errors = errors + ? "
        "WHERE day = ? AND tool = ?"
    )
    # Reaper + outcome finalizer share this scan: cheap enough to list without
    # paging — compose_sessions has a 7-day TTL, so the table never grows large.
    LIST_ALL_SUMMARY = _Stmt(
        "SELECT created_at, session_id, status, source_url FROM algorand_platform.compose_sessions "
        "WHERE bucket = ? LIMIT 1000"
    )
    MARK_STALE = _Stmt(
        "UPDATE algorand_platform.compose_sessions SET status = ? "
        "WHERE bucket = ? AND created_at = ? AND session_id = ?"
    )


# --------------------------------------------------------------------------- #
# translation_sessions
# --------------------------------------------------------------------------- #
class TranslationSessionStmts:
    """Prepared statements for per-language translation lifecycle tracking."""

    INSERT = _Stmt(
        "INSERT INTO algorand_platform.translation_sessions ("
        "bucket, started_at, session_id, article_id, lang, status, duration_ms, error"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    # Reaper shares this scan: cheap enough to list without paging --
    # translation_sessions has a 7-day TTL, so the table never grows large.
    LIST_ALL_SUMMARY = _Stmt(
        "SELECT started_at, session_id, status, article_id, lang "
        "FROM algorand_platform.translation_sessions WHERE bucket = ? LIMIT 1000"
    )
    MARK_DONE = _Stmt(
        "UPDATE algorand_platform.translation_sessions SET status = ?, duration_ms = ?, error = ? "
        "WHERE bucket = ? AND started_at = ? AND session_id = ?"
    )
    MARK_STALE = _Stmt(
        "UPDATE algorand_platform.translation_sessions SET status = ? "
        "WHERE bucket = ? AND started_at = ? AND session_id = ?"
    )


# --------------------------------------------------------------------------- #
# service_profiles
# --------------------------------------------------------------------------- #
class ServiceProfileStmts:
    """Prepared statements for a service's computed profile weight."""

    GET_WEIGHT = _Stmt(
        "SELECT impressiveness_score FROM algorand_platform.service_profiles WHERE service_id = ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.service_profiles ("
        "service_id, impressiveness_score, text_chars, reasons, updated_at"
        ") VALUES (?, ?, ?, ?, ?)"
    )
    # Kept separate from INSERT above: impressiveness updates on every
    # content-changed ingest, scale only every SERVICE_SCALE_REFRESH_DAYS --
    # a merged upsert would null out one column's value every time the other
    # writes.
    GET_SCALE = _Stmt(
        "SELECT scale_score, scale_updated_at FROM algorand_platform.service_profiles "
        "WHERE service_id = ?"
    )
    UPSERT_SCALE = _Stmt(
        "UPDATE algorand_platform.service_profiles "
        "SET scale_score = ?, scale_source = ?, scale_updated_at = ? WHERE service_id = ?"
    )


# --------------------------------------------------------------------------- #
# investigation_findings
# --------------------------------------------------------------------------- #
class InvestigationStmts:
    """Prepared statements for investigative-tool findings."""

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
# conduit_meta / transactions_by_round (chain tables written by Conduit)
# --------------------------------------------------------------------------- #
class ChainStmts:
    """Prepared statements for chain data indexed by Conduit."""

    CONDUIT_HEAD = CHAIN_CONDUIT_HEAD
    TXNS_BY_ROUND = CHAIN_TXNS_BY_ROUND



# --------------------------------------------------------------------------- #
# pageview analytics -- deferred (write-behind) dimensions
# --------------------------------------------------------------------------- #
class AnalyticsFlushStmts:
    """Variable-delta bumps for the six deferred pageview-analytics dimensions.

    backend defers these to a Redis pending buffer instead of writing
    directly (2026-08-25) -- see backend's app/modules/seo/analytics_store.py
    module note above _write_pageview_counters for why these six specifically
    (and not the others) are safe to apply on a delay. flush_pending_analytics
    (this module's counterpart to flush_pending_views) drains backend's
    "news:analytics:pending:*" Redis keys into these tables. Table names/PKs
    match backend's core/statements.py AnalyticsStmts GEO_BUMP/CAMPAIGN_BUMP/
    HOUR_BUMP/LANGUAGE_BUMP/REFERRER_PATH_BUMP/REFERRER_URL_BUMP exactly --
    this just applies a batched delta instead of a fixed +1.
    """

    GEO_BUMP = _Stmt(
        "UPDATE algorand_platform.geo_country_daily SET views = views + ? "
        "WHERE day = ? AND country = ?"
    )
    CAMPAIGN_BUMP = _Stmt(
        "UPDATE algorand_platform.campaign_daily SET views = views + ? "
        "WHERE day = ? AND campaign = ?"
    )
    HOUR_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_hour_daily SET views = views + ? "
        "WHERE day = ? AND hour = ?"
    )
    LANGUAGE_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_language_daily SET views = views + ? "
        "WHERE day = ? AND lang = ?"
    )
    REFERRER_PATH_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_referrer_path_daily SET views = views + ? "
        "WHERE day = ? AND referrer = ? AND path = ?"
    )
    REFERRER_URL_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_referrer_url_daily SET views = views + ? "
        "WHERE day = ? AND referrer_url = ?"
    )


# --------------------------------------------------------------------------- #
# service_registry
# --------------------------------------------------------------------------- #
class ServiceRegistryStmts:
    """Prepared statements for the service registry."""

    LIST_ALL = _Stmt(
        "SELECT service_id, display_name, match_kind, match_value, scrape_url, enabled, origin "
        "FROM algorand_platform.service_registry"
    )
    GET_ID = _Stmt("SELECT service_id FROM algorand_platform.service_registry WHERE service_id = ?")
    # Mirrors the backend admin store's UPSERT — keep the column lists in sync.
    UPSERT = _Stmt(
        "INSERT INTO algorand_platform.service_registry ("
        "service_id, display_name, match_kind, match_value, scrape_url, enabled, "
        "updated_at, origin"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    SET_ENABLED = _Stmt(
        "UPDATE algorand_platform.service_registry SET enabled = ?, updated_at = ? "
        "WHERE service_id = ?"
    )
    GET_SCRAPE_URL = _Stmt(
        "SELECT scrape_url FROM algorand_platform.service_registry WHERE service_id = ?"
    )


# --------------------------------------------------------------------------- #
# service_sources / service_by_domain (service layer — one service, N sources)
# --------------------------------------------------------------------------- #
class ServiceSourceStmts:
    """Prepared statements for a service's known web sources."""

    UPSERT = SERVICE_SOURCE_UPSERT
    LIST_FOR_SERVICE = SERVICE_SOURCE_LIST_FOR_SERVICE
    DELETE_FOR_SERVICE = SERVICE_SOURCE_DELETE_FOR_SERVICE
    UPSERT_BY_DOMAIN = SERVICE_SOURCE_UPSERT_BY_DOMAIN
    GET_BY_DOMAIN = SERVICE_SOURCE_GET_BY_DOMAIN


# --------------------------------------------------------------------------- #
# editorial_briefs
# --------------------------------------------------------------------------- #
class EditorialBriefStmts:
    """Prepared statements for editorial briefs."""

    LIST = _Stmt(
        "SELECT brief_id, title, body_markdown, keywords, status, "
        "refresh_every_days, last_run_at, linked_article_id, is_special_edition "
        "FROM algorand_platform.editorial_briefs LIMIT ?"
    )
    GET = _Stmt(
        "SELECT brief_id, title, body_markdown, keywords, status, "
        "refresh_every_days, last_run_at, linked_article_id, is_special_edition "
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
    """Prepared statements for crawler configuration rows."""

    LIST_ALL = _Stmt(
        "SELECT crawler_type, display_name, "
        "description, enabled FROM algorand_platform.crawler_config"
    )


# --------------------------------------------------------------------------- #
# official_channels
# --------------------------------------------------------------------------- #
class OfficialChannelStmts:
    """Prepared statements for the official-channels trust list."""

    BY_KIND = _Stmt("SELECT channel_id FROM algorand_platform.official_channels WHERE kind = ?")


# --------------------------------------------------------------------------- #
# glossary_terms (mostly read-only from this side -- entries are admin-curated
# via the backend; workers reads published terms for auto-linking, writes
# suggested drafts via INSERT_SUGGESTED, and fills in translations the same
# way it does for articles)
# --------------------------------------------------------------------------- #
class GlossaryStmts:
    """Prepared statements the workers side needs for the glossary."""

    LIST_ALL = _Stmt(
        "SELECT slug, term, definition, aliases, status "
        "FROM algorand_platform.glossary_terms"
    )
    GET = _Stmt("SELECT slug FROM algorand_platform.glossary_terms WHERE slug = ?")
    GET_FOR_TRANSLATE = _Stmt(
        "SELECT slug, term, definition, translations "
        "FROM algorand_platform.glossary_terms WHERE slug = ?"
    )
    INSERT_SUGGESTED = _Stmt(
        "INSERT INTO algorand_platform.glossary_terms ("
        "slug, term, definition, aliases, status, created_at, updated_at, created_by"
        ") VALUES (?, ?, ?, ?, 'draft', ?, ?, ?) IF NOT EXISTS"
    )
    UPDATE_TRANSLATIONS = GLOSSARY_UPDATE_TRANSLATIONS


# --------------------------------------------------------------------------- #
# artifacts / artifacts_pending / artifact_content / to_compose (2026-08-25
# editorial-room redesign, SHADOW MODE -- see artifact_store.py /
# artifact_priority.py / to_compose_selection.py. Not read by the live
# compose/publish path; publish_queue's own statements above are untouched.)
# --------------------------------------------------------------------------- #
# ArtifactStmts / ToComposeStmts moved to algorand_shared.artifact_statements
# (2026-08-26) alongside artifact_store.py / to_compose_selection.py, so
# backend's admin artifact/to-compose routes can read (and pin/reset can
# write) these tables directly instead of a Celery round-trip. Imported above
# and re-exported here so existing workers call sites
# (`from app.core.statements import ArtifactStmts`) keep working unchanged.
