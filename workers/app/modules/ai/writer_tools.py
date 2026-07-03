"""Agentic tools the Mistral writer can call on demand while composing.

Each tool is a (schema, handler) pair. Handlers read live platform data and
must be cheap and failure-tolerant — a tool error returns {"error": ...} and
never aborts the article.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _tool_get_algo_market() -> dict[str, Any]:
    from app.modules.newspaper.writer_enrichment.collectors.market import collect_market_context

    return collect_market_context()


def _tool_get_chain_head() -> dict[str, Any]:
    from app.modules.chain_tail.chain_reader import get_algod_head_round, get_conduit_head_round

    indexed: int | None = None
    try:
        indexed = get_conduit_head_round()
    except Exception:
        indexed = None

    live: int | None = None
    try:
        live = get_algod_head_round()
    except Exception:
        live = None

    latest = live if live is not None else indexed
    if latest is None:
        return {"error": "chain head unavailable from both algod and conduit index"}
    return {"latest_round": latest, "live_round": live, "indexed_round": indexed}


def _tool_get_price_history(days: int = 7) -> dict[str, Any]:
    """Daily-ish ALGO price series for charting (x=dates, y=USD price)."""
    from app.modules.metrics.price_metrics_store import list_recent_samples

    d = max(1, min(int(days), 30))
    samples = list_recent_samples(asset_id="algorand", lookback_days=d, limit=400)
    pts = sorted(
        ((row.collected_at, float(row.price_usd)) for row in samples),
        key=lambda t: t[0],
    )
    # Down-sample to at most ~14 points so the chart stays readable.
    if len(pts) > 14:
        step = len(pts) // 14
        pts = pts[::step]
    return {
        "x": [t[0].strftime("%m-%d") for t in pts],
        "y": [round(t[1], 5) for t in pts],
        "points": len(pts),
    }


def _tool_recent_articles(limit: int = 5) -> dict[str, Any]:
    from app.modules.newspaper.article_store import list_feed_articles

    rows = list_feed_articles(limit=max(1, min(int(limit), 15)))
    return {
        "articles": [
            {"article_id": r.article_id, "title": r.title, "summary": (r.summary or "")[:160]}
            for r in rows
        ]
    }


def _tool_search_platform(query: str, limit: int = 5) -> dict[str, Any]:
    """Lightweight title/summary match over the published feed (no extra infra)."""
    from app.modules.newspaper.article_store import list_feed_articles

    q = (query or "").lower().strip()
    if not q:
        return {"matches": []}
    terms = [t for t in q.split() if t]
    matches = []
    for r in list_feed_articles(limit=200):
        hay = f"{r.title} {r.summary or ''}".lower()
        if any(t in hay for t in terms):
            matches.append(
                {"article_id": r.article_id, "title": r.title, "summary": (r.summary or "")[:160]}
            )
        if len(matches) >= max(1, min(int(limit), 10)):
            break
    return {"matches": matches}


def _tool_search_crawled_pages(
    query: str,
    limit: int = 5,
    domain: str = "",
    service_id: str = "",
) -> dict[str, Any]:
    """Search stored crawled pages in Typesense `pages` collection."""
    from app.modules.search.core.indexer import PAGES_COLLECTION
    from app.modules.search.core.typesense_config import (
        build_typesense_client,
        is_typesense_configured,
    )

    q = (query or "").strip()
    if not q:
        return {"matches": []}
    if not is_typesense_configured():
        return {"error": "typesense_not_configured", "matches": []}
    client = build_typesense_client()
    if client is None:
        return {"error": "typesense_client_unavailable", "matches": []}

    n = max(1, min(int(limit), 10))
    filters: list[str] = []
    d = (domain or "").strip().lower()
    if d:
        filters.append(f"domain:={d}")
    sid = (service_id or "").strip()
    if sid:
        filters.append(f"service_id:={sid}")
    params: dict[str, Any] = {
        "q": q,
        "query_by": "title,description,body,url,domain,keywords",
        "per_page": n,
    }
    if filters:
        params["filter_by"] = " && ".join(filters)

    try:
        result = client.collections[PAGES_COLLECTION].documents.search(params)
    except Exception as exc:
        return {"error": str(exc)[:200], "matches": []}

    matches = []
    for found in result.get("hits", [])[:n]:
        doc = found.get("document", {})
        matches.append(
            {
                "url": doc.get("url", ""),
                "domain": doc.get("domain", ""),
                "title": doc.get("title", ""),
                "description": (doc.get("description", "") or "")[:320],
                "keywords": list(doc.get("keywords") or [])[:10],
                "service_id": doc.get("service_id", ""),
                "score": found.get("text_match", 0),
            }
        )
    return {"query": q, "count": len(matches), "matches": matches}


def _tool_get_article(article_id: str) -> dict[str, Any]:
    """Load the FULL text of a previously published article by id.

    Use after recent_articles / search_platform / source_history to read prior
    coverage in depth — to build on it, avoid repeating it, or link it. Returns
    the full markdown body. Does NOT expose trigger txids/rounds (those must
    never appear in a new article body)."""
    from app.modules.newspaper.article_store import get_article

    aid = (article_id or "").strip()
    if not aid:
        return {"error": "article_id is required"}
    detail = get_article(aid)
    if detail is None:
        return {"error": "no article found for that id"}
    body = detail.body or ""
    # The agent loop truncates each serialized tool result to 4000 chars; keep the
    # body within that window so the metadata fields below survive, and place body
    # last so any overflow only clips the body tail rather than dropping fields.
    truncated = len(body) > 3000
    return {
        "article_id": detail.article_id,
        "title": detail.title,
        "summary": detail.summary,
        "source_url": detail.source_url,
        "published_at_epoch": detail.published_at_epoch,
        "body_truncated": truncated,
        "body": body[:3000],
    }


def _tool_trending_articles(limit: int = 5) -> dict[str, Any]:
    """Most-read recent articles on this platform — what readers actually click.
    Use to gauge audience interest or pick a follow-up angle."""
    from app.modules.newspaper.article_store import list_feed_articles
    from app.modules.newspaper.view_counts import get_views_bulk

    rows = list_feed_articles(limit=60)
    counts = get_views_bulk([r.article_id for r in rows])
    ranked = sorted(rows, key=lambda r: counts.get(r.article_id, 0), reverse=True)
    n = max(1, min(int(limit), 10))
    return {
        "articles": [
            {
                "title": r.title,
                "views": counts.get(r.article_id, 0),
                "summary": (r.summary or "")[:160],
            }
            for r in ranked[:n]
            if counts.get(r.article_id, 0) > 0
        ]
    }


def _tool_source_history(source: str, limit: int = 8) -> dict[str, Any]:
    """Past articles this platform already published about one source domain or
    publisher, newest first — continuity so the writer can say 'third incident
    this quarter' and avoid repeating prior coverage."""
    from app.modules.chain_tail.registry_cache import load_enabled_services
    from app.modules.crawler.domain_tracker import domain_from_url
    from app.modules.newspaper.article_store import list_feed_articles

    q = (source or "").strip().lower()
    if not q:
        return {"source": source, "articles": []}
    want_domain = domain_from_url(q) if ("." in q or "://" in q) else ""
    # The model knows a source by display name / URL; resolve to internal ids.
    service_ids: set[str] = set()
    for svc in load_enabled_services():
        name = (svc.display_name or "").lower()
        svc_domain = domain_from_url(svc.scrape_url or "") if svc.scrape_url else ""
        if (name and (q == name or q in name)) or (want_domain and want_domain == svc_domain):
            service_ids.add(svc.service_id)
    if not service_ids:
        return {"source": source, "articles": [], "note": "no matching tracked source"}
    n = max(1, min(int(limit), 15))
    rows = [r for r in list_feed_articles(limit=300) if r.service_id in service_ids]
    rows.sort(key=lambda r: r.published_at_epoch, reverse=True)
    return {
        "source": source,
        "matched_services": sorted(service_ids),
        "articles": [
            {
                "title": r.title,
                "summary": (r.summary or "")[:160],
                "published_at_epoch": r.published_at_epoch,
            }
            for r in rows[:n]
        ],
    }


