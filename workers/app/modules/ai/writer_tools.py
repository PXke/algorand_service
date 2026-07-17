"""Agentic tools the Mistral writer can call on demand while composing.

Each tool is a (schema, handler) pair. Handlers read live platform data and
must be cheap and failure-tolerant — a tool error returns {"error": ...} and
never aborts the article.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.modules.ai.chart_tools import CHART_DATA_SCHEMA, _tool_chart_data

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
    """Daily-ish ALGO price series (x=dates, y=USD price).

    Prefer ``chart_data(dataset='algo_price')`` when you need a ```chart block
    for the article — this returns raw x/y for inline prose only."""
    from app.modules.ai.chart_tools import algo_price_series

    pts = algo_price_series(days=days)
    if not pts:
        return {"error": "no price samples available", "x": [], "y": [], "points": 0}
    labels, prices = zip(*pts, strict=True)
    return {"x": list(labels), "y": list(prices), "points": len(labels)}


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
    """Self-assessment: schema heuristic + LLM rubric (narrative/depth)."""
    from app.modules.newspaper.article_grader import grade_article_draft
    from app.modules.newspaper.article_quality_llm import grade_article_quality_llm

    try:
        review = grade_article_draft(title=title, body=body)
        review["quality"] = grade_article_quality_llm(title=title, body=body)
        return review
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
            "for forum activity AND forum search (query param), search_bluesky for "
            "community sentiment (use this "
            "instead of X/Twitter), medium_api_article_list for a blog's article list, "
            "reddit_api_post_history for a user's Reddit history, xgov_proposal_status "
            "for xGov grant proposal status, fetch_archive_text to "
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

REPORT_COMPOSE_ISSUE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "report_compose_issue",
        "description": (
            "Surface a problem with the compose pipeline so engineers can improve it — "
            "confusing or contradictory PROMPT instructions, thin/stale/misleading "
            "SOURCE DATA you were handed, an existing TOOL that misbehaved or returned "
            "unusable results, or broader PIPELINE friction (two-stage handoff, digest "
            "quality, missing context). Use this for operational feedback about what "
            "you already have; if you need a capability that does not exist at all, "
            "call suggest_tool instead. Returns no story data — call when you hit real "
            "friction, then keep researching or writing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": ["prompt", "source_data", "tool", "pipeline", "other"],
                    "description": (
                        "prompt = instructions confusing/contradictory; "
                        "source_data = scraped/handoff material bad; "
                        "tool = registered tool misbehaved; "
                        "pipeline = process/handoff issue; other = none of the above"
                    ),
                },
                "summary": {
                    "type": "string",
                    "description": "One-line headline of the issue (specific, actionable)",
                },
                "detail": {
                    "type": "string",
                    "description": (
                        "What happened, what you expected, and how it blocked or "
                        "degraded this story (concrete examples help)"
                    ),
                },
                "severity": {
                    "type": "string",
                    "enum": ["low", "medium", "high"],
                    "description": (
                        "high = could not verify a key claim or nearly wrote fiction; "
                        "medium = meaningful workaround needed; low = minor annoyance"
                    ),
                },
                "related_tool": {
                    "type": "string",
                    "description": (
                        "When category is tool, the tool name involved (e.g. fetch_url)"
                    ),
                },
            },
            "required": ["category", "summary"],
        },
    },
}


# Tokens too generic to identify a capability on their own — "api", "search",
# "lookup" etc. appear in half the suggestions and would match everything.
_GENERIC_TOKENS = frozenset(
    {
        "api",
        "tool",
        "search",
        "lookup",
        "get",
        "fetch",
        "data",
        "status",
        "history",
        "list",
        "full",
        "text",
        "machine",
        "check",
        "tracker",
        "documentation",
        "algo",
        "algorand",
        "onchain",
    }
)

# Suggested-name vocabulary → the registered tool that covers it, for cases
# token overlap can't catch (prod suggestions asked for "wayback_machine_*"
# while the tool family is named fetch_archive_*).
_CAPABILITY_ALIASES = {
    "wayback": "fetch_archive_text",
    "archive": "fetch_archive_text",
    "twitter": "search_bluesky",
    "x": "search_bluesky",
    "chart": "chart_data",
    "plot": "chart_data",
}


def _match_existing_tool(capability: str, known_tools: set[str]) -> str | None:
    """Best-effort map of a suggested capability onto an already-registered tool.

    The writer keeps suggesting tools it already has (~30 schemas in context and
    it loses track — prod asked for reddit_api_post_history, discourse_forum and
    medium_api_article_list, all long since registered). Conservative on
    purpose: exact name, alias vocabulary, or a shared non-generic token."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", (capability or "").lower()) if t]
    if not tokens:
        return None
    name = "_".join(tokens)
    if name in known_tools:
        return name
    for tok in tokens:
        alias = _CAPABILITY_ALIASES.get(tok)
        if alias and alias in known_tools:
            return alias
    significant = {t for t in tokens if t not in _GENERIC_TOKENS}
    best: tuple[int, str] | None = None
    for tool in known_tools:
        overlap = significant & {
            t for t in tool.lower().split("_") if t not in _GENERIC_TOKENS
        }
        if overlap and (best is None or len(overlap) > best[0]):
            best = (len(overlap), tool)
    return best[1] if best else None


def _make_suggest_tool_handler(
    context: dict[str, Any] | None, known_tools: set[str] | None = None
):
    ctx = context or {}
    known = known_tools or set()

    def _handler(capability: str = "", reason: str = "") -> dict[str, Any]:
        from app.modules.ai.tool_insights_store import record_tool_suggestion

        existing = _match_existing_tool(capability, known)
        if existing and existing != "suggest_tool":
            # Nudge instead of record: the correction reaches the model while it
            # can still act on it this session, and the insights table stays
            # free of already-covered asks.
            return {
                "ok": False,
                "already_available": existing,
                "hint": f"you already have this capability — call {existing} now "
                "instead of suggesting it",
            }
        record_tool_suggestion(
            capability,
            reason,
            service_id=str(ctx.get("service_id", "")),
            source_url=str(ctx.get("source_url", "")),
            model=str(ctx.get("model", "")),
        )
        return {"ok": True, "noted": (capability or "").strip()}

    return _handler


def _make_report_compose_issue_handler(context: dict[str, Any] | None):
    ctx = context or {}

    def _handler(
        category: str = "",
        summary: str = "",
        detail: str = "",
        severity: str = "medium",
        related_tool: str = "",
    ) -> dict[str, Any]:
        from app.modules.ai.tool_insights_store import record_compose_feedback

        cat = (category or "").strip().lower()
        headline = (summary or "").strip()
        if not headline:
            return {"ok": False, "error": "summary required"}
        if cat not in {"prompt", "source_data", "tool", "pipeline", "other"}:
            return {
                "ok": False,
                "error": "invalid category — use prompt, source_data, tool, pipeline, or other",
            }
        saved = record_compose_feedback(
            category=cat,
            summary=headline,
            detail=detail,
            severity=severity,
            related_tool=related_tool,
            service_id=str(ctx.get("service_id", "")),
            source_url=str(ctx.get("source_url", "")),
            model=str(ctx.get("model", "")),
        )
        if not saved:
            return {"ok": False, "error": "could not record feedback"}
        return {
            "ok": True,
            "noted": headline[:120],
            "category": cat,
            "hint": "continue researching or writing — this report does not block you",
        }

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
                "ALGO daily price series (x=dates, y=USD) as raw arrays. For an "
                "article chart, call chart_data(dataset='algo_price') instead — it "
                "returns the validated ```chart fence."
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

