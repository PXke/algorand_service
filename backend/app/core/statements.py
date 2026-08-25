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

from typing import TYPE_CHECKING

from algorand_shared.article_statements import (
    ARTICLE_VERSION_INSERT,
    ARTICLE_VERSION_LATEST,
    PUBLISH_QUEUE_CLEAR_HUMAN_PICK,
    PUBLISH_QUEUE_DELETE_PENDING,
    PUBLISH_QUEUE_INSERT_PENDING,
    PUBLISH_QUEUE_SET_HUMAN_PICK,
)
from algorand_shared.chain_statements import CHAIN_CONDUIT_HEAD, CHAIN_TXNS_BY_ROUND
from algorand_shared.platform_statements import (
    CLASSIFIER_FEEDBACK_INSERT_BY_TIME,
    DOMAIN_TRACKING_INSERT,
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
# share_links / share_links_by_article
# --------------------------------------------------------------------------- #
class ShareLinkStmts:
    """Prepared statements for share_links + its share_links_by_article reverse index.

    Column is `share_token`, not `token` -- `token` is a reserved CQL
    keyword (the token() partitioner function) and CREATE TABLE rejects it
    outright as a bare column name (caught live during the 2026-08-12
    deploy). The Python-level field/API stays named `token` throughout
    (ShareLinkItem.token, the :token URL path param) -- only the underlying
    CQL column is renamed.
    """

    GET = _Stmt(
        "SELECT share_token, article_id, label, created_at, created_by, revoked, revoked_at "
        "FROM algorand_platform.share_links WHERE share_token = ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.share_links "
        "(share_token, article_id, label, created_at, created_by, revoked, revoked_at) "
        "VALUES (?, ?, ?, ?, ?, false, null)"
    )
    REVOKE = _Stmt(
        "UPDATE algorand_platform.share_links SET revoked = true, revoked_at = ? "
        "WHERE share_token = ?"
    )
    LIST_BY_ARTICLE = _Stmt(
        "SELECT article_id, created_at, share_token, label, created_by, revoked, revoked_at "
        "FROM algorand_platform.share_links_by_article WHERE article_id = ?"
    )
    INSERT_BY_ARTICLE = _Stmt(
        "INSERT INTO algorand_platform.share_links_by_article "
        "(article_id, created_at, share_token, label, created_by, revoked, revoked_at) "
        "VALUES (?, ?, ?, ?, ?, false, null)"
    )
    REVOKE_BY_ARTICLE = _Stmt(
        "UPDATE algorand_platform.share_links_by_article SET revoked = true, revoked_at = ? "
        "WHERE article_id = ? AND created_at = ? AND share_token = ?"
    )


# --------------------------------------------------------------------------- #
# draft_comments
# --------------------------------------------------------------------------- #
class DraftCommentStmts:
    """Prepared statements for draft_comments (small per-article partition, full scan)."""

    LIST_BY_ARTICLE = _Stmt(
        "SELECT article_id, created_at, comment_id, body, author_name, "
        "anchor_quote, anchor_prefix, anchor_suffix "
        "FROM algorand_platform.draft_comments WHERE article_id = ?"
    )
    INSERT = _Stmt(
        "INSERT INTO algorand_platform.draft_comments "
        "(article_id, created_at, comment_id, body, author_name, "
        "anchor_quote, anchor_prefix, anchor_suffix) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    DELETE = _Stmt(
        "DELETE FROM algorand_platform.draft_comments "
        "WHERE article_id = ? AND created_at = ? AND comment_id = ?"
    )


# --------------------------------------------------------------------------- #
# article_versions
# --------------------------------------------------------------------------- #
class ArticleVersionStmts:
    """Prepared statements for article edit-history versions."""

    LATEST = ARTICLE_VERSION_LATEST
    INSERT = ARTICLE_VERSION_INSERT
    LIST = _Stmt(
        "SELECT version, title, summary, edit_reason, editor, edited_at "
        "FROM algorand_platform.article_versions WHERE article_id = ? LIMIT ?"
    )
    GET_ONE = _Stmt(
        "SELECT version, title, summary, body, edit_reason, editor, edited_at "
        "FROM algorand_platform.article_versions WHERE article_id = ? AND version = ?"
    )
    LIST_VERSIONS = _Stmt(
        "SELECT version FROM algorand_platform.article_versions WHERE article_id = ?"
    )
    DELETE = _Stmt(
        "DELETE FROM algorand_platform.article_versions WHERE article_id = ? AND version = ?"
    )



# --------------------------------------------------------------------------- #
# editorial_briefs
# --------------------------------------------------------------------------- #
class EditorialBriefStmts:
    """Prepared statements for editorial briefs."""

    INSERT = _Stmt(
        "INSERT INTO algorand_platform.editorial_briefs ("
        "brief_id, title, body_markdown, keywords, status, "
        "wallet_address, created_at, updated_at, refresh_every_days, is_special_edition"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    LIST = _Stmt(
        "SELECT brief_id, title, keywords, status, wallet_address, created_at, updated_at, "
        "refresh_every_days, last_run_at, linked_article_id, is_special_edition "
        "FROM algorand_platform.editorial_briefs LIMIT ?"
    )
    GET = _Stmt(
        "SELECT brief_id, title, body_markdown, keywords, status, "
        "wallet_address, created_at, updated_at, "
        "refresh_every_days, last_run_at, linked_article_id, is_special_edition "
        "FROM algorand_platform.editorial_briefs WHERE brief_id = ?"
    )


# --------------------------------------------------------------------------- #
# classifier_feedback / classifier_feedback_by_time
# --------------------------------------------------------------------------- #
class ClassifierFeedbackStmts:
    """Prepared statements for classifier training feedback."""

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
    INSERT_BY_TIME = CLASSIFIER_FEEDBACK_INSERT_BY_TIME


# --------------------------------------------------------------------------- #
# gatekeeper_anchors / gatekeeper_validation_report
# --------------------------------------------------------------------------- #
class GatekeeperStmts:
    """Prepared statements for gatekeeper telemetry/training data."""

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
    """Prepared statements for per-domain crawl/frontier tracking."""

    GET_FOR_CORRECTION = _Stmt(
        "SELECT domain, last_crawled_at, last_online_at, relevance_score, "
        "category, is_relevant, metadata FROM algorand_platform.domain_tracking WHERE domain = ?"
    )
    INSERT = DOMAIN_TRACKING_INSERT
    LIST_ALL = _Stmt(
        "SELECT domain, last_crawled_at, last_online_at, relevance_score, "
        "category, is_relevant, metadata, frontier_status FROM algorand_platform.domain_tracking"
    )
    DELETE = _Stmt("DELETE FROM algorand_platform.domain_tracking WHERE domain = ?")
    # 5000 comfortably covers the current pool (pending=1213, approved=251,
    # dead_end=220 as of 2026-07-22) with headroom — the old LIMIT 500 silently
    # dropped over half of pending with no ORDER BY to guarantee which half,
    # so newly discovered domains could go permanently invisible in the admin
    # list once the pool crossed 500 (owner report 2026-07-22: "I do not see
    # new services appearing").
    LIST_BY_STATUS = _Stmt(
        "SELECT domain, last_crawled_at, relevance_score, category, "
        "is_relevant, metadata, frontier_status "
        "FROM algorand_platform.domain_tracking WHERE frontier_status = ? LIMIT 5000"
    )
    LIST_BRIEF = _Stmt(
        "SELECT domain, last_crawled_at, relevance_score, category, "
        "is_relevant, metadata, frontier_status FROM algorand_platform.domain_tracking LIMIT 5000"
    )


# --------------------------------------------------------------------------- #
# crawled_pages_by_domain (read-only here)
# --------------------------------------------------------------------------- #
class CrawledPageStmts:
    """Prepared statements for crawled_pages."""

    COUNT_BY_DOMAIN = _Stmt(
        "SELECT COUNT(*) AS c FROM algorand_platform.crawled_pages_by_domain WHERE domain = ?"
    )


# --------------------------------------------------------------------------- #
# tool_suggestions / compose_sessions (writer introspection, read-only here)
# --------------------------------------------------------------------------- #
class ToolInsightStmts:
    """Prepared statements for writer tool-usage insights."""

    LIST_SUGGESTIONS = _Stmt(
        "SELECT created_at, suggestion_id, capability, reason, service_id, source_url, "
        "model, resolved "
        "FROM algorand_platform.tool_suggestions WHERE bucket = ? LIMIT 300"
    )
    RESOLVE_SUGGESTION = _Stmt(
        "UPDATE algorand_platform.tool_suggestions SET resolved = ? "
        "WHERE bucket = ? AND created_at = ? AND suggestion_id = ?"
    )
    LIST_COMPOSE_FEEDBACK = _Stmt(
        "SELECT created_at, category, severity, summary, detail, related_tool, "
        "service_id, source_url, model "
        "FROM algorand_platform.compose_feedback WHERE bucket = ? LIMIT 300"
    )
    LIST_COMPOSE_SESSIONS = _Stmt(
        "SELECT created_at, session_id, service_id, source_url, model, status, "
        "rounds, tool_calls, duration_ms, messages, final_output, "
        "prompt_tokens, completion_tokens, total_tokens "
        "FROM algorand_platform.compose_sessions WHERE bucket = ? LIMIT 20"
    )
    # Summary-only variant for the polled list view — skips messages/final_output,
    # which can be up to ~140KB per row (see tool_insights_store.record_compose_session).
    LIST_COMPOSE_SESSIONS_SUMMARY = _Stmt(
        "SELECT created_at, session_id, service_id, source_url, model, status, "
        "rounds, tool_calls, duration_ms, prompt_tokens, completion_tokens, total_tokens, "
        "cached_tokens "
        "FROM algorand_platform.compose_sessions WHERE bucket = ? LIMIT ?"
    )
    # Keyset page: everything older than the cursor. created_at is a clustering
    # column, so this stays a single-partition range read rather than a scan.
    LIST_COMPOSE_SESSIONS_SUMMARY_BEFORE = _Stmt(
        "SELECT created_at, session_id, service_id, source_url, model, status, "
        "rounds, tool_calls, duration_ms, prompt_tokens, completion_tokens, total_tokens, "
        "cached_tokens "
        "FROM algorand_platform.compose_sessions WHERE bucket = ? AND created_at < ? LIMIT ?"
    )
    GET_COMPOSE_SESSION_DETAIL = _Stmt(
        "SELECT messages, final_output FROM algorand_platform.compose_sessions "
        "WHERE bucket = ? AND created_at = ? AND session_id = ?"
    )


# --------------------------------------------------------------------------- #
# url_queue / url_queue_by_url / url_queue_pending (admin frontier-approval seed)
# --------------------------------------------------------------------------- #
class UrlQueueStmts:
    """Prepared statements for the crawl frontier URL queue."""

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
    """Prepared statements for investigative-tool findings."""

    LIST = _Stmt(
        "SELECT created_at, tool, arguments, result_json "
        "FROM algorand_platform.investigation_findings WHERE service_id = ? LIMIT 50"
    )


# --------------------------------------------------------------------------- #
# classifier_review_queue / classifier_review_pending
# --------------------------------------------------------------------------- #
class ClassifierReviewStmts:
    """Prepared statements for the pending classifier-review queue."""

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
# publish_queue (admin observability — the workers own the write path)
# --------------------------------------------------------------------------- #
class PublishQueueStmts:
    """Prepared statements for the publish queue."""

    # Pending rows come from the SAME single-partition index the drain reads
    # (publish_queue_pending), so the admin view is complete and exact for
    # pending. The unfiltered token-order scan below only fills in resolved
    # history — it is a sample, not "most recent", since queue_id is the sole
    # partition key and there is no status/time index to page by. Both exclude
    # payload — it carries the full page text (up to ~48k chars per row).
    LIST_PENDING_IDS = _Stmt(
        "SELECT queue_id FROM algorand_platform.publish_queue_pending WHERE status = ? LIMIT ?"
    )
    GET_ROW = _Stmt(
        "SELECT queue_id, status, last_reason, priority, topic, publish_kind, "
        "service_id, display_name, scrape_url, created_at, updated_at, human_pick_day "
        "FROM algorand_platform.publish_queue WHERE queue_id = ?"
    )
    LIST_RECENT = _Stmt(
        "SELECT queue_id, status, last_reason, priority, topic, publish_kind, "
        "service_id, display_name, scrape_url, created_at, updated_at, human_pick_day "
        "FROM algorand_platform.publish_queue LIMIT ?"
    )
    GET_PAYLOAD = _Stmt("SELECT payload FROM algorand_platform.publish_queue WHERE queue_id = ?")
    # "Compose next": publish_queue_pending is clustered by (priority DESC,
    # created_at ASC), so a single-row read of the top of that clustering
    # gives the current max pending priority for free — no aggregation query
    # needed. Bumping a row above it means it wins the drain's next priority
    # scan without touching the pacing clock/daily cap that gate WHEN the
    # drain runs at all.
    MAX_PENDING_PRIORITY = _Stmt(
        "SELECT priority FROM algorand_platform.publish_queue_pending WHERE status = ? LIMIT 1"
    )
    UPDATE_PRIORITY = _Stmt(
        "UPDATE algorand_platform.publish_queue SET priority = ?, updated_at = ? WHERE queue_id = ?"
    )
    # publish_queue_pending's clustering key includes priority, so bumping it
    # is a DELETE-at-old-position + INSERT-at-new-position, not an UPDATE.
    DELETE_PENDING = PUBLISH_QUEUE_DELETE_PENDING
    INSERT_PENDING = PUBLISH_QUEUE_INSERT_PENDING
    SET_HUMAN_PICK = PUBLISH_QUEUE_SET_HUMAN_PICK
    CLEAR_HUMAN_PICK = PUBLISH_QUEUE_CLEAR_HUMAN_PICK



# --------------------------------------------------------------------------- #
# News article store (articles_by_id / articles_feed) — the read/write path the
# public feed uses.
# --------------------------------------------------------------------------- #
class NewsStmts:
    """Prepared statements for reader-facing article reads."""

    # Slug -> article id. A single-partition read on the reverse index, never a
    # scan of articles_by_id.
    ID_BY_SLUG = _Stmt(
        "SELECT article_id FROM algorand_platform.articles_by_slug WHERE slug = ?"
    )


# --------------------------------------------------------------------------- #
# article_view_counts (counter table)
# --------------------------------------------------------------------------- #
class ViewCountStmts:
    """Prepared statements for per-article view counters."""

    BUMP = _Stmt(
        "UPDATE algorand_platform.article_view_counts SET views = views + 1 WHERE article_id = ?"
    )
    GET = _Stmt("SELECT views FROM algorand_platform.article_view_counts WHERE article_id = ?")


# --------------------------------------------------------------------------- #
# Chain tables written by the Conduit exporter (read-only here)
# --------------------------------------------------------------------------- #
class ChainStmts:
    """Prepared statements for chain data indexed by Conduit."""

    GET_TXN = _Stmt(
        "SELECT txid, round, intra, sender, txn_type, txn_json, receiver, amount_microalgos "
        "FROM algorand_platform.transactions_by_id WHERE txid = ?"
    )
    CONDUIT_HEAD = CHAIN_CONDUIT_HEAD
    TXNS_BY_ROUND = CHAIN_TXNS_BY_ROUND


# --------------------------------------------------------------------------- #
# suggestions_by_status / upvotes_by_suggestion
# --------------------------------------------------------------------------- #
class SuggestionStmts:
    """Prepared statements for service suggestions."""

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
    """Prepared statements for contact messages."""

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
    """Prepared statements for suggestion upvotes."""

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
    """Prepared statements for the service registry."""

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
# --------------------------------------------------------------------------- #
class ServiceSourceStmts:
    """Prepared statements for a service's known web sources."""

    UPSERT = SERVICE_SOURCE_UPSERT
    LIST_FOR_SERVICE = SERVICE_SOURCE_LIST_FOR_SERVICE
    DELETE_FOR_SERVICE = SERVICE_SOURCE_DELETE_FOR_SERVICE
    UPSERT_BY_DOMAIN = SERVICE_SOURCE_UPSERT_BY_DOMAIN
    GET_BY_DOMAIN = SERVICE_SOURCE_GET_BY_DOMAIN


# --------------------------------------------------------------------------- #
# page_snapshots
# --------------------------------------------------------------------------- #
class SnapshotStmts:
    """Prepared statements for service-source content snapshots."""

    GET_LATEST = PAGE_SNAPSHOT_GET_LATEST
    INSERT = PAGE_SNAPSHOT_INSERT


# --------------------------------------------------------------------------- #
# feed_placements / feed_placements_by_slot
# --------------------------------------------------------------------------- #
class PlacementStmts:
    """Prepared statements for sponsored feed placements."""

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
    """Prepared statements for price-metrics samples and briefs."""

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
    # Sparkline history: newest-first thanks to the DESC clustering order;
    # ~hourly samples, so 200 rows comfortably covers a week.
    PRICE_HISTORY = _Stmt(
        "SELECT collected_at, price_usd "
        "FROM algorand_platform.price_metric_samples WHERE asset_id = ? LIMIT ?"
    )


# --------------------------------------------------------------------------- #
# First-party pageview analytics (counters + samples). Every statement is fully
# defined here — the per-day aggregate reads are NOT built from runtime fragments.
# --------------------------------------------------------------------------- #
class AnalyticsStmts:
    """Prepared statements for session/search analytics."""

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
    LANGUAGE_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_language_daily SET views = views + 1 "
        "WHERE day = ? AND lang = ?"
    )
    HOUR_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_hour_daily SET views = views + 1 "
        "WHERE day = ? AND hour = ?"
    )
    REFERRER_BUMP = _Stmt(
        "UPDATE algorand_platform.pageview_referrer_daily SET views = views + 1 "
        "WHERE day = ? AND referrer = ?"
    )
    # -- retroactive correction (2026-07-22): a UA whose is_repeated_ua count
    # JUST crossed the daily threshold had its earlier hits today already
    # counted as human via the '(direct)' bucket — the only lane with a raw
    # per-request log (pageview_direct_sample) to reconstruct a correction
    # from. Counter columns accept negative deltas same as positive.
    DIRECT_SAMPLE_ALL_BY_DAY = _Stmt(
        "SELECT ts, path, referer, user_agent, ua_class "
        "FROM algorand_platform.pageview_direct_sample WHERE day = ?"
    )
    DIRECT_SAMPLE_DELETE = _Stmt(
        "DELETE FROM algorand_platform.pageview_direct_sample WHERE day = ? AND ts = ?"
    )
    PAGEVIEW_BUMP_DECR = _Stmt(
        "UPDATE algorand_platform.pageview_daily SET views = views - ? WHERE kind = ? AND day = ?"
    )
    PATH_KIND_BUMP_DECR = _Stmt(
        "UPDATE algorand_platform.pageview_path_kind_daily SET views = views - ? "
        "WHERE day = ? AND path = ? AND kind = ?"
    )
    DEVICE_BUMP_DECR = _Stmt(
        "UPDATE algorand_platform.pageview_device_daily SET views = views - ? "
        "WHERE day = ? AND device = ?"
    )
    BROWSER_BUMP_DECR = _Stmt(
        "UPDATE algorand_platform.pageview_browser_daily SET views = views - ? "
        "WHERE day = ? AND browser = ?"
    )
    REFERRER_BUMP_DECR = _Stmt(
        "UPDATE algorand_platform.pageview_referrer_daily SET views = views - ? "
        "WHERE day = ? AND referrer = ?"
    )
    DIRECT_UACLASS_BUMP_DECR = _Stmt(
        "UPDATE algorand_platform.pageview_direct_uaclass_daily SET views = views - ? "
        "WHERE day = ? AND ua_class = ?"
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
    AGG_NOTFOUND = _Stmt(
        "SELECT path, views FROM algorand_platform.pageview_notfound_daily WHERE day = ?"
    )
    AGG_DEVICE = _Stmt(
        "SELECT device, views FROM algorand_platform.pageview_device_daily WHERE day = ?"
    )
    AGG_BROWSER = _Stmt(
        "SELECT browser, views FROM algorand_platform.pageview_browser_daily WHERE day = ?"
    )
    AGG_LANGUAGE = _Stmt(
        "SELECT lang, views FROM algorand_platform.pageview_language_daily WHERE day = ?"
    )
    AGG_REFERRER_URL = _Stmt(
        "SELECT referrer_url, views FROM algorand_platform.pageview_referrer_url_daily "
        "WHERE day = ?"
    )
    AGG_GEO = _Stmt("SELECT country, views FROM algorand_platform.geo_country_daily WHERE day = ?")
    AGG_CAMPAIGN = _Stmt(
        "SELECT campaign, views FROM algorand_platform.campaign_daily WHERE day = ?"
    )


# --------------------------------------------------------------------------- #
# kyc_enrollments / kyc_lookup_events (KYC-as-a-service, x402 challenge)
# --------------------------------------------------------------------------- #
class KycStmts:
    """Prepared statements for KYC enrollments and lookup events."""

    UPSERT_ENROLLMENT = _Stmt(
        "INSERT INTO algorand_platform.kyc_enrollments ("
        "wallet_address, enrolled_at, updated_at, consent_signature_b64, "
        "wallet_age_round, recent_tx_count, kyc_level"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    GET_ENROLLMENT = _Stmt(
        "SELECT wallet_address, enrolled_at, updated_at, consent_signature_b64, "
        "wallet_age_round, recent_tx_count, kyc_level "
        "FROM algorand_platform.kyc_enrollments WHERE wallet_address = ?"
    )
    INSERT_LOOKUP_EVENT = _Stmt(
        "INSERT INTO algorand_platform.kyc_lookup_events ("
        "wallet_address, created_at, payer_address, payment_txid, found, "
        "payout_status, payout_txid, payout_error"
        ") VALUES (?, now(), ?, ?, ?, ?, ?, ?)"
    )


# --------------------------------------------------------------------------- #
# glossary_terms
# --------------------------------------------------------------------------- #
class GlossaryStmts:
    """Prepared statements for the admin-curated glossary."""

    # No WHERE clause -- a full-partition scan, same convention as
    # ServiceRegistryStmts.LIST_ALL for a small, fully-enumerable admin table.
    LIST_ALL = _Stmt(
        "SELECT slug, term, definition, aliases, status, created_at, updated_at, "
        "created_by, translations FROM algorand_platform.glossary_terms"
    )
    GET = _Stmt(
        "SELECT slug, term, definition, aliases, status, created_at, updated_at, "
        "created_by, translations FROM algorand_platform.glossary_terms WHERE slug = ?"
    )
    UPSERT = _Stmt(
        "INSERT INTO algorand_platform.glossary_terms ("
        "slug, term, definition, aliases, status, created_at, updated_at, created_by"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    DELETE = _Stmt("DELETE FROM algorand_platform.glossary_terms WHERE slug = ?")
    UPDATE_TRANSLATIONS = GLOSSARY_UPDATE_TRANSLATIONS