def _tool_review_draft(title: str, body: str) -> dict[str, Any]:
    """Self-assessment for the writer: grade its own draft 0-10 with subscores
    (novelty/relevance/recency/length/structure) + issues, so it can fix
    problems once before finishing."""
    from app.modules.newspaper.article_grader import grade_article_draft

    try:
        return grade_article_draft(title=title, body=body)
    except Exception as exc:
        return {"error": str(exc)[:200], "grade": None}


SUGGEST_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "suggest_tool",
        "description": (
            "Report a data source your available tools do NOT already cover that "
            "would have made THIS story sharper, deeper, or better verified. FIRST "
            "check your tool list and USE the closest existing tool instead of "
            "suggesting it — e.g. lookup_account / lookup_asset / lookup_application "
            "for mainnet holdings and contract state, testnet_lookup to verify a "
            "Testnet txn/app deploy, github_activity for repo momentum and "
            "github_repository_contents to READ contract source, search_leak_databases "
            "for offshore leaks and screen_sanctions_and_pep for people (present on "
            "investigative stories), discourse_forum "
            "for forum activity, search_bluesky for community sentiment (use this "
            "instead of X/Twitter), medium_api_article_list for a blog's article list, "
            "reddit_api_post_history for a user's Reddit history, fetch_archive_text to "
            "read a deleted/edited page from the Wayback Machine. ONLY when nothing "
            "existing fits, record the genuine gap (e.g. Telegram search, an NFT "
            "collection's floor price, a historical TVL time-series). Do NOT suggest a "
            "capability you already have. This returns "
            "no data; call it only for real gaps, then keep writing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "capability": {
                    "type": "string",
                    "description": (
                        "short name of the GENUINELY missing tool/data, e.g. 'twitter_x_search'"
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "how it would have improved this story (what you couldn't get or "
                        "had to work around)"
                    ),
                },
            },
            "required": ["capability"],
        },
    },
}