TOOL_SCHEMAS.append(CHART_DATA_SCHEMA)

TOOL_HANDLERS: dict[str, Any] = {
    "get_algo_market": _tool_get_algo_market,
    "get_chain_head": _tool_get_chain_head,
    "get_price_history": _tool_get_price_history,
    "chart_data": _tool_chart_data,
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
    schemas.append(REPORT_COMPOSE_ISSUE_SCHEMA)
    schemas.append(SUGGEST_TOOL_SCHEMA)
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
    try:
        from app.modules.ai.story_spike import SPIKE_STORY_SCHEMA, spike_story_handler

        schemas.append(SPIKE_STORY_SCHEMA)
        handlers["spike_story"] = spike_story_handler
    except Exception:
        logger.warning("failed to load spike_story tool", exc_info=True)
    # Registered last, once every toolset is merged, so the already-have-it
    # check sees the FULL tool registry for this compose.
    handlers["report_compose_issue"] = _make_report_compose_issue_handler(context)
    handlers["suggest_tool"] = _make_suggest_tool_handler(context, set(handlers))
    if "fetch_url" in handlers:
        handlers["fetch_url"] = _wrap_fetch_url_enqueue(
            _wrap_fetch_url_scroll(handlers["fetch_url"], context),
            context,
        )
    return schemas, handlers


def _canonical_fetch_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    return u


def _wrap_fetch_url_scroll(handler: Any, context: dict[str, Any] | None):
    """Track per-compose scroll position so the model only passes continue_reading."""
    ctx = context or {}
    offsets: dict[str, int] = ctx.setdefault("_fetch_url_offsets", {})
    window_caps: dict[str, int] = ctx.setdefault("_fetch_url_window_caps", {})

    def _wrapped(**kwargs: Any) -> dict[str, Any]:
        from app.modules.ai.research_tools import _fetch_url_internal, _publicize_fetch_result

        url = _canonical_fetch_url(str(kwargs.get("url") or ""))
        if not url:
            return {"error": "url required"}
        continue_reading = bool(kwargs.get("continue_reading"))
        max_chars = kwargs.get("max_chars") or 6000
        if continue_reading:
            offset = offsets.get(url, 0)
            max_chars = window_caps.get(url, max_chars)
        else:
            offset = 0
            offsets.pop(url, None)
            window_caps[url] = max(500, min(int(max_chars), 12000))
        raw = _fetch_url_internal(url, max_chars=max_chars, offset=offset)
        if raw.get("error"):
            return raw
        next_offset = raw.get("_next_offset")
        if raw.get("has_more") and next_offset is not None:
            offsets[url] = int(next_offset)
        else:
            offsets.pop(url, None)
            window_caps.pop(url, None)
        return _publicize_fetch_result(raw)

    return _wrapped


def _wrap_fetch_url_enqueue(handler: Any, context: dict[str, Any] | None):
    """After fetch_url returns, queue the canonical URL for a full crawl."""
    ctx = context or {}

    def _wrapped(**kwargs: Any) -> dict[str, Any]:
        result = handler(**kwargs)
        try:
            from app.modules.crawler.writer_fetch_enqueue import maybe_enqueue_writer_fetched_url

            maybe_enqueue_writer_fetched_url(
                result,
                is_continuation=bool(kwargs.get("continue_reading")),
                compose_source=str(ctx.get("source_url", "")),
                service_id=str(ctx.get("service_id", "")),
            )
        except Exception:
            logger.debug("writer fetch enqueue hook failed", exc_info=True)
        return result

    return _wrapped
