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
)
from algorand_shared.artifact_statements import ArtifactStmts as ArtifactStmts
from algorand_shared.artifact_statements import ToComposeStmts as ToComposeStmts
from algorand_shared.chain_statements import CHAIN_CONDUIT_HEAD, CHAIN_TXNS_BY_ROUND
from algorand_shared.crawler_statements import (
    CLASSIFIER_FEEDBACK_INSERT,
    CLASSIFIER_REVIEW_DELETE_PENDING,
    CLASSIFIER_REVIEW_GET_DETAIL,
    CLASSIFIER_REVIEW_INSERT_QUEUE,
    CLASSIFIER_REVIEW_LIST_PENDING,
    CRAWLED_PAGE_COUNT_BY_DOMAIN,
    URL_QUEUE_INSERT,
    URL_QUEUE_INSERT_PENDING,
)
from algorand_shared.platform_statements import (
    CLASSIFIER_FEEDBACK_INSERT_BY_TIME,
    DOMAIN_TRACKING_INSERT,
    GLOSSARY_UPDATE_TRANSLATIONS,
    PAGE_SNAPSHOT_GET_LATEST,
    PAGE_SNAPSHOT_INSERT,
    SERVICE_REGISTRY_GET_ID,
    SERVICE_REGISTRY_GET_SCRAPE_URL,
    SERVICE_REGISTRY_LIST_ALL,
    SERVICE_REGISTRY_SET_ENABLED,
    SERVICE_REGISTRY_UPSERT,
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
    # article_versions' PRIMARY KEY is (article_id, version) -- article_id alone
    # is the full partition key, so every version of one article lives in the
    # same partition. Deleting "all versions of this article" is therefore a
    # single partition-range delete, not a per-version tombstone: no read of
    # the version list is needed first, and there is nothing to batch (a
    # multi-statement BATCH buys nothing over one DELETE that already covers
    # the whole partition, and Cassandra's own guidance is against reaching
    # for BATCH as a round-trip-reduction tool).
    DELETE_ALL_FOR_ARTICLE = _Stmt(
        "DELETE FROM algorand_platform.article_versions WHERE article_id = ?"
    )


# --------------------------------------------------------------------------- #
# articles (admin-only bounded reads)
# --------------------------------------------------------------------------- #
class AdminArticleStmts:
    """Backend-admin-only prepared statements against `articles` that need a LIMIT.

    The shared ArticlesStmts.LIST_IDS_BY_STATUS (algorand_shared.
    article_statements) doesn't take one; kept as a separate statement here
    rather than changing the shared one's signature out from under its other
    callers (e.g. the sitemap's deleted-article enumeration).
    """

    LIST_IDS_BY_STATUS_BOUNDED = _Stmt(
        "SELECT article_id, status_updated_at FROM algorand_platform.articles "
        "WHERE status = ? AND year = ? LIMIT ?"
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
    """Prepared statements for classifier training feedback.

    GET_GRADE deliberately does NOT select `url` -- workers' copy of this
    class does (see workers/app/core/statements.py), but grep found no
    workers call site that reads it back off the row; left as unexercised
    drift there rather than edited as part of this consolidation.
    """

    GET_GRADE = _Stmt(
        "SELECT approved, metadata FROM algorand_platform.classifier_feedback WHERE feedback_id = ?"
    )
    LIST_BY_TIME = _Stmt(
        "SELECT feedback_id, approved FROM algorand_platform.classifier_feedback_by_time "
        "WHERE bucket = ? LIMIT 5000"
    )
    INSERT = CLASSIFIER_FEEDBACK_INSERT
    INSERT_BY_TIME = CLASSIFIER_FEEDBACK_INSERT_BY_TIME


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

    COUNT_BY_DOMAIN = CRAWLED_PAGE_COUNT_BY_DOMAIN


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

    # All writes bind a trailing TTL param (config.URL_QUEUE_ROW_TTL_SECONDS)
    # so terminal rows can expire instead of accumulating forever. Binding 0 is
    # the documented CQL "no TTL" value — identical to the pre-TTL statements —
    # so one statement shape serves both the enabled and disabled config.
    # Mirrors workers' UrlQueueStmts (whose UPDATE_STATUS carries the same
    # TTL) — keep the TTL treatment in sync across both services.
    INSERT = URL_QUEUE_INSERT
    # Unlike workers' INSERT_BY_URL, this also writes a `status` column --
    # harmless drift (see algorand_shared.crawler_statements' module docstring:
    # nothing actually reads `.status` back off `url_queue_by_url`), left
    # local/distinct rather than force-unified.
    INSERT_BY_URL = _Stmt(
        "INSERT INTO algorand_platform.url_queue_by_url (url, queue_id, enqueued_at, status) "
        "VALUES (?, ?, ?, ?) USING TTL ?"
    )
    INSERT_PENDING = URL_QUEUE_INSERT_PENDING


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
    """Prepared statements for the pending classifier-review queue.

    GET_FULL deliberately does NOT select `status` -- workers' copy of this
    class does (see workers/app/core/statements.py), because its recompose
    path needs to refuse acting on an already-resolved review (2026-07-10
    regression fix). Backend's only caller (`_complete_classifier_review`)
    always overwrites status with the resolution being applied, so it never
    needs to read the old value back.
    """

    GET_METADATA = _Stmt(
        "SELECT metadata FROM algorand_platform.classifier_review_queue WHERE review_id = ?"
    )
    GET_FULL = _Stmt(
        "SELECT review_id, url, page_text, page_title, category, storage_score, "
        "created_at, metadata FROM algorand_platform.classifier_review_queue WHERE review_id = ?"
    )
    GET_DETAIL = CLASSIFIER_REVIEW_GET_DETAIL
    INSERT_QUEUE = CLASSIFIER_REVIEW_INSERT_QUEUE
    LIST_PENDING = CLASSIFIER_REVIEW_LIST_PENDING
    DELETE_PENDING = CLASSIFIER_REVIEW_DELETE_PENDING


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
    # suggestions_by_id (migration 089): suggestion_id -> full row lookup
    # table, dual-written alongside every INSERT above. Replaces
    # `WHERE status = ? AND suggestion_id = ? ALLOW FILTERING` against
    # suggestions_by_status -- suggestion_id is the second clustering column
    # (after created_at), so filtering on it alone skipped a clustering
    # column and forced ALLOW FILTERING even though the read was scoped to
    # a single partition -- with a direct partition-key point lookup.
    GET = _Stmt(
        "SELECT suggestion_id, wallet_address, title, body, submission_txid, created_at, status "
        "FROM algorand_platform.suggestions_by_id WHERE suggestion_id = ?"
    )
    INSERT_BY_ID = _Stmt(
        "INSERT INTO algorand_platform.suggestions_by_id ("
        "suggestion_id, status, created_at, wallet_address, title, body, submission_txid"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    # suggestions_by_txid (migration 087): submission_txid -> suggestion_id
    # lookup table, dual-written alongside every INSERT above. Replaces a
    # cluster-wide `WHERE submission_txid = ? ALLOW FILTERING` scan of
    # suggestions_by_status (every submission ran it) with a direct
    # partition-key point lookup.
    INSERT_TXID = _Stmt(
        "INSERT INTO algorand_platform.suggestions_by_txid ("
        "submission_txid, suggestion_id, status, created_at"
        ") VALUES (?, ?, ?, ?)"
    )
    HAS_TXID = _Stmt(
        "SELECT suggestion_id FROM algorand_platform.suggestions_by_txid WHERE submission_txid = ?"
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
    """Prepared statements for the service registry.

    DELETE is backend-admin-only (workers never deletes a registry row), so
    it stays local -- everything else here is byte-identical to workers' copy
    and sourced from algorand_shared.platform_statements.
    """

    LIST_ALL = SERVICE_REGISTRY_LIST_ALL
    UPSERT = SERVICE_REGISTRY_UPSERT
    DELETE = _Stmt("DELETE FROM algorand_platform.service_registry WHERE service_id = ?")
    GET_ID = SERVICE_REGISTRY_GET_ID
    SET_ENABLED = SERVICE_REGISTRY_SET_ENABLED
    GET_SCRAPE_URL = SERVICE_REGISTRY_GET_SCRAPE_URL


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
    # Same raw-sample idea as DIRECT_SAMPLE_INSERT, widened (2026-08-25) to
    # '(internal)'/external-referred hits — see pageview_referred_sample
    # migration 074 for why this is a separate table.
    REFERRED_SAMPLE_INSERT = _Stmt(
        "INSERT INTO algorand_platform.pageview_referred_sample "
        "(day, ts, path, bucket, referer, user_agent, ua_class) "
        "VALUES (?, now(), ?, ?, ?, ?, ?)"
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
    # '(internal)'/external-referred mirror of the two statements above (2026-08-25).
    REFERRED_SAMPLE_ALL_BY_DAY = _Stmt(
        "SELECT ts, path, bucket, referer, user_agent, ua_class "
        "FROM algorand_platform.pageview_referred_sample WHERE day = ?"
    )
    REFERRED_SAMPLE_DELETE = _Stmt(
        "DELETE FROM algorand_platform.pageview_referred_sample WHERE day = ? AND ts = ?"
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
# x402_listings / x402_listings_by_recency / x402_settlements (migration 090)
# --------------------------------------------------------------------------- #
class X402DirectoryStmts:
    """Prepared statements for the x402 endpoint directory and settlement ledger."""

    # Full INSERT, never a partial UPDATE: a partial write to either listing
    # table would upsert a row whose unwritten columns read back as null, the
    # same phantom-row class articles_feed hit (CLAUDE.md section 3).
    UPSERT_LISTING = _Stmt(
        "INSERT INTO algorand_platform.x402_listings ("
        "url_hash, url, price, assets, description, schema_json, tags, "
        "term_end, settlement_tx_id, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    GET_LISTING = _Stmt(
        "SELECT url_hash, url, price, assets, description, schema_json, tags, "
        "term_end, settlement_tx_id, created_at "
        "FROM algorand_platform.x402_listings WHERE url_hash = ?"
    )
    INSERT_RECENCY = _Stmt(
        "INSERT INTO algorand_platform.x402_listings_by_recency ("
        "directory, created_at, url_hash, url, price, assets, description, "
        "schema_json, tags, term_end, settlement_tx_id"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    # Deletes the row a re-listing supersedes. Needs the exact created_at of
    # the previous listing (read from x402_listings first), since created_at is
    # a clustering column -- deleting by url_hash alone is not addressable.
    DELETE_RECENCY = _Stmt(
        "DELETE FROM algorand_platform.x402_listings_by_recency "
        "WHERE directory = ? AND created_at = ? AND url_hash = ?"
    )
    # Newest-first by clustering order; the LIMIT is bound, never interpolated,
    # and the caller clamps it (no unbounded listings, CLAUDE.md section 4).
    LIST_RECENT = _Stmt(
        "SELECT url_hash, url, price, assets, description, schema_json, tags, "
        "term_end, settlement_tx_id, created_at "
        "FROM algorand_platform.x402_listings_by_recency "
        "WHERE directory = ? LIMIT ?"
    )


class X402BoardStmts:
    """Prepared statements for the x402 paid visibility board."""

    # Full INSERT, never a partial UPDATE: a partial write to either board
    # table would upsert a row whose unwritten columns read back as null, the
    # same phantom-row class articles_feed hit (CLAUDE.md section 3).
    UPSERT_PLACEMENT = _Stmt(
        "INSERT INTO algorand_platform.x402_board_entries ("
        "entry_id, link, name, pitch, payer, term_end, settlement_tx_id, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    GET_PLACEMENT = _Stmt(
        "SELECT entry_id, link, name, pitch, payer, term_end, settlement_tx_id, "
        "created_at FROM algorand_platform.x402_board_entries WHERE entry_id = ?"
    )
    INSERT_RECENCY = _Stmt(
        "INSERT INTO algorand_platform.x402_board_by_recency ("
        "board, created_at, entry_id, link, name, pitch, payer, term_end, settlement_tx_id"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    # Deletes the row a renewed placement supersedes. Needs the exact
    # created_at of the previous placement (read from x402_board_entries
    # first), since created_at is a clustering column -- deleting by entry_id
    # alone is not addressable.
    DELETE_RECENCY = _Stmt(
        "DELETE FROM algorand_platform.x402_board_by_recency "
        "WHERE board = ? AND created_at = ? AND entry_id = ?"
    )
    # Newest-first by clustering order; the LIMIT is bound, never interpolated,
    # and the caller clamps it (no unbounded listings, CLAUDE.md section 4).
    # Expired placements are filtered by BoardService, not here: term_end is
    # not part of the key, so a CQL filter on it would need ALLOW FILTERING.
    LIST_RECENT = _Stmt(
        "SELECT entry_id, link, name, pitch, payer, term_end, settlement_tx_id, "
        "created_at FROM algorand_platform.x402_board_by_recency "
        "WHERE board = ? LIMIT ?"
    )


class X402FeaturesStmts:
    """Prepared statements for the x402 feature-request board."""

    # Full INSERT, never a partial UPDATE: a partial write to either request
    # table would upsert a row whose unwritten columns read back as null, the
    # same phantom-row class articles_feed hit (CLAUDE.md section 3).
    INSERT_REQUEST = _Stmt(
        "INSERT INTO algorand_platform.x402_feature_requests ("
        "request_id, title, description, submitter, settlement_tx_id, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?)"
    )
    GET_REQUEST = _Stmt(
        "SELECT request_id, title, description, submitter, settlement_tx_id, "
        "created_at FROM algorand_platform.x402_feature_requests WHERE request_id = ?"
    )
    INSERT_RECENCY = _Stmt(
        "INSERT INTO algorand_platform.x402_feature_requests_by_recency ("
        "board, created_at, request_id, title, description, submitter, settlement_tx_id"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    # Newest-first by clustering order; the LIMIT is bound, never interpolated,
    # and the caller clamps it (no unbounded listings, CLAUDE.md section 4).
    # There is no DELETE_RECENCY counterpart to the board's: a feature request
    # is created once and never re-stamped, so no projection row is ever
    # superseded.
    LIST_RECENT = _Stmt(
        "SELECT request_id, title, description, submitter, settlement_tx_id, "
        "created_at FROM algorand_platform.x402_feature_requests_by_recency "
        "WHERE board = ? LIMIT ?"
    )
    # A counter update, which is why vote_total lives in its own table:
    # Cassandra forbids mixing counter and non-counter columns outside the
    # primary key, so it cannot be a column on x402_feature_requests. The
    # counter is what makes two simultaneous votes both land -- see
    # CassandraFeatureStore.increment_vote_total.
    INCREMENT_VOTE_TOTAL = _Stmt(
        "UPDATE algorand_platform.x402_feature_vote_totals "
        "SET vote_total = vote_total + 1 WHERE request_id = ?"
    )
    GET_VOTE_TOTAL = _Stmt(
        "SELECT vote_total FROM algorand_platform.x402_feature_vote_totals WHERE request_id = ?"
    )
    # Append-only audit log, one row per settled vote. Never read on a request
    # path and deliberately has no public read surface -- it exists for abuse
    # forensics, not as a product.
    INSERT_VOTE = _Stmt(
        "INSERT INTO algorand_platform.x402_feature_votes ("
        "request_id, voted_at, settlement_tx_id, voter"
        ") VALUES (?, ?, ?, ?)"
    )


class X402Stmts:
    """Prepared statements shared by every x402-gated module (modules/x402/).

    Not directory-specific, despite the table being defined in migration 090
    (the directory was simply the first paid module built). See
    modules/x402/settlement.py.
    """

    INSERT_SETTLEMENT = _Stmt(
        "INSERT INTO algorand_platform.x402_settlements ("
        "day, settled_at, tx_id, asset_id, amount_atomic, payer, resource, "
        "network, eur_value"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )


class X402GradingStmts:
    """Prepared statements for x402 endpoint grading (migration 093)."""

    # Full INSERT, never a partial UPDATE: a partial write to either grading
    # table would upsert a row whose unwritten columns read back as null, the
    # same phantom-row class articles_feed hit (CLAUDE.md section 3).
    #
    # This single statement is also the whole "one grade per (grader, url),
    # latest overwrites" rule: (url_hash, grader) is the primary key, so a
    # re-grade addresses and replaces the same row rather than adding one.
    UPSERT_GRADE = _Stmt(
        "INSERT INTO algorand_platform.x402_grades ("
        "url_hash, grader, url, score, comment, settlement_tx_id, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    GET_GRADE = _Stmt(
        "SELECT url_hash, grader, url, score, comment, settlement_tx_id, created_at "
        "FROM algorand_platform.x402_grades WHERE url_hash = ? AND grader = ?"
    )
    # One endpoint's grades, for the aggregate. Single partition, clustered by
    # grader; the LIMIT is bound, never interpolated, and the caller clamps it
    # (no unbounded listings, CLAUDE.md section 4).
    LIST_GRADES = _Stmt(
        "SELECT url_hash, grader, url, score, comment, settlement_tx_id, created_at "
        "FROM algorand_platform.x402_grades WHERE url_hash = ? LIMIT ?"
    )
    INSERT_GRADED_ENDPOINT = _Stmt(
        "INSERT INTO algorand_platform.x402_graded_endpoints ("
        "registry, url_hash, url, last_graded_at"
        ") VALUES (?, ?, ?, ?)"
    )
    GET_GRADED_ENDPOINT = _Stmt(
        "SELECT registry, url_hash, url, last_graded_at "
        "FROM algorand_platform.x402_graded_endpoints WHERE registry = ? AND url_hash = ?"
    )
    LIST_GRADED_ENDPOINTS = _Stmt(
        "SELECT registry, url_hash, url, last_graded_at "
        "FROM algorand_platform.x402_graded_endpoints WHERE registry = ? LIMIT ?"
    )
    # Reads the SHARED settlement ledger (x402_settlements, migration 090) to
    # total how much a grader has paid this marketplace, which is the weight
    # their grade carries. A read of another module's table, deliberately: the
    # ledger is the one place settlements are recorded and duplicating it would
    # be worse. It selects only the three columns the sum needs -- network
    # included because TestNet and MainNet spend must never be summed together
    # (modules/x402/settlement.py records it per row for exactly this reason).
    #
    # Whole day partitions, summed in Python, because payer is not a key column
    # and CLAUDE.md section 4 forbids ALLOW FILTERING on non-key columns.
    # Partition-key read, bound LIMIT, caller-bounded number of days --
    # bounded, but see CassandraSpendLookup for why a by-payer projection of
    # the ledger is the real answer.
    LIST_SETTLEMENTS_FOR_DAY = _Stmt(
        "SELECT payer, amount_atomic, network FROM algorand_platform.x402_settlements "
        "WHERE day = ? LIMIT ?"
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