def _make_suggest_tool_handler(context: dict[str, Any] | None):
    ctx = context or {}

    def _handler(capability: str = "", reason: str = "") -> dict[str, Any]:
        from app.modules.ai.tool_insights_store import record_tool_suggestion

        record_tool_suggestion(
            capability,
            reason,
            service_id=str(ctx.get("service_id", "")),
            source_url=str(ctx.get("source_url", "")),
            model=str(ctx.get("model", "")),
        )
        return {"ok": True, "noted": (capability or "").strip()}

    return _handler


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_algo_market",
            "description": (
                "Current ALGO price (USD), 24h change, market cap, volume and trend narrative."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chain_head",
            "description": (
                "Current Algorand chain head. Returns `latest_round` (use this as the "
                "on-chain freshness/recency signal), plus `live_round` (true network "
                "head from algod) and `indexed_round` (latest round the platform has "
                "ingested into its index). `latest_round` is the live head when the "
                "node is reachable, otherwise the indexed head."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_history",
            "description": (
                "ALGO daily price series (x=dates, y=USD) for plotting a line or bar chart."
            ),
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "description": "1-30, default 7"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_articles",
            "description": (
                "Recently published articles on this platform, for context and cross-references."
            ),
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "1-15"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_platform",
            "description": (
                "Search previously published articles by keywords to avoid repetition "
                "and add links."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "description": "1-10"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_crawled_pages",
            "description": (
                "Search crawled website pages stored in the platform index. "
                "Use this when you need extra source context beyond the initial "
                "page, and optionally narrow by domain or service_id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "description": "1-10, default 5"},
                    "domain": {
                        "type": "string",
                        "description": "optional domain filter, e.g. algorand.foundation",
                    },
                    "service_id": {"type": "string", "description": "optional service_id filter"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_article",
            "description": (
                "Load the FULL markdown text of a previously published article by id "
                "(get the id from recent_articles, search_platform or source_history). "
                "Use to read prior coverage in depth so you can build on it, link it, or "
                "avoid repeating it — the other tools only return titles and summaries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "article_id": {
                        "type": "string",
                        "description": "article_id from another tool's result",
                    },
                },
                "required": ["article_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trending_articles",
            "description": (
                "Most-read recent articles on this platform (by reader view count). "
                "Use to gauge what the audience cares about or choose a follow-up angle."
            ),
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "description": "1-10, default 5"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "source_history",
            "description": (
                "Past articles this platform published about a given source domain "
                "or publisher (newest first). Use to add continuity and avoid "
                "repeating prior coverage; pass the source URL, domain, or publisher name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "source domain, URL, or publisher name",
                    },
                    "limit": {"type": "integer", "description": "1-15, default 8"},
                },
                "required": ["source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_draft",
            "description": (
                "Self-assess your draft BEFORE finishing: returns a quality grade 0-10 with "
                "per-dimension subscores (novelty vs recent articles, relevance, recency, "
                "length, structure) and concrete issues to fix. Call this ONCE "
                "when the draft is ready; if the grade is below ~6 or there are issues, revise "
                "to address them, then finish. Do NOT call it repeatedly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "your draft title"},
                    "body": {"type": "string", "description": "your full draft markdown body"},
                },
                "required": ["title", "body"],
            },
        },
    },
]

TOOL_HANDLERS: dict[str, Any] = {
    "get_algo_market": _tool_get_algo_market,
    "get_chain_head": _tool_get_chain_head,
    "get_price_history": _tool_get_price_history,
    "recent_articles": _tool_recent_articles,
    "search_platform": _tool_search_platform,
    "search_crawled_pages": _tool_search_crawled_pages,
    "get_article": _tool_get_article,
    "source_history": _tool_source_history,
    "trending_articles": _tool_trending_articles,
    "review_draft": _tool_review_draft,
}


# Story lanes whose subject is a person/company under scrutiny — the only ones
# where entity-background OSINT (sanctions/registry/dockets/leaks) earns its
# schema budget. An empty topic (callers that predate lanes) keeps everything.
_ENTITY_OSINT_TOPICS = ("scam_alert", "editorial_assignment")


def all_tools(
    context: dict[str, Any] | None = None,
    *,
    topic: str = "",
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Writer tools plus, when enabled, the investigative OSINT and external
    research (web search + Bluesky) toolsets. ``context`` (service_id, source_url,
    model) tags any suggest_tool calls with the story they came from. ``topic``
    (a PublishTopic value) lane-gates entity-background OSINT tools to
    investigative stories; empty means ungated."""
    schemas = list(TOOL_SCHEMAS)
    handlers = dict(TOOL_HANDLERS)
    schemas.append(SUGGEST_TOOL_SCHEMA)
    handlers["suggest_tool"] = _make_suggest_tool_handler(context)
    try:
        from app.core.config import INVESTIGATIVE_TOOLS_ENABLED

        if INVESTIGATIVE_TOOLS_ENABLED:
            from app.modules.ai.investigative_tools import investigative_tools

            inv_schemas, inv_handlers = investigative_tools(
                include_entity_osint=(not topic or topic in _ENTITY_OSINT_TOPICS)
            )
            schemas.extend(inv_schemas)
            handlers.update(inv_handlers)
    except Exception:
        logger.warning("failed to load investigative tools", exc_info=True)
    try:
        from app.modules.ai.research_tools import research_tools

        research_schemas, research_handlers = research_tools()
        schemas.extend(research_schemas)
        handlers.update(research_handlers)
    except Exception:
        logger.warning("failed to load research tools", exc_info=True)
    try:
        from app.modules.ai.chain_tools import chain_tools

        chain_schemas, chain_handlers = chain_tools()
        schemas.extend(chain_schemas)
        handlers.update(chain_handlers)
    except Exception:
        logger.warning("failed to load chain tools", exc_info=True)
    return schemas, handlers
