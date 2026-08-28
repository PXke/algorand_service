"""External research tools the writer can call on demand.

- search_web: general web research via self-hosted SearXNG (no Google, no key,
  no per-query cost). SEARXNG_URL is operator-configured and trusted, so it is
  called directly; any RESULT url the model later fetches still goes through the
  SSRF-guarded fetch tool.
- search_bluesky: free public Bluesky post search for community sentiment.
  Public AppView needs no auth. Bluesky is a public host, so it rides the SSRF
  guard like any other untrusted fetch.
- search_x: reads this week's scheduled X (Twitter) search sweep, opt-in
  (X_SEARCH_ENABLED required to register). Redesigned 2026-08-25 from a live
  per-compose API call into a read against a weekly Cassandra cache
  (x_search_weekly, populated by x_search_sweep.py's Celery beat task) --
  compose no longer spends money on this at all. See config.X_SEARCH_ENABLED's
  comment for the full picture, and _x_search_live below for the one place
  that still calls X's API (the sweep, never the writer).

Every handler is failure-tolerant: an error returns {"error": ...} and never
aborts the article.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

    from app.modules.newspaper.x_search_store import XSearchSnapshot

logger = logging.getLogger(__name__)

_UA = "algorand-platform-newspaper/1.0 (+https://algorand.pxke.me)"
# searchPosts requires an authenticated session (the public AppView 403s it), so
# we mint an app-password session against the entryway and call it with a Bearer.
_BSKY_CREATE_SESSION = "https://bsky.social/xrpc/com.atproto.server.createSession"
_BSKY_SEARCH = "https://bsky.social/xrpc/app.bsky.feed.searchPosts"
_bsky_token_cache: dict[str, float | str] = {}


def _tool_search_web(query: str, limit: int = 6) -> dict[str, Any]:
    """General web search via SearXNG: titles, URLs and snippets a journalist would skim before writing. Use to discover sources and context you were not handed; then fetch the most relevant URL with the safe fetch tool. Also queries news-specific engines (Bing News, DuckDuckGo News, Google News) for a real publish-date signal — general engines rarely return one at all."""
    from app.core.config import SEARXNG_URL
    from app.core.http_client import get_http_client

    if not SEARXNG_URL:
        return {"query": query, "error": "web search not configured", "results": []}
    q = (query or "").strip()
    if not q:
        return {"query": query, "results": []}
    n = max(1, min(int(limit), 12))
    try:
        resp = get_http_client(timeout=12.0).get(
            f"{SEARXNG_URL}/search",
            params={
                "q": q,
                "format": "json",
                "categories": "general,news",
                "language": "en",
            },
            headers={"User-Agent": _UA},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"query": query, "error": str(exc)[:200], "results": []}
    # News engines (Bing/DuckDuckGo/Google News) carry a real publish date;
    # general engines (Bing/DuckDuckGo web) almost never do — surface whichever
    # results actually have one first, so a freshness-sensitive story doesn't
    # lose its few dated hits to the 12-result truncation below.
    ranked = sorted(data.get("results") or [], key=lambda r: 0 if r.get("publishedDate") else 1)
    results = [
        {
            "title": (r.get("title") or "")[:200],
            "url": r.get("url") or "",
            "snippet": (r.get("content") or "")[:300],
            "published_date": r.get("publishedDate") or None,
        }
        for r in ranked[:n]
    ]
    out: dict[str, Any] = {"query": query, "count": len(results), "results": results}
    # SearXNG's own query-refinement hints (e.g. a likely spelling correction
    # or a related term) — previously fetched and silently discarded. Surface
    # them so a query that returns few/no results can be retried smarter
    # instead of the model guessing blind at a rephrase.
    suggestions = [str(s)[:100] for s in (data.get("suggestions") or [])][:5]
    if suggestions:
        out["suggestions"] = suggestions
    return out


def _bsky_access_token() -> tuple[str, str]:
    """App-password session token, cached ~50 min, as (token, error_message). Both empty when Bluesky simply isn't configured (no credentials set) -- that's a real, non-error state. A non-empty error_message means credentials ARE set but minting a session actually failed (bad password, network error, Bluesky outage); the caller must not conflate that with "not configured", or a real outage silently reads as a deliberate no-op."""
    import os
    import time

    import httpx

    ident = os.getenv("BLUESKY_IDENTIFIER", "").strip()
    pw = os.getenv("BLUESKY_APP_PASSWORD", "").strip()
    if not ident or not pw:
        return "", ""
    cached = _bsky_token_cache.get("token")
    expires = _bsky_token_cache.get("expires", 0.0)
    if isinstance(cached, str) and isinstance(expires, float) and time.time() < expires:
        return cached, ""
    try:
        resp = httpx.post(
            _BSKY_CREATE_SESSION,
            json={"identifier": ident, "password": pw},
            headers={"User-Agent": _UA},
            timeout=12.0,
        )
        resp.raise_for_status()
        token = str(resp.json().get("accessJwt") or "")
    except Exception as exc:
        return "", str(exc)[:200]
    if token:
        _bsky_token_cache["token"] = token
        _bsky_token_cache["expires"] = time.time() + 3000.0
    return token, ""


def _tool_search_bluesky(query: str, limit: int = 10) -> dict[str, Any]:
    """Recent public Bluesky posts matching a query — community sentiment and discussion. Returns post text + engagement so the writer judges the mood; a post is social opinion, never cited as established fact.

    Uses `_guarded_get_with_retry` (2026-08-28) rather than a bare `guarded_get`
    call: root-caused live on a real compose (Lumi Rogue recompose,
    session 957f895a) where both attempts got a straight `502 Bad Gateway`
    from bsky.social with zero retry, silently losing the community-
    sentiment angle for a first-coverage story. 502 is in
    `_FETCH_RETRYABLE_STATUS`, the same policy every other external-API tool
    in this module already gets.
    """
    q = (query or "").strip()
    if not q:
        return {"query": query, "posts": []}
    token, token_error = _bsky_access_token()
    if not token:
        return {
            "query": query,
            "error": token_error or "bluesky not configured",
            "posts": [],
        }
    n = max(1, min(int(limit), 25))
    try:
        resp = _guarded_get_with_retry(
            _BSKY_SEARCH,
            params={"q": q, "limit": n, "sort": "top"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=12.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        # A stale token (~expired) — drop it so the next call re-auths.
        _bsky_token_cache.pop("token", None)
        return {"query": query, "error": str(exc)[:200], "posts": []}
    posts = []
    for p in (data.get("posts") or [])[:n]:
        record = p.get("record") or {}
        author = p.get("author") or {}
        handle = author.get("handle", "")
        uri = p.get("uri", "")
        rkey = uri.rsplit("/", 1)[-1] if uri else ""
        url = f"https://bsky.app/profile/{handle}/post/{rkey}" if handle and rkey else ""
        posts.append(
            {
                "author": handle,
                "text": (record.get("text") or "")[:300],
                "likes": p.get("likeCount", 0),
                "reposts": p.get("repostCount", 0),
                "replies": p.get("replyCount", 0),
                "url": url,
            }
        )
    return {"query": query, "count": len(posts), "posts": posts}


_X_SEARCH_BASE = "https://api.x.com/2/tweets/search/recent"
# X bills per RESULT RETURNED (pay-as-you-go, $0.005/resource quoted
# 2026-08-21), charged the moment X's API sends it back -- truncating text
# in the response does NOT reduce cost, only max_results (how many posts we
# ask for) and the call count do. Fixed at X's own API minimum for
# recent-search and deliberately not model-adjustable, so each sweep call's
# cost is small and predictable ($0.05/call at 10 results). See
# config.X_SEARCH_ENABLED's comment for the full control picture.
_X_SEARCH_MAX_RESULTS = 10

# Tokens too generic to identify a specific tracked service on their own --
# stripped before matching a writer's free-text query against a stored
# snapshot's display_name/service_id, same reasoning as writer_tools.py's
# _GENERIC_TOKENS (a shared "algorand" alone would false-match nearly every
# tracked service).
_X_MATCH_STOPWORDS = frozenset({"the", "a", "an", "and", "of", "for", "on", "algorand", "algo"})


def _x_search_live(query: str) -> dict[str, Any]:
    """Live X (Twitter) recent-search call -- used ONLY by the weekly sweep task (x_search_sweep.py), never at compose/writer time. See _tool_search_x for the compose-time reader that serves cached weekly results instead of calling X directly."""
    from app.core.config import X_BEARER_TOKEN, X_SEARCH_ENABLED
    from app.core.net_guard import guarded_get

    q = (query or "").strip()
    if not q:
        return {"query": query, "posts": []}
    if not X_SEARCH_ENABLED or not X_BEARER_TOKEN:
        return {"query": query, "error": "X search not configured", "posts": []}
    try:
        resp = guarded_get(
            _X_SEARCH_BASE,
            params={
                "query": q,
                "max_results": _X_SEARCH_MAX_RESULTS,
                "tweet.fields": "author_id,created_at,public_metrics",
            },
            headers={"User-Agent": _UA, "Authorization": f"Bearer {X_BEARER_TOKEN}"},
            timeout=12.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"query": query, "error": str(exc)[:200], "posts": []}
    posts = []
    for p in (data.get("data") or [])[:_X_SEARCH_MAX_RESULTS]:
        metrics = p.get("public_metrics") or {}
        pid = p.get("id", "")
        posts.append(
            {
                # Full text, untruncated -- we're already billed for this
                # resource the moment X returned it; truncating here only
                # loses information for free, it doesn't save anything.
                "text": p.get("text") or "",
                "created_at": p.get("created_at"),
                "likes": metrics.get("like_count", 0),
                "reposts": metrics.get("retweet_count", 0),
                "replies": metrics.get("reply_count", 0),
                "url": f"https://x.com/i/web/status/{pid}" if pid else "",
            }
        )
    return {"query": query, "count": len(posts), "posts": posts}


def _tokenize_for_x_match(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t and t not in _X_MATCH_STOPWORDS}


def _best_x_search_match(
    query: str, snapshots: list[XSearchSnapshot]
) -> XSearchSnapshot | None:
    """Best-effort match of a writer's free-text query onto one tracked service's stored snapshot, by shared non-generic token overlap (same shape as writer_tools.py's _match_existing_tool) -- a service's snapshot rather than a full-text search index, since the sweep only ever covers known ecosystem services, not arbitrary topics. A token-subset match (e.g. query 'Folks Finance liquidations' containing display_name 'Folks Finance' whole) scores a bonus over plain overlap, since that's the strongest signal a query is actually about that service."""
    q_tokens = _tokenize_for_x_match(query)
    if not q_tokens:
        return None
    best = None
    best_score = 0
    for snap in snapshots:
        name_tokens = _tokenize_for_x_match(snap.display_name) | _tokenize_for_x_match(snap.service_id)
        if not name_tokens:
            continue
        overlap = q_tokens & name_tokens
        score = len(overlap)
        if score and (name_tokens <= q_tokens or q_tokens <= name_tokens):
            score += 1
        if score > best_score:
            best_score = score
            best = snap
    return best if best_score > 0 else None


def _tool_search_x(query: str) -> dict[str, Any]:
    """Search this week's swept X (Twitter) posts for a TRACKED Algorand ecosystem service -- results come from a weekly scheduled sweep of known services (service_registry), not a live arbitrary search, so this only ever covers ecosystem projects the newsroom already tracks. Treat results as social opinion/announcement, never cited as established fact on their own."""
    from app.core.config import X_SEARCH_ENABLED

    q = (query or "").strip()
    if not q:
        return {"query": query, "posts": []}
    if not X_SEARCH_ENABLED:
        return {"query": query, "error": "X search not configured", "posts": []}
    try:
        from app.modules.newspaper.x_search_store import list_snapshots

        snapshots = list_snapshots()
    except Exception as exc:
        return {"query": query, "error": str(exc)[:200], "posts": []}

    match = _best_x_search_match(q, snapshots)
    if match is None:
        result: dict[str, Any] = {
            "query": query,
            "error": (
                "no tracked service matches this query -- search_x now reads a "
                "weekly sweep of known Algorand ecosystem services rather than an "
                "arbitrary live search, so it can only answer for a project already "
                "in the service registry"
            ),
            "posts": [],
        }
        sample = sorted({s.display_name for s in snapshots if s.display_name})[:8]
        if sample:
            result["tracked_services_sample"] = sample
        return result

    posts = list(match.posts)[:_X_SEARCH_MAX_RESULTS]
    result = {
        "query": query,
        "matched_service": match.display_name or match.service_id,
        "swept_at": match.swept_at.isoformat() if match.swept_at else None,
        "count": len(posts),
        "posts": posts,
    }
    if match.error and not posts:
        result["sweep_error"] = match.error
    # Root-caused 2026-08-21 (HesabPay/Movement article): the writer cited a
    # single reply with 0 likes/0 reposts/0 replies as "Algorand community
    # members noticed" -- a real post, but the engagement numbers right next
    # to it never actually got weighed. A data-driven nudge (only fires when
    # every result genuinely IS low-engagement) is more reliable than a
    # static schema warning, since it responds to the actual numbers instead
    # of hoping the model remembers a general instruction.
    if posts and max((p.get("likes", 0) + p.get("reposts", 0) + p.get("replies", 0)) for p in posts) < 3:
        result["engagement_note"] = (
            "Every result has minimal engagement (under 3 combined likes/reposts/"
            "replies). Cite a specific post only if its CONTENT is genuinely useful "
            "to the article -- don't cite one just because it exists. If you do cite "
            "one, attribute it to that one account, not to 'the community' or "
            "'users' -- near-zero engagement is not evidence of a broader reaction."
        )
    return result


_WEB_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": (
            "General + news web search (SearXNG) — titles, URLs and snippets to "
            "discover sources and context you were not handed. Use this first when "
            "you need to research a topic; then fetch the best URL with the safe "
            "fetch tool. Results with a real published_date (from news engines) are "
            "returned first — use that date, never a guess, when a result's "
            "recency matters to the story. A response with few/no results may "
            "include `suggestions` (alternate phrasings) — try one of those before "
            "giving up on the query."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "1-12, default 6"},
            },
            "required": ["query"],
        },
    },
}

_BLUESKY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_bluesky",
        "description": (
            "Search recent public Bluesky posts for community sentiment/discussion "
            "on a topic (free, no Twitter/X). Use ONLY when the story's value "
            "depends on what the community is saying; summarize the mood and treat "
            "posts as social opinion, not fact."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "1-25, default 10"},
            },
            "required": ["query"],
        },
    },
}

_X_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_x",
        "description": (
            "Recent public X (Twitter) posts about a TRACKED Algorand ecosystem "
            "service -- many projects announce primarily on X rather than Bluesky. "
            "Backed by a weekly scheduled sweep of known services (not a live "
            "search), so this only answers for a project already tracked by the "
            "newsroom -- name the service/project directly in the query (e.g. its "
            "product name) rather than a generic topic. Free to call (no live API "
            "cost), but still capped per session since a miss doesn't get better by "
            "rephrasing the query many times. Up to 10 most-recent matching posts "
            "from the last sweep, each with likes/reposts/replies -- cite a "
            "specific post only when it is genuinely useful to the article, and "
            "check its engagement before framing it as a reaction: a single reply "
            "with 0 likes/0 reposts is one account's opinion, not 'the community' "
            "or 'users'. Treat results as social opinion/announcement, never cited "
            "as established fact on their own."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
}


def _guarded_get(
    url: str, *, headers: dict | None = None, params: dict | None = None, timeout: float = 12.0
) -> httpx.Response:
    """SSRF-guarded GET for external / LLM-supplied URLs (revalidates each redirect)."""
    from app.core.net_guard import guarded_get

    h = {"User-Agent": _UA}
    if headers:
        h.update(headers)
    return guarded_get(url, headers=h, params=params, timeout=timeout)


_FETCH_MAX_ATTEMPTS = 5
_FETCH_BACKOFF_BASE_SECONDS = 2.0
_FETCH_BACKOFF_MAX_SECONDS = 60.0
_FETCH_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _fetch_backoff_seconds(attempt: int, resp: httpx.Response | None = None) -> float:
    """Backoff before retrying after `attempt` (0-based) fails. A 429 means the server is actively throttling us, so it honors Retry-After when sent and otherwise backs off harder than the plain exponential schedule used for transient network errors / 5xx."""
    if resp is not None and getattr(resp, "status_code", None) == 429:
        from app.modules.ai.llm_openai_compatible import _retry_after_seconds

        retry_after = _retry_after_seconds(resp)
        if retry_after is not None:
            return min(_FETCH_BACKOFF_MAX_SECONDS, retry_after)
        return min(_FETCH_BACKOFF_MAX_SECONDS, _FETCH_BACKOFF_BASE_SECONDS * (3**attempt))
    return min(_FETCH_BACKOFF_MAX_SECONDS, _FETCH_BACKOFF_BASE_SECONDS * (2**attempt))


def _guarded_get_with_retry(
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: float = 12.0,
) -> httpx.Response:
    """`_guarded_get` with retry: transient network errors and 429/5xx responses get up to 5 attempts with exponential backoff, capped at 60s per wait (429 backs off harder, honoring Retry-After when the server sends one). SSRF rejections and real 4xx responses are permanent, so they fail immediately."""
    import time

    from app.core.net_guard import UnsafeUrlError

    resp = None
    last_exc: Exception | None = None
    for attempt in range(_FETCH_MAX_ATTEMPTS):
        try:
            resp = _guarded_get(url, headers=headers, params=params, timeout=timeout)
        except UnsafeUrlError:
            raise
        except Exception as exc:
            last_exc = exc
            resp = None
            if attempt == _FETCH_MAX_ATTEMPTS - 1:
                raise
            time.sleep(_fetch_backoff_seconds(attempt))
            continue
        if resp.status_code in _FETCH_RETRYABLE_STATUS and attempt < _FETCH_MAX_ATTEMPTS - 1:
            time.sleep(_fetch_backoff_seconds(attempt, resp))
            continue
        return resp
    if resp is not None:
        return resp
    raise last_exc  # pragma: no cover - unreachable, loop always returns or raises


def _guarded_post(
    url: str,
    *,
    json: Any = None,  # noqa: ANN401 -- arbitrary JSON POST body
    headers: dict | None = None,
    timeout: float = 12.0,
) -> httpx.Response:
    """SSRF-guarded POST for a known external JSON API. Validates the host is public and does NOT follow redirects (so it can't be bounced to an internal one). Used for fixed endpoints we choose, not LLM-supplied URLs."""
    from app.core.http_client import get_http_client
    from app.core.net_guard import assert_public_url

    assert_public_url(url)
    h = {"User-Agent": _UA, "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    return get_http_client(timeout=timeout, follow_redirects=False).post(url, json=json, headers=h)


def _github_get(
    url: str, *, params: dict | None = None, timeout: float | None = None
) -> httpx.Response:
    """GET against the GitHub API with GITHUB_TOKEN when set — but never let a dead token take a tool down. GitHub answers 401 to ANY request carrying a revoked/expired token, while the same request unauthenticated succeeds (just rate-limited harder). Root-caused 2026-07-16: the prod token expired and github_repository_search started returning '401 Unauthorized' verbatim into research traces; on 401-with-token this logs loudly and retries once without the Authorization header."""
    import os

    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    kwargs: dict[str, Any] = {"params": params, "headers": headers}
    if timeout is not None:
        kwargs["timeout"] = timeout
    resp = _guarded_get(url, **kwargs)
    if resp.status_code == 401 and token:
        logger.warning(
            "GITHUB_TOKEN was rejected (expired/revoked?) — retrying %s unauthenticated",
            url,
        )
        headers.pop("Authorization", None)
        resp = _guarded_get(url, **kwargs)
    return resp


_GITHUB_OWNER_STARS_PAGE_CAP = 3  # up to 300 repos scanned for the star total, so one huge org can't turn this into dozens of requests


def _github_owner_total_stars(owner: str, total_repos: int | None) -> tuple[int | None, bool]:
    """Sum stargazers_count across an owner's repos, paginated up to _GITHUB_OWNER_STARS_PAGE_CAP pages.

    Returns (total_stars, complete) — complete is False if the owner has more
    repos than the page cap covers, so a caller can tell "the real total" from
    "a lower bound" the same way get_asset_transaction_volume's page cap does.
    """
    total = 0
    checked = 0
    for page in range(1, _GITHUB_OWNER_STARS_PAGE_CAP + 1):
        try:
            resp = _github_get(
                f"https://api.github.com/users/{owner}/repos",
                params={"per_page": 100, "page": page},
            )
            resp.raise_for_status()
            page_repos = resp.json()
        except Exception:
            return None, False
        if not page_repos:
            break
        total += sum(r.get("stargazers_count", 0) or 0 for r in page_repos if isinstance(r, dict))
        checked += len(page_repos)
        if len(page_repos) < 100:
            break
    complete = total_repos is None or checked >= total_repos
    return total, complete


def _github_owner_repos(owner: str) -> dict[str, Any]:
    """Repo list for a GitHub org/user, most recently pushed first — returned when the model passes an owner instead of owner/name (the top prod failure mode for this tool), so it can pick a repo and call again instead of dead-ending.

    total_public_repos / total_stars_across_all_repos (added 2026-08-10,
    root-caused live): the 8-repo 'repos' list below is sorted by recency, not
    stars, so a real org-wide claim like "carries no stars" silently drew on
    only the 8 MOST RECENTLY PUSHED repos out of many more -- a live incident
    (chopmob-cloud/AlgoVoi) had exactly this happen: the 8-repo window
    genuinely showed 0 stars each, while 4 different, less-recently-touched
    repos elsewhere in the org's 112 total had 1 star each. Without an
    explicit org-wide total, "0 in the sample" reads as "0 for the org."
    """
    try:
        resp = _github_get(
            f"https://api.github.com/users/{owner}/repos",
            params={"sort": "pushed", "per_page": 8},
        )
        if resp.status_code == 404:
            return {"owner": owner, "error": "owner not found on GitHub"}
        resp.raise_for_status()
        repos = resp.json()
    except Exception as exc:
        return {"owner": owner, "error": str(exc)[:200]}

    # The org-wide total/star-aggregate lookups below are enrichment, not the
    # core "list repos to pick from" contract above -- a failure here (or a
    # response shape a test double doesn't model) must degrade to None, never
    # take down a call that would otherwise have succeeded.
    total_repos: int | None = None
    try:
        profile_resp = _github_get(f"https://api.github.com/users/{owner}")
        profile_resp.raise_for_status()
        data = profile_resp.json()
        if isinstance(data, dict):
            total_repos = data.get("public_repos")
    except Exception:
        logger.debug("github owner profile lookup failed for %s", owner, exc_info=True)

    total_stars, stars_complete = _github_owner_total_stars(owner, total_repos)

    return {
        "owner": owner,
        "total_public_repos": total_repos,
        "total_stars_across_all_repos": total_stars,
        "total_stars_may_be_incomplete": not stars_complete,
        "repos": [
            {
                "repo": r.get("full_name"),
                "description": (r.get("description") or "")[:160],
                "stars": r.get("stargazers_count"),
                "pushed_at": r.get("pushed_at"),
                "archived": bool(r.get("archived")),
            }
            for r in repos
            if isinstance(r, dict)
        ][:8],
        "hint": (
            f"'repos' is only the 8 MOST RECENTLY PUSHED of {total_repos} total "
            "public repos, sorted by recency not stars -- do NOT generalize star "
            "counts, activity, or adoption from this list alone to the whole "
            "organisation. total_stars_across_all_repos is the real org-wide "
            "aggregate (see total_stars_may_be_incomplete for very large orgs). "
            "Call github_activity again with one of these 'owner/name' repos "
            "for full detail on one."
        ),
    }


def _owner_liveness(owner: str, *, exclude: str = "", recent_days: int = 120) -> dict[str, Any]:
    """Is the OWNER still shipping code in NON-archived repos? This is the liveness signal a single archived repo cannot give.

    Root cause of a mis-published article (2026-07-20): the writer saw
    `perawallet/pera-wallet` was archived and declared Pera Wallet — the most-used
    Algorand wallet — defunct, telling readers to migrate away. But the SAME owner
    had `perawallet/pera-react-native` pushed that very day: the repo was
    superseded, not the product discontinued. A domain check can't catch this (the
    site resolves fine); the owner's other repos are the tell.
    """
    from datetime import UTC, datetime, timedelta

    listing = _github_owner_repos(owner)
    repos = listing.get("repos") or []
    cutoff = datetime.now(UTC) - timedelta(days=recent_days)
    active: list[dict[str, Any]] = []
    for r in repos:
        if r.get("archived"):
            continue
        if exclude and (r.get("repo") or "").lower() == exclude.lower():
            continue
        pushed = r.get("pushed_at") or ""
        try:
            dt = datetime.fromisoformat(pushed.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt >= cutoff:
            active.append({"repo": r.get("repo"), "pushed_at": pushed, "stars": r.get("stars")})
    active.sort(key=lambda x: x["pushed_at"] or "", reverse=True)
    if active:
        verdict = (
            f"OWNER STILL ACTIVE: '{owner}' has {len(active)} non-archived repo(s) "
            f"pushed in the last {recent_days} days (most recent: {active[0]['repo']} "
            f"@ {active[0]['pushed_at']}). An archived repo under this owner is most "
            "likely SUPERSEDED or migrated, NOT the project being discontinued — do "
            "NOT report the project as defunct or tell users to migrate away without "
            "first checking the active repo(s) above."
        )
    else:
        verdict = (
            f"'{owner}' shows no recently-pushed non-archived repos either — the "
            "project may genuinely be dormant, but confirm via its site/announcements "
            "before calling it defunct."
        )
    return {"owner": owner, "active_repos": active[:8], "verdict": verdict}


def _github_repo_metadata(slug: str) -> dict[str, Any]:
    """Repo description/stars/pushed_at/archived, plus owner-liveness when archived (an archived repo is NOT a dead project — check whether the owner is still shipping code elsewhere, so the writer doesn't over-conclude "defunct" from one archived repo, per the Pera Wallet incident 2026-07-20). On a 404, falls back to the owner's repo listing (a wrong guess under a real owner) or an error dict — either always carries an "error" key, the caller's signal to stop."""
    out: dict[str, Any] = {"repo": slug}
    try:
        meta_resp = _github_get(f"https://api.github.com/repos/{slug}")
        if meta_resp.status_code == 404:
            # A wrong repo guess under a real owner (prod: 'AlgoNode/algonode') —
            # surface the owner's actual repos rather than a dead end.
            owner = slug.split("/")[0]
            listing = _github_owner_repos(owner)
            if listing.get("repos"):
                listing["error"] = f"repo '{slug}' not found; owner's repos listed"
                return listing
            return {"repo": slug, "error": "repo not found"}
        meta_resp.raise_for_status()
        meta = meta_resp.json()
        out.update(
            description=meta.get("description"),
            stars=meta.get("stargazers_count"),
            pushed_at=meta.get("pushed_at"),
            archived=meta.get("archived"),
        )
        if meta.get("archived"):
            out["owner_liveness"] = _owner_liveness(slug.split("/")[0], exclude=slug)
    except Exception as exc:
        return {"repo": slug, "error": str(exc)[:200]}
    return out


def _github_releases(slug: str, n: int) -> list[dict[str, Any]]:
    try:
        rel = _github_get(
            f"https://api.github.com/repos/{slug}/releases",
            params={"per_page": n},
        ).json()
        return [
            {
                "name": x.get("name") or x.get("tag_name"),
                "tag": x.get("tag_name"),
                "published_at": x.get("published_at"),
                "notes": (x.get("body") or "")[:500],
            }
            for x in rel
            if isinstance(x, dict)
        ][:n]
    except Exception as exc:
        # An empty list here reads to the writer as "this repo has no
        # releases" ground truth, indistinguishable from a GitHub API/network
        # failure — surface the failure instead (per _tool_github_activity's
        # docstring: every branch of this helper must carry an "error" key).
        return [{"error": str(exc)[:200]}]


def _github_recent_commits(slug: str, n: int) -> list[dict[str, Any]]:
    try:
        commits = _github_get(
            f"https://api.github.com/repos/{slug}/commits",
            params={"per_page": n},
        ).json()
        return [
            {
                "message": (c.get("commit", {}).get("message") or "").splitlines()[0][:140],
                "date": c.get("commit", {}).get("author", {}).get("date"),
                "author": (c.get("author") or {}).get("login"),
            }
            for c in commits
            if isinstance(c, dict)
        ][:n]
    except Exception as exc:
        # See _github_releases: don't let an API failure read as "no commits".
        return [{"error": str(exc)[:200]}]


def _github_top_contributors(slug: str, n: int) -> list[dict[str, Any]]:
    """Top contributors by total commit count — who really built the project, a stronger "anonymous team" signal than the last few commit authors."""
    try:
        contributors = _github_get(
            f"https://api.github.com/repos/{slug}/contributors",
            params={"per_page": n},
        ).json()
        return [
            {"login": c.get("login"), "contributions": c.get("contributions")}
            for c in contributors
            if isinstance(c, dict)
        ][:n]
    except Exception as exc:
        # See _github_releases: don't let an API failure read as "no contributors".
        return [{"error": str(exc)[:200]}]


def _tool_github_activity(repo: str, limit: int = 5) -> dict[str, Any]:
    """Recent activity for a GitHub repo: metadata, latest releases and commits.

    Accepts 'owner/name' or a github.com URL; a bare owner/org lists its repos.
    """
    slug = (repo or "").strip().rstrip("/")
    if "github.com/" in slug:
        slug = slug.split("github.com/", 1)[1]
    slug = "/".join(slug.split("/")[:2])
    if slug.endswith(".git"):
        slug = slug[:-4]
    n = max(1, min(int(limit), 10))
    if "/" not in slug and slug:
        # An org/user, not a repo — list its repos instead of erroring.
        return _github_owner_repos(slug)
    if slug.count("/") != 1 or not all(slug.split("/")):
        return {"error": f"expected owner/name, got '{repo}'"}
    out = _github_repo_metadata(slug)
    if "error" in out:
        return out
    out["releases"] = _github_releases(slug, n)
    out["recent_commits"] = _github_recent_commits(slug, n)
    out["top_contributors"] = _github_top_contributors(slug, n)
    return out


def _tool_github_repository_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search ALL of GitHub for repos matching a keyword query — use this when github_activity's owner/repo guess 404s and you don't know the real owner (e.g. a project's site names it but not its GitHub org). Not scoped to one owner, unlike github_activity's owner-repo-listing fallback."""
    q = (query or "").strip()
    if not q:
        return {"error": "query must not be empty"}
    n = max(1, min(int(limit), 10))
    try:
        resp = _github_get(
            "https://api.github.com/search/repositories",
            params={"q": q, "per_page": n, "sort": "stars"},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"query": q, "error": str(exc)[:200]}
    items = data.get("items", []) if isinstance(data, dict) else []
    return {
        "query": q,
        "total_count": data.get("total_count") if isinstance(data, dict) else None,
        "results": [
            {
                "repo": r.get("full_name"),
                "description": (r.get("description") or "")[:160],
                "stars": r.get("stargazers_count"),
                "pushed_at": r.get("pushed_at"),
            }
            for r in items
            if isinstance(r, dict)
        ][:n],
    }


def _tool_search_token_listings(asset_id: int | str) -> dict[str, Any]:
    """Whether an Algorand ASA is actually listed/tradeable on the two biggest Algorand DEXs (Tinyman, Pact) — real liquidity, price, and 24h/7d volume in USD, or confirmation it's NOT listed anywhere. Use this instead of assuming a token trades just because it exists; a real supply with zero listings is itself a notable fact worth reporting. For a single cross-DEX aggregate price/volume/market-cap/TVL number instead of a per-DEX breakdown, use lookup_asset_market_data instead."""
    aid = str(asset_id).strip()
    if not aid.isdigit():
        return {"error": "asset_id must be a numeric ASA id"}
    out: dict[str, Any] = {"asset_id": int(aid)}
    try:
        resp = _guarded_get(f"https://mainnet.analytics.tinyman.org/api/v1/assets/{aid}/")
        if resp.status_code == 404:
            out["tinyman"] = {"listed": False}
        else:
            resp.raise_for_status()
            t = resp.json()
            out["tinyman"] = {
                "listed": True,
                "verified": t.get("is_verified"),
                "liquidity_usd": t.get("liquidity_in_usd"),
                "price_usd": t.get("price_in_usd"),
                "volume_24h_usd": t.get("last_day_volume_in_usd"),
                "volume_7d_usd": t.get("last_week_volume_in_usd"),
            }
    except Exception as exc:
        out["tinyman"] = {"error": str(exc)[:200]}
    try:
        # Pact's API silently ignores an unrecognized `asset_id` param and
        # returns its entire (currently ~3900-pool) unfiltered listing instead
        # of erroring — confirmed live 2026-07-14: a COMPX query returned
        # unrelated USDC/goUSD, ALGO/gALGO pools with count=3863, matching the
        # platform total. `primary_asset__on_chain_id` is the real filter (and,
        # despite the name, matches the asset on EITHER side of the pool —
        # verified live: it returns pools where the target is primary AND
        # pools where it's secondary, both under this one param).
        resp = _guarded_get(
            "https://api.pact.fi/api/pools",
            params={"primary_asset__on_chain_id": aid, "limit": 10},
        )
        resp.raise_for_status()
        pools = (resp.json() or {}).get("results", [])
        out["pact"] = {
            "listed": bool(pools),
            "pool_count": len(pools),
            "pools": [
                {
                    "pair": (
                        f"{(p.get('primary_asset') or {}).get('unit_name')}/"
                        f"{(p.get('secondary_asset') or {}).get('unit_name')}"
                    ),
                    "tvl_usd": (p.get("primary_asset") or {}).get("tvl_usd"),
                }
                for p in pools
                if isinstance(p, dict)
            ][:5],
        }
    except Exception as exc:
        out["pact"] = {"error": str(exc)[:200]}
    return out


def _fetch_failure_hint(url: str, error: str, *, status_code: int | None = None) -> str:
    """Steer the writer to the dedicated tool (or a different strategy) for fetches that failed in a known way.

    Prod transcripts show the model repeatedly fetch_url-ing medium.com (403) and
    reddit.com (403) while the purpose-built tools sit unused — a hint inside the
    error result is followed far more reliably than a schema description. The
    status_code checks come first since they're precise (host checks below are
    best-effort text matching that a 401/403/429/5xx would otherwise fall through).
    """
    from urllib.parse import urlsplit

    host = urlsplit(url).netloc.lower()
    if host.endswith("medium.com"):
        return (
            "medium.com blocks direct fetches — use medium_api_article_list "
            "with the @handle or publication URL instead"
        )
    if host.endswith("reddit.com"):
        return (
            "reddit blocks all requests from this server — no reddit data is "
            "available; use Bluesky or forum search for community sentiment"
        )
    if host.endswith("github.com"):
        return (
            "for GitHub use github_activity (repo metadata/releases/commits) or "
            "github_repository_contents (read files) instead of fetching the page"
        )
    if status_code in (401, 403) or "401" in error or "403" in error:
        return (
            "the site refused this request (login-walled or bot-blocked), not "
            "necessarily unavailable — try fetch_archive_text for a cached copy "
            "instead of treating this as the page being gone"
        )
    if status_code == 429 or "429" in error:
        return (
            "rate-limited — do not retry this URL again this session; try "
            "fetch_archive_text or search_web for other sources instead"
        )
    if status_code in (500, 502, 503, 504) or any(c in error for c in ("500", "502", "503", "504")):
        return (
            "the site errored (likely transient) — one retry may help, "
            "otherwise fall back to fetch_archive_text or search_web"
        )
    if status_code in (404, 410) or "404" in error or "410" in error:
        return (
            "the page is gone — fetch_archive_text can read a Wayback Machine "
            "snapshot of it if the historical content matters. If you guessed "
            "this URL yourself (e.g. a bare /about or /terms path) rather than "
            "following a real <a href> on the page, this 404 does NOT prove a "
            "site's link or button is broken -- most single-page apps have no "
            "server route for guessed paths even when the on-page control "
            "works fine via JavaScript (root-caused 2026-08-10, recurred "
            "2026-08-12 on lumirogue.com's 'Terms of use'). Use click_element "
            "on the visible button/link text before reporting anything as a "
            "broken link"
        )
    low = error.lower()
    if (
        "dns resolution failed" in low
        or "name or service not known" in low
        or "nodename nor servname" in low
        or "no address associated with hostname" in low
    ):
        # The domain itself does not resolve — a much stronger signal than a 404
        # that the project is defunct/abandoned. A prod incident (2026-07-19) had
        # the writer recommend MyAlgo Wallet as a live wallet even though
        # myalgo.com failed to resolve mid-research: the terse error alone was
        # ignored, so state the implication as an instruction.
        return (
            "this domain does not resolve — the project is likely DEFUNCT or "
            "abandoned. Do NOT present it as an active, current, or recommended "
            "service and do not link it. If it still matters, use search_web to "
            "confirm whether it shut down, and if so say so explicitly"
        )
    return ""


def _slice_document_text(
    text: str,
    *,
    url: str,
    title: str,
    links: list[dict[str, str]],
    max_chars: int,
    offset: int = 0,
) -> dict[str, Any]:
    """Return one window of extracted page text with scroll navigation metadata."""
    cap = max(500, min(int(max_chars), 12000))
    offset = max(0, int(offset))
    total = len(text)
    if offset >= total:
        return {
            "url": url,
            "title": title,
            "text": "",
            "links": [],
            "chars": total,
            "chunk_chars": 0,
            "truncated": False,
            "has_more": False,
            "_next_offset": None,
            "hint": "no more content in this document",
        }
    end = min(offset + cap, total)
    chunk = text[offset:end]
    has_more = end < total
    return {
        "url": url,
        "title": title,
        "text": chunk,
        # Links only on the first window — repeating them on scroll rounds wastes tokens.
        "links": links if offset == 0 else [],
        "chars": total,
        "chunk_chars": len(chunk),
        "truncated": has_more,
        "has_more": has_more,
        "_next_offset": end if has_more else None,
    }


def _publicize_fetch_result(raw: dict[str, Any]) -> dict[str, Any]:
    """Strip internal scroll offsets; expose a model-friendly continue_reading hint."""
    out = dict(raw)
    out.pop("_next_offset", None)
    if out.get("has_more"):
        out["scroll"] = {
            "url": out.get("url"),
            "continue_reading": True,
            "instruction": (
                "More content remains. Call fetch_url again with the same url and "
                "continue_reading=true."
            ),
        }
    return out


def _fetch_url_error(u: str, exc: Exception, *, status_code: int | None = None) -> dict[str, Any]:
    """Build the {url, error, [status_code], [hint]} response for a failed fetch."""
    out: dict[str, Any] = {"url": u, "error": str(exc)[:200]}
    if status_code is not None:
        out["status_code"] = status_code
    hint = _fetch_failure_hint(u, out["error"], status_code=status_code)
    if hint:
        out["hint"] = hint
    return out


def _fetch_pdf_document(resp: Any, *, base: str, cap: int, offset: int) -> dict[str, Any]:  # noqa: ANN401 -- httpx.Response, kept loosely typed to match the caller
    """Extract text from a PDF response (whitepapers, audits, tokenomics docs are common source links) and slice it like any other fetched document."""
    try:
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(resp.content))
        md = reader.metadata
        title = (getattr(md, "title", None) or "")[:200] if md else ""
        text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:40]).strip()
    except Exception as exc:
        return {"url": base, "error": f"pdf parse failed: {str(exc)[:160]}"}
    return _slice_document_text(text, url=base, title=title, links=[], max_chars=cap, offset=offset)


_PDF_URL_IN_ATTR_RE = re.compile(r'(?:href|src)=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', re.I)
_GDOCS_VIEWER_URL_RE = re.compile(
    r"(?:docs\.google\.com/(?:viewer|gview)\?[^\"'<> ]*url=)([^\"'&<> ]+)", re.I
)


def _find_pdf_url_in_html(html: str, base: str) -> str | None:
    """Scan a page's HTML for the actual PDF file behind a JS-based viewer — a direct .pdf href/src anywhere in the markup, or a Google Docs/gview viewer's url= query param."""
    from urllib.parse import unquote, urljoin

    m = _GDOCS_VIEWER_URL_RE.search(html)
    if m:
        return unquote(m.group(1))
    m = _PDF_URL_IN_ATTR_RE.search(html)
    if m:
        return urljoin(base, m.group(1))
    return None


def _tool_extract_pdf_from_page(url: str) -> dict[str, Any]:
    """Find and read the actual PDF behind a page that embeds it in a JS-based viewer (Google Docs viewer, PDF.js, a slide-deck embed) instead of linking it directly.

    fetch_url only reads the wrapper page's own chrome/text in that case
    (self-reported gap, 2026-08-10: CGAP's 62-slide 'Stablecoins in
    Humanitarian Cash Transfers' deck rendered via a JS viewer with no
    accessible download URL on its landing page, so its exact wording could
    only be attested secondhand). Tries the page's raw HTML first, then --
    since some viewers inject the file only via client-side JS -- a
    Playwright-rendered copy. Once a direct PDF URL is found, extracts up to
    40 pages of text the same way fetch_url does for a URL that's already a
    direct PDF link.
    """
    u = (url or "").strip()
    if not u:
        return {"error": "url required"}
    if not u.startswith(("http://", "https://")):
        u = "https://" + u

    pdf_url: str | None = None
    try:
        resp = _guarded_get_with_retry(u, headers={"Accept": "text/html"}, timeout=15.0)
        resp.raise_for_status()
        if "pdf" in resp.headers.get("content-type", "").lower():
            return _fetch_pdf_document(resp, base=str(resp.url), cap=8000, offset=0)
        pdf_url = _find_pdf_url_in_html(resp.text, str(resp.url))
    except Exception as exc:
        return {"url": u, "error": str(exc)[:200]}

    if not pdf_url:
        try:
            from app.modules.scraper.core.browser_scrape import fetch_page

            rendered = fetch_page(u, skip_login_wall_check=True)
            pdf_url = _find_pdf_url_in_html(rendered.html, rendered.final_url)
        except Exception:
            pdf_url = None

    if not pdf_url:
        return {
            "url": u,
            "error": (
                "no direct PDF file URL found on this page (checked raw HTML "
                "and a rendered copy) -- the document may only be downloadable "
                "manually, or the viewer uses a pattern this tool doesn't "
                "recognize yet"
            ),
        }

    try:
        pdf_resp = _guarded_get_with_retry(pdf_url, timeout=20.0)
        pdf_resp.raise_for_status()
    except Exception as exc:
        return {
            "url": u,
            "found_pdf_url": pdf_url,
            "error": f"found a PDF URL but fetching it failed: {str(exc)[:160]}",
        }

    result = _fetch_pdf_document(pdf_resp, base=pdf_url, cap=8000, offset=0)
    result["found_pdf_url"] = pdf_url
    return result


_GOOGLE_DOC_ID_RE = re.compile(r"/document/d/([a-zA-Z0-9_-]+)")


def _tool_fetch_google_doc(url: str, max_chars: int = 6000, offset: int = 0) -> dict[str, Any]:
    """Read a published/shared Google Doc's full plain text via its own export endpoint, instead of fetch_url hitting the JS-rendered editor shell (which shows a loading UI, not the document's words).

    Only works for a doc whose sharing is "Anyone with the link can view" (or
    published to the web) — a private doc's export endpoint 401s/redirects to
    a login page, same as any other access-controlled page this pipeline
    can't authenticate into. Paginated the same way fetch_url is: call again
    with the same url and a later offset (from has_more/_next_offset) to keep
    reading.
    """
    u = (url or "").strip()
    if not u:
        return {"error": "url required"}
    m = _GOOGLE_DOC_ID_RE.search(u)
    if not m:
        return {"url": u, "error": "not a docs.google.com/document/d/<id>/... URL"}
    doc_id = m.group(1)
    export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    try:
        resp = _guarded_get_with_retry(export_url, timeout=15.0)
    except Exception as exc:
        return {"url": u, "error": str(exc)[:200]}
    if resp.status_code in (401, 403) or "accounts.google.com" in str(resp.url):
        return {
            "url": u,
            "error": (
                "doc is not publicly viewable (export endpoint requires login) -- "
                "sharing must be set to 'Anyone with the link can view'"
            ),
        }
    try:
        resp.raise_for_status()
    except Exception as exc:
        return {"url": u, "error": str(exc)[:200]}
    raw = _slice_document_text(
        resp.text, url=u, title="", links=[], max_chars=max_chars, offset=offset
    )
    return _publicize_fetch_result(raw)


_SCRIPT_SRC_RE = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.I)
_BUNDLE_GREP_MAX_SCRIPTS = 12
_BUNDLE_GREP_MAX_BYTES_TOTAL = 6_000_000
_BUNDLE_GREP_CONTEXT_CHARS = 180
_BUNDLE_GREP_NOISY_HOSTS = (
    "google-analytics",
    "googletagmanager",
    "doubleclick",
    "facebook.net",
    "hotjar",
    "sentry.io",
    "segment.",
    "intercom.",
)


def _bundle_script_urls(html: str, base: str) -> list[str]:
    """<script src=...> URLs on a page, resolved to absolute, deduped, and stripped of obvious third-party trackers/ads -- those never carry app logic and would otherwise crowd out the real bundles under the script cap."""
    from urllib.parse import urljoin, urlparse

    script_urls: list[str] = []
    seen: set[str] = set()
    for m in _SCRIPT_SRC_RE.finditer(html):
        src = urljoin(base, m.group(1))
        if src in seen:
            continue
        seen.add(src)
        host = (urlparse(src).hostname or "").lower()
        if any(noisy in host for noisy in _BUNDLE_GREP_NOISY_HOSTS):
            continue
        script_urls.append(src)
    return script_urls[:_BUNDLE_GREP_MAX_SCRIPTS]


def _bundle_body_matches(
    body: str, src: str, term: str, *, limit: int
) -> list[dict[str, Any]]:
    """Every occurrence of term (case-insensitive) in one script body, with surrounding context, up to limit."""
    matches: list[dict[str, Any]] = []
    body_lower = body.lower()
    term_lower = term.lower()
    start = 0
    while len(matches) < limit:
        idx = body_lower.find(term_lower, start)
        if idx < 0:
            break
        lo = max(0, idx - _BUNDLE_GREP_CONTEXT_CHARS)
        hi = min(len(body), idx + len(term) + _BUNDLE_GREP_CONTEXT_CHARS)
        matches.append({"script_url": src, "context": body[lo:hi]})
        start = idx + len(term)
    return matches


def _tool_grep_frontend_bundle(url: str, search_term: str, max_matches: int = 5) -> dict[str, Any]:
    """Search a page's own JS bundles for a literal string — for verifying what a single-page app actually DOES (a wallet-connect requirement, a fee constant, a feature flag) when that behavior lives only in client-side JS and never renders as text fetch_url can read.

    Root-caused 2026-07-24 (AlgoRank incident): an article claimed a dApp
    needed no wallet to vote, based on what fetch_url's rendered-text view
    showed; the live JS bundle actually required a wallet-connect call before
    the vote write, and no tool existed to check the bundle directly instead
    of the rendered page. Grep beats WebFetch/fetch_url here because the
    claim lives in source code, not in anything the DOM ever displays.

    Fetches the page's own <script src=...> bundles (same-origin and CDN,
    skipping obvious third-party analytics/ad scripts) up to a byte budget,
    and returns each match with surrounding context. A miss across every
    bundle is itself informative — it means the term isn't in the client
    code at all, not that the check failed.
    """
    u = (url or "").strip()
    term = (search_term or "").strip()
    if not u:
        return {"error": "url required"}
    if not term:
        return {"error": "search_term required"}
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    n_matches = max(1, min(int(max_matches), 20))

    try:
        page = _guarded_get_with_retry(u, headers={"Accept": "text/html"}, timeout=15.0)
        page.raise_for_status()
    except Exception as exc:
        return {"url": u, "error": str(exc)[:200]}

    script_urls = _bundle_script_urls(page.text, str(page.url))
    if not script_urls:
        return {"url": u, "error": "no <script src=...> bundles found on this page"}

    matches: list[dict[str, Any]] = []
    scripts_checked: list[str] = []
    bytes_total = 0
    for src in script_urls:
        if bytes_total >= _BUNDLE_GREP_MAX_BYTES_TOTAL or len(matches) >= n_matches:
            break
        try:
            resp = _guarded_get_with_retry(src, timeout=15.0)
            resp.raise_for_status()
        except Exception:
            continue
        body = resp.text
        bytes_total += len(body.encode("utf-8", errors="ignore"))
        scripts_checked.append(src)
        matches.extend(_bundle_body_matches(body, src, term, limit=n_matches - len(matches)))

    return {
        "url": u,
        "search_term": term,
        "scripts_checked": scripts_checked,
        "scripts_found_on_page": len(script_urls),
        "match_count": len(matches),
        "matches": matches,
    }


def _extract_html_text_and_links(
    resp: Any,  # noqa: ANN401 -- httpx.Response, kept loosely typed to match the caller
    *,
    base: str,
) -> tuple[str, str, str, list[dict[str, str]]]:
    """Parse an HTML response into (title, keep-links text, plain text, absolute-URL links list). Falls back to plain-text-only on any parse failure."""
    from urllib.parse import urljoin

    from bs4 import BeautifulSoup

    from app.modules.scraper.core.web_fetch import html_to_plain_text

    title = ""
    links: list[dict[str, str]] = []
    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        if soup.title and soup.title.string:
            title = soup.title.string.strip()[:200]
        # Resolve hrefs to absolute so both the inline text and the links list
        # give the model real, fetchable URLs to explore next.
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            absurl = urljoin(base, a["href"].strip())
            a["href"] = absurl
            label = a.get_text(" ", strip=True)
            if absurl.startswith(("http://", "https://")) and label and absurl not in seen:
                seen.add(absurl)
                if len(links) < 40:
                    links.append({"text": label[:120], "url": absurl})
        text = html_to_plain_text(str(soup), keep_links=True)
        plain_text = html_to_plain_text(resp.text)
    except Exception:
        text = plain_text = html_to_plain_text(resp.text)
    return title, text, plain_text, links


def _maybe_render_spa_fallback(
    resp: Any,  # noqa: ANN401 -- httpx.Response, kept loosely typed to match the caller
    *,
    base: str,
    title: str,
    text: str,
    plain_text: str,
    links: list[dict[str, str]],
    playwright_session: Any = None,  # noqa: ANN401 -- PlaywrightSession, kept loose to dodge an import cycle
) -> tuple[str, str, list[dict[str, str]], str]:
    """Render the page with Playwright before returning it. Returns (title, text, links, base), rendered values if it helped, the original ones otherwise (a failed render beats no result at all).

    Root-caused 2026-08-11 (self-reported, hesab.com): used to only render
    when a thin/SPA-shaped-response heuristic (needs_spa_fallback) fired,
    which is a real page-shape detector but not built to catch "the HTML
    looks like a normal page, but the numbers a writer actually needs are
    filled in by a client-side fetch()". Rather than keep tuning that
    heuristic's edge cases, this now always renders every HTML fetch when a
    persistent per-compose PlaywrightSession is available (see
    maybe_start_session in browser_scrape.py) -- reusing one browser across
    the whole compose is what makes "always render" affordable; a fresh
    Chromium launch per call was not. Falls back to the old heuristic-gated
    one-shot render for callers with no session (compose failed to start
    one, or this is called outside a compose at all).
    """
    from app.modules.scraper.crawler_registry import is_web_spa_enabled

    if not is_web_spa_enabled():
        return title, text, links, base
    if playwright_session is not None:
        try:
            rendered = playwright_session.fetch(base)
            return (
                rendered.title or title,
                rendered.text or text,
                links,  # the session's plain-text extraction carries no link list
                rendered.final_url or base,
            )
        except Exception:
            return title, text, links, base
    from app.modules.scraper.crawlers.web_crawler import needs_spa_fallback

    if not needs_spa_fallback(plain_text, raw_html=resp.text):
        return title, text, links, base
    try:
        from app.modules.scraper.core.browser_scraper import BrowserScraper

        rendered = BrowserScraper().scrape(base, "research-fetch_url")
        return (
            rendered.title or title,
            rendered.text or text,
            rendered.links or links,
            rendered.url or base,
        )
    except Exception:
        return title, text, links, base


def _fetch_url_internal(
    url: str,
    max_chars: int = 6000,
    offset: int = 0,
    playwright_session: Any = None,  # noqa: ANN401 -- PlaywrightSession
) -> dict[str, Any]:
    """Fetch and slice a URL; returns raw dict (may include ``_next_offset``)."""
    import httpx

    cap = max(500, min(int(max_chars), 12000))
    u = (url or "").strip()
    if not u:
        return {"error": "url required"}
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        resp = _guarded_get_with_retry(
            u, headers={"Accept": "text/html,application/xhtml+xml"}, timeout=15.0
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return _fetch_url_error(u, exc, status_code=exc.response.status_code)
    except Exception as exc:
        return _fetch_url_error(u, exc)
    ctype = resp.headers.get("content-type", "")
    ctype_lower = ctype.lower()
    base = str(resp.url)
    # PDFs (whitepapers, audits, tokenomics docs) are common source links; read
    # their text instead of refusing them. pypdf is already a dependency.
    if "pdf" in ctype_lower or base.lower().split("?")[0].endswith(".pdf"):
        return _fetch_pdf_document(resp, base=base, cap=cap, offset=offset)
    # Public JSON/XML APIs (sitemaps, service status endpoints, etc.) are a
    # documented fetch_url fallback for writer tools -- don't refuse them just
    # because they aren't HTML. Pretty-print JSON for readability; XML/other
    # text-ish payloads pass through as-is.
    if "html" not in ctype_lower and "json" in ctype_lower:
        body = resp.text
        with contextlib.suppress(ValueError):
            body = json.dumps(json.loads(resp.text), indent=2)
        return _slice_document_text(body, url=base, title=base, links=[], max_chars=cap, offset=offset)
    if "html" not in ctype_lower and "xml" in ctype_lower:
        return _slice_document_text(resp.text, url=base, title=base, links=[], max_chars=cap, offset=offset)
    if "html" not in ctype_lower and "text" not in ctype_lower:
        return {"url": u, "error": f"unsupported content-type: {ctype[:60]}"}

    title, text, plain_text, links = _extract_html_text_and_links(resp, base=base)
    title, text, links, base = _maybe_render_spa_fallback(
        resp,
        base=base,
        title=title,
        text=text,
        plain_text=plain_text,
        links=links,
        playwright_session=playwright_session,
    )
    return _slice_document_text(
        text,
        url=base,
        title=title,
        links=links,
        max_chars=cap,
        offset=offset,
    )


_GITHUB_REPO_URL_RE = re.compile(r"github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)", re.I)
_GITHUB_RESERVED_OWNERS = {
    "orgs",
    "topics",
    "search",
    "features",
    "about",
    "sponsors",
    "marketplace",
    "settings",
    "pulls",
    "issues",
    "notifications",
    "explore",
    "collections",
}


def _augment_github_archived(url: str, result: dict[str, Any]) -> dict[str, Any]:
    """When fetch_url lands on a github.com/<owner>/<repo> page that shows the 'repository was archived' notice, attach the OWNER's liveness so the writer can't conclude the whole project is dead from the page alone. Same signal as github_activity, but on the path the writer actually took (a raw page fetch) in the Pera Wallet incident (2026-07-20). Fail-open."""
    m = _GITHUB_REPO_URL_RE.search(url or "")
    if not m:
        return result
    owner, repo = m.group(1), m.group(2)
    if repo.endswith(".git"):
        repo = repo[:-4]
    if owner.lower() in _GITHUB_RESERVED_OWNERS:
        return result
    text = result.get("text") if isinstance(result.get("text"), str) else ""
    low = text.lower()
    if "repository was archived" not in low and "repository has been archived" not in low:
        return result
    liveness = _owner_liveness(owner, exclude=f"{owner}/{repo}")
    result["owner_liveness"] = liveness
    # Prepend the verdict so a writer skimming the page text cannot miss it — the
    # archived notice is prominent, this must be at least as prominent.
    result["text"] = "[ARCHIVED-REPO CHECK] " + liveness.get("verdict", "") + "\n\n" + text
    return result


_SPA_ROUTER_NOTFOUND_PHRASES = (
    "could not be found in this application",
    "page could not be found",
)


def _augment_spa_notfound_warning(result: dict[str, Any]) -> dict[str, Any]:
    """When fetch_url lands on a client-side-router "not found" page (HTTP 200, but the SPA's own JS painted a 404), warn the writer this does NOT prove a real UI link/button is broken.

    Root-caused 2026-08-10 (lumirogue.com "About") and recurred 2026-08-12
    (same site, "Terms of use", both from a bare-guessed /about and /terms
    URL): a single-page app routes entirely client-side, so a guessed path
    with no matching route renders this same "not found" shell even though
    the real on-page button (no <a href> at all) opens a working modal via
    JS. This never raises an HTTP error -- the server returns a normal 200
    with the app shell, so _fetch_failure_hint's 404 branch never runs.
    Fail-open: matched only on distinctive client-router copy, not a bare
    "404" (too many false positives on real dead pages' own wording).
    """
    text = result.get("text") if isinstance(result.get("text"), str) else ""
    low = text.lower()
    if not any(phrase in low for phrase in _SPA_ROUTER_NOTFOUND_PHRASES):
        return result
    result["text"] = (
        "[CLIENT-SIDE ROUTE CHECK] This looks like a single-page app's own "
        "'not found' shell for a guessed URL, not necessarily a broken link "
        "or missing page -- the real UI control (if one exists) may have no "
        "href at all and only work via a JS click. Do not report this as a "
        "broken link/page without also trying click_element on the visible "
        "button/link text.\n\n" + text
    )
    return result


def _tool_fetch_url(
    url: str,
    max_chars: int = 6000,
    offset: int = 0,
    playwright_session: Any = None,  # noqa: ANN401 -- PlaywrightSession; only set by callers that manage one (see writer_tools' scroll wrapper)
) -> dict[str, Any]:
    """Fetch a web page and return its cleaned main text (public tool result)."""
    raw = _fetch_url_internal(
        url, max_chars=max_chars, offset=offset, playwright_session=playwright_session
    )
    if raw.get("error"):
        return raw
    result = _publicize_fetch_result(raw)
    try:
        result = _augment_github_archived(url, result)
    except Exception:
        logger.debug("github-archived augmentation failed", exc_info=True)
    try:
        result = _augment_spa_notfound_warning(result)
    except Exception:
        logger.debug("SPA not-found augmentation failed", exc_info=True)
    return result


def _tool_click_element(
    url: str,
    click_text: str,
    playwright_session: Any = None,  # noqa: ANN401 -- PlaywrightSession; injected from compose context, see writer_tools._wrap_browser_actions
) -> dict[str, Any]:
    """Click a specific element (button, tab, footer item) on a page by its visible text, and return the page's content AFTER the click.

    fetch_url and browser_scrape's rendering only ever follow real hrefs or
    read what's already on the page — many SPA "links" are actually buttons
    with NO href at all, whose real content only appears via a JS action (an
    in-page modal, a non-standard accordion toggle, a tab switch). Use this
    when a page visibly has such a control and its content matters to a
    claim you're making (self-reported gap, 2026-08-10 — root-caused live
    the same day: an article said lumirogue.com's footer 'About this
    project' / 'Terms of use' were broken links returning 404. The guessed
    /about and /terms URLs genuinely do 404, but that's not the real user
    experience — the footer items are buttons with no href, and clicking
    either opens a working in-page modal with real content). Slow (loads
    and interacts with a full browser) — expect several seconds. If the
    click_text doesn't match anything, the error lists what WAS clickable
    on the page so you can retry with the right text.
    """
    u = (url or "").strip()
    if not u:
        return {"error": "url required"}
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    text = (click_text or "").strip()
    if not text:
        return {"error": "click_text required"}
    try:
        if playwright_session is not None:
            result = playwright_session.click_and_read(u, text)
        else:
            from app.modules.scraper.core.browser_scrape import click_and_read

            result = click_and_read(u, text)
    except Exception as exc:
        return _fetch_url_error(u, exc)
    return {
        "url": result.final_url,
        "title": result.title,
        "clicked": text,
        "text": result.text[:8000],
    }


def _tool_type_into_page(
    url: str,
    field_text: str,
    value: str,
    submit: bool = False,
    playwright_session: Any = None,  # noqa: ANN401 -- PlaywrightSession; injected from compose context, see writer_tools._wrap_browser_actions
) -> dict[str, Any]:
    """Type into a search box, filter, or form field on a page by the field's label/placeholder, optionally submit, and return the page's content afterward.

    Self-reported gap, 2026-08-11: fetch_url and click_element can only
    read what's already reachable by URL or a click -- some real content
    only appears after typing (an on-chain explorer's address search box,
    a directory's filter field, a docs site's search). Use submit=true to
    press Enter after filling (e.g. a search box that navigates on submit);
    leave it false for a filter that reacts live as you type. If
    field_text doesn't match anything, the error lists what fields WERE on
    the page so you can retry with the right one. Slow (loads and
    interacts with a full browser) — expect several seconds.
    """
    u = (url or "").strip()
    if not u:
        return {"error": "url required"}
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    field = (field_text or "").strip()
    if not field:
        return {"error": "field_text required"}
    if playwright_session is None:
        return {
            "error": (
                "no browser session available for this compose -- type_into_page "
                "requires Playwright rendering to be enabled"
            )
        }
    try:
        result = playwright_session.type_and_read(u, field, value or "", submit=bool(submit))
    except Exception as exc:
        return _fetch_url_error(u, exc)
    return {
        "url": result.final_url,
        "title": result.title,
        "typed_into": field,
        "submitted": bool(submit),
        "text": result.text[:8000],
    }


def _tool_capture_screenshot(
    url: str,
    full_page: bool = False,
    playwright_session: Any = None,  # noqa: ANN401 -- PlaywrightSession; injected from compose context, see writer_tools._wrap_browser_actions
) -> dict[str, Any]:
    """Screenshot a live page and return a public image_url, to illustrate an article with real visual evidence instead of describing it in prose alone -- a game's actual UI, a dashboard's current state, what a marketplace listing really looks like.

    Owner request 2026-08-11 (after personally buying a Lumi Rogue Ankh and
    playing the game). Use narrowly: reach for this when a screenshot
    genuinely shows something prose can't capture as well, not as a
    decorative addition to every article -- most articles are well served
    by the source's own og:image, already resolved automatically. full_page
    captures the whole scrollable page (a long leaderboard); the default
    (viewport only) is what a real visitor sees without scrolling, which is
    usually the right shot. Slow (loads and screenshots a full browser) —
    expect several seconds.
    """
    u = (url or "").strip()
    if not u:
        return {"error": "url required"}
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    if playwright_session is None:
        return {
            "error": (
                "no browser session available for this compose -- capture_screenshot "
                "requires Playwright rendering to be enabled"
            )
        }
    try:
        png_bytes = playwright_session.capture_screenshot(u, full_page=bool(full_page))
    except Exception as exc:
        return _fetch_url_error(u, exc)
    from app.modules.scraper.core.browser_scrape import save_screenshot

    image_url = save_screenshot(png_bytes)
    if image_url is None:
        return {
            "error": (
                "screenshot captured but could not be saved -- storage may not be "
                "configured on this host"
            )
        }
    return {"url": u, "image_url": image_url, "full_page": bool(full_page)}


def _tool_inspect_network_hosts(
    url: str,
    click_text: str = "",
    playwright_session: Any = None,  # noqa: ANN401 -- PlaywrightSession; injected from compose context, see writer_tools._wrap_browser_actions
) -> dict[str, Any]:
    """Load a page and report which hosts it ACTUALLY made network requests to, with a best-effort Algorand mainnet-vs-testnet call from known algod/indexer hostnames -- ground truth for what a dapp really does, immune to stale page copy.

    Use this, not grep_frontend_bundle, for any "is this mainnet or
    testnet" / "are fees real" / "what does connecting actually do" claim.
    Root-caused live 2026-08-13: lumirogue.com's OWN rendered UI text said
    "Algorand Testnet" -- grep_frontend_bundle dutifully found and quoted
    it -- while the site's wallet code was hardcoded to mainnet (chainId
    416001). grep_frontend_bundle is a literal text search; it can't trace
    which of several config objects a minified bundle actually wires up at
    runtime. This tool sidesteps that entirely by watching what the page's
    OWN code does when it runs, not what its text claims. Pass click_text
    (e.g. "Connect Wallet") when the relevant network call only fires after
    an interaction, not on page load. Slow (loads a full browser) — expect
    several seconds.

    A `detected_network: "unknown"` result is NOT a dead end -- confirmed
    live 2026-08-15 (also lumirogue.com): a site whose chain calls run
    server-side behind its own backend (Base44-hosted here) never exposes
    an algod/indexer host to the BROWSER at all, no matter how far you
    click through the connect flow -- this tool can only watch what the
    browser itself requests. When this happens, fall back to an on-chain
    query tool (application_boxes, lookup_asset, lookup_application)
    against any app/asset ID already found on the page instead of trusting
    the page's own network label.
    """
    u = (url or "").strip()
    if not u:
        return {"error": "url required"}
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    if playwright_session is None:
        return {
            "error": (
                "no browser session available for this compose -- "
                "inspect_network_hosts requires Playwright rendering to be enabled"
            )
        }
    try:
        return playwright_session.inspect_network_hosts(u, click_text=(click_text or "").strip())
    except Exception as exc:
        return _fetch_url_error(u, exc)


def _interactive_dispatch(
    act: str,
    playwright_session: Any,  # noqa: ANN401 -- PlaywrightSession
    url: str,
    target: str,
    value: str,
    submit: bool,
) -> Any:  # noqa: ANN401 -- BrowserPageResult
    """Perform one play_interactive action (open/click/type/read). Raises ValueError for a usage error (missing arg, unknown action), or whatever the session raises for a real render/interaction failure -- _tool_play_interactive sorts those into the right error shape."""
    if act == "open":
        u = (url or "").strip()
        if not u:
            raise ValueError("url required for action='open'")
        if not u.startswith(("http://", "https://")):
            u = "https://" + u
        return playwright_session.interactive_open(u)
    if act == "click":
        t = (target or "").strip()
        if not t:
            raise ValueError("target required for action='click'")
        return playwright_session.interactive_click(t)
    if act == "type":
        t = (target or "").strip()
        if not t:
            raise ValueError("target required for action='type'")
        return playwright_session.interactive_type(t, value or "", submit=bool(submit))
    if act == "read":
        return playwright_session.interactive_read()
    raise ValueError(f"unknown action {act!r} -- must be one of open/click/type/read/close")


def _tool_play_interactive(
    action: str,
    url: str = "",
    target: str = "",
    value: str = "",
    submit: bool = False,
    playwright_session: Any = None,  # noqa: ANN401 -- PlaywrightSession; injected from compose context, see writer_tools._wrap_play_interactive
) -> dict[str, Any]:
    """Explore a live web app/game across multiple steps on the SAME page state, to discover how its mechanics actually work.

    Owner request 2026-08-11: not to master or complete a game, just to
    discover its systems -- what a button does, what a menu reveals, how a
    form responds. fetch_url/click_element/type_into_page each start from a
    fresh page every call, so nothing the model does in one call is still
    true in the next (confirmed live: a click-to-open-search then a
    type-into-the-revealed-input failed across two separate calls because
    the second started from a blank page again). This keeps ONE page open
    across action='open' -> 'click'/'type'/'read' -> 'close', so acting on
    the actual resulting state is possible. The per-compose step budget
    (see the "budget" field on every response) is small on purpose:
    exploring a system takes a handful of steps, not a playthrough.
    """
    act = (action or "").strip().lower()
    if playwright_session is None:
        return {
            "error": (
                "no browser session available for this compose -- play_interactive "
                "requires Playwright rendering to be enabled"
            )
        }
    if act == "close":
        playwright_session.interactive_close()
        return {"action": "close", "status": "closed"}
    try:
        result = _interactive_dispatch(act, playwright_session, url, target, value, submit)
    except ValueError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        return _fetch_url_error(url or target or "interactive session", exc)
    return {
        "action": act,
        "url": result.final_url,
        "title": result.title,
        "text": result.text[:6000],
    }


def _tool_get_defi_tvl(protocol: str = "") -> dict[str, Any]:
    """Current DeFi TVL from DeFiLlama (USD). No protocol → Algorand chain TVL; a protocol slug (e.g. 'tinyman', 'folks-finance', 'pact') → that protocol's TVL."""
    p = (protocol or "").strip().lower().replace(" ", "-")
    try:
        if p:
            resp = _guarded_get(f"https://api.llama.fi/tvl/{p}")
            if resp.status_code == 404:
                return {
                    "protocol": p,
                    "error": "not found on DeFiLlama — try the slug, e.g. 'tinyman'",
                }
            resp.raise_for_status()
            return {"protocol": p, "tvl_usd": resp.json(), "source": "DeFiLlama"}
        chains = _guarded_get("https://api.llama.fi/v2/chains").json()
        for c in chains:
            if isinstance(c, dict) and (c.get("name") or "").lower() == "algorand":
                return {"chain": "Algorand", "tvl_usd": c.get("tvl"), "source": "DeFiLlama"}
        return {"chain": "Algorand", "error": "Algorand not present in DeFiLlama chains"}
    except Exception as exc:
        return {"protocol": p or "algorand-chain", "error": str(exc)[:200]}


# Root-caused live 2026-08-18: a rug.ninja recompose invented a full 10-coin
# Tinyman-liquidity bar chart plus a vestige.fi volume stat, with zero
# grounding in any tool call -- there was no tool that could answer a
# per-token liquidity/volume/TVL question at all (get_defi_tvl only covers
# protocol-level TVL via DeFiLlama, never a specific ASA). Vestige's own
# documented "free API" host (free-api.vestige.fi) 530s from both this
# server and prod; api.vestigelabs.org is the real host the live vestige.fi
# frontend actually calls (found via its JS bundle) and it works.
def _tool_lookup_asset_market_data(asset_ids: str) -> dict[str, Any]:
    """Live price, 24h/7d volume, market cap and total pool liquidity (TVL) for one or more Algorand ASAs, via Vestige's aggregator API (a single cross-DEX aggregate number per token) — get_defi_tvl only covers protocol-level TVL, never a specific coin. Use this for ANY liquidity, volume, or market-cap claim about a named token instead of estimating or inventing a number. For a per-DEX breakdown (is it actually listed on Tinyman/Pact specifically, verified status, or confirmation it's not listed anywhere), use search_token_listings instead or in addition."""
    ids = [s.strip() for s in str(asset_ids).split(",") if s.strip()]
    if not ids:
        return {"error": "asset_ids required — comma-separated ASA ids, e.g. '2200000000'"}
    if len(ids) > 25:
        return {"error": "too many asset_ids — max 25 per call"}
    try:
        resp = _guarded_get(
            "https://api.vestigelabs.org/assets/list",
            params={"asset_ids": ",".join(ids), "network_id": 0},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"asset_ids": ids, "error": str(exc)[:200]}
    results = data.get("results") if isinstance(data, dict) else None
    if not results:
        return {
            "asset_ids": ids,
            "assets": [],
            "error": "no Vestige data for these asset ids — untracked (e.g. never traded on a "
            "tracked DEX) or invalid",
        }
    assets = [
        {
            "asset_id": r.get("id"),
            "name": r.get("name"),
            "ticker": r.get("ticker"),
            "price_usd": r.get("price"),
            "price_change_1d_usd": r.get("price1d"),
            "volume_1d_usd": r.get("volume1d"),
            "volume_7d_usd": r.get("volume7d"),
            "swaps_1d": r.get("swaps1d"),
            "market_cap_usd": r.get("market_cap"),
            "tvl_usd": r.get("tvl"),
            "rank": r.get("rank"),
        }
        for r in results
    ]
    found_ids = {str(a["asset_id"]) for a in assets}
    missing = [i for i in ids if i not in found_ids]
    out: dict[str, Any] = {"assets": assets, "source": "Vestige (api.vestigelabs.org)"}
    if missing:
        out["missing_asset_ids"] = missing
    return out


_ASSET_MARKET_DATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lookup_asset_market_data",
        "description": (
            "Live price, 24h/7d volume, market cap, and total pool liquidity (TVL) "
            "for one or more Algorand ASAs, via Vestige's aggregator API (one "
            "cross-DEX aggregate number per token) — "
            "get_defi_tvl only covers protocol-level TVL, never a specific coin. "
            "Use for ANY liquidity, volume, or market-cap claim about a named "
            "token instead of estimating or inventing a number. For a per-DEX "
            "breakdown (is it actually listed on Tinyman/Pact specifically, "
            "verified status, or confirmed not listed anywhere), use "
            "search_token_listings instead or in addition."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "asset_ids": {
                    "type": "string",
                    "description": (
                        "Comma-separated Algorand ASA ids, e.g. '2200000000' or "
                        "'2200000000,31566704' (max 25 per call)."
                    ),
                },
            },
            "required": ["asset_ids"],
        },
    },
}


def _downsample_monthly(points: list[dict[str, Any]]) -> list[dict[str, str | float]]:
    """Collapse a daily {date, totalLiquidityUSD} series to one point per calendar month (the last day seen in that month) — DeFiLlama's raw series runs 1,000+ daily points for any protocol tracked more than a few years, far past what a research tool response should return in one call. Keeping the LAST day of each month (not first/average) matches how a reader intuitively reads a monthly chart: "where did it end up that month."."""
    from datetime import UTC, datetime

    by_month: dict[str, dict[str, Any]] = {}
    for pt in points:
        ts = pt.get("date")
        tvl = pt.get("totalLiquidityUSD")
        if ts is None or tvl is None:
            continue
        month_key = datetime.fromtimestamp(int(ts), tz=UTC).strftime("%Y-%m")
        by_month[month_key] = {"month": month_key, "tvl_usd": tvl}
    return [by_month[k] for k in sorted(by_month)]


def _tool_get_defi_tvl_history(protocol: str, months: int = 24) -> dict[str, Any]:
    """Historical DeFi TVL trend for one protocol from DeFiLlama (USD), downsampled to one point per month. Use to chart adoption/decline over time instead of inferring a trend from a single current-TVL snapshot -- `get_defi_tvl` only gives you today's number."""
    p = (protocol or "").strip().lower().replace(" ", "-")
    if not p:
        return {
            "error": "protocol is required, e.g. 'tinyman' — use get_defi_tvl for the Algorand chain total"
        }
    months = max(1, min(months, 60))
    try:
        resp = _guarded_get(f"https://api.llama.fi/protocol/{p}")
        # Unlike /tvl/{slug} (404 on a bad slug), /protocol/{slug} answers an
        # unknown slug with 400 — verified live 2026-08-07 against a garbage
        # slug, not documented behavior to assume from the sibling endpoint.
        if resp.status_code in (400, 404):
            return {
                "protocol": p,
                "error": "not found on DeFiLlama — try the slug, e.g. 'tinyman'",
            }
        resp.raise_for_status()
        data = resp.json()
        chain_tvls = data.get("chainTvls") or {}
        # A multi-chain protocol has one series per chain; prefer Algorand's,
        # falling back to the top-level combined series if Algorand isn't its
        # own key (single-chain protocols keep everything under one entry).
        series = ((chain_tvls.get("Algorand") or {}).get("tvl")) or data.get("tvl") or []
        if not series:
            return {"protocol": p, "error": "no TVL history available for this protocol"}
        monthly = _downsample_monthly(series)[-months:]
        if not monthly:
            return {"protocol": p, "error": "no TVL history available for this protocol"}
        values = [m["tvl_usd"] for m in monthly]
        return {
            "protocol": p,
            "monthly_tvl_usd": monthly,
            "peak_tvl_usd": max(values),
            "current_tvl_usd": values[-1],
            "source": "DeFiLlama",
        }
    except Exception as exc:
        return {"protocol": p, "error": str(exc)[:200]}


def _tool_search_nfd_directory(name: str = "", address: str = "") -> dict[str, Any]:
    """Resolve an Algorand NFD (.algo name) via the NFDomains public API.

    Pass `name` (e.g. 'gazer.algo') to look up its owner address; pass
    `address` to reverse-lookup the .algo name(s) that address owns. Exactly
    one of name/address should be set.

    Name lookups use view=full (2026-08-05, root-caused live): the previous
    view=tiny silently dropped properties.verified entirely -- an NFD owner
    can cryptographically verify Discord/GitHub/X/Bluesky/Telegram handles
    and additional linked Algorand addresses (caAlgo), which is exactly the
    kind of independent identity corroboration a "who is really behind this
    project" investigation needs and no other tool here provides.
    """
    name = (name or "").strip()
    address = (address or "").strip()
    if not name and not address:
        return {"error": "name or address is required"}
    try:
        if name:
            slug = name if name.endswith(".algo") else f"{name}.algo"
            resp = _guarded_get(f"https://api.nf.domains/nfd/{slug}", params={"view": "full"})
            if resp.status_code == 404:
                return {"name": slug, "found": False}
            resp.raise_for_status()
            data = resp.json()
            verified = (data.get("properties") or {}).get("verified") or {}
            return {
                "name": data.get("name", slug),
                "found": True,
                "owner": data.get("owner"),
                "deposit_account": data.get("depositAccount"),
                "url": (data.get("properties") or {}).get("userDefined", {}).get("url"),
                "time_created": data.get("timeCreated"),
                # Cryptographically verified by the NFD owner, not self-reported
                # free text -- absence of a field means not verified, never
                # "confirmed absent."
                "verified_discord": verified.get("discord"),
                "verified_github": verified.get("github"),
                "verified_x": verified.get("x") or verified.get("twitter"),
                "verified_bluesky_did": verified.get("blueskydid"),
                "verified_telegram": verified.get("telegram"),
                # Other Algorand addresses this same NFD identity has verified as
                # its own -- an on-chain-verified alternative to comparing raw
                # creator addresses or auth_addr.
                "linked_algorand_addresses": data.get("caAlgo") or [],
            }
        resp = _guarded_get(
            "https://api.nf.domains/nfd/lookup", params={"address": address, "view": "tiny"}
        )
        resp.raise_for_status()
        data = resp.json()
        entry = data.get(address) if isinstance(data, dict) else None
        if not entry:
            return {"address": address, "found": False}
        return {
            "address": address,
            "found": True,
            "name": entry.get("name"),
            "state": entry.get("state"),
            "expired": entry.get("expired"),
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


def _tool_list_nfd_segments(parent_name: str, limit: int = 20) -> dict[str, Any]:
    """List the child segments (subdomains) issued under a parent Algorand NFD (.algo name), via NFDomains' public API.

    search_nfd_directory only resolves ONE name at a time; it has no way to
    answer "how many identities has this project actually issued" (e.g. every
    *.lumirogue.algo the game hands out to players) short of guessing names
    one by one. Self-reported gap, 2026-08-13 (suggest_tool, LumiRogue
    session): "I could not LIST all .lumirogue.algo subdomains the game has
    issued -- that would have given the exact number of player identities."
    Two real API calls: resolve the parent to its appID, then browse by
    parentAppID (documented for exactly this: "the parent NFD Application ID
    to find. Used for fetching segments").
    """
    parent = (parent_name or "").strip()
    if not parent:
        return {"error": "parent_name is required"}
    slug = parent if parent.endswith(".algo") else f"{parent}.algo"
    try:
        resp = _guarded_get(f"https://api.nf.domains/nfd/{slug}", params={"view": "full"})
        if resp.status_code == 404:
            return {"parent": slug, "found": False}
        resp.raise_for_status()
        parent_data = resp.json()
        app_id = parent_data.get("appID")
        if not app_id:
            return {"parent": slug, "found": True, "error": "parent NFD has no appID on record"}
        # The parent's own record reports its true total segment count
        # (segmentCount) independent of pagination below -- surface it
        # directly so a capped `limit` never silently understates "how many
        # identities has this project issued".
        reported_total = (parent_data.get("properties") or {}).get("internal", {}).get(
            "segmentCount"
        )
        browse_resp = _guarded_get(
            "https://api.nf.domains/nfd/browse",
            params={"parentAppID": app_id, "limit": max(1, min(int(limit), 200))},
        )
        browse_resp.raise_for_status()
        rows = browse_resp.json()
        segments = [
            {
                "name": r.get("name"),
                "owner": r.get("owner"),
                "state": r.get("state"),
                "expired": r.get("expired"),
            }
            for r in (rows if isinstance(rows, list) else [])
        ]
        return {
            "parent": slug,
            "found": True,
            "reported_total_segments": int(reported_total) if reported_total else None,
            "returned": len(segments),
            "segments": segments,
        }
    except Exception as exc:
        return {"error": str(exc)[:200]}


_LIST_NFD_SEGMENTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "list_nfd_segments",
        "description": (
            "List the child segments (subdomains) issued under a parent "
            "Algorand NFD, e.g. every *.lumirogue.algo a project has handed "
            "out — via NFDomains' public API. Use for a real 'how many "
            "on-chain identities has this project issued' count, instead of "
            "counting names you happen to spot elsewhere (a scoreboard, a "
            "screenshot). Also returns the parent's own reported total "
            "segment count, which stays accurate even if more segments exist "
            "than `limit` returns."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "parent_name": {
                    "type": "string",
                    "description": "the parent NFD, e.g. 'lumirogue.algo' (the .algo suffix is optional)",
                },
                "limit": {
                    "type": "integer",
                    "description": "max segments to return, default 20, capped at 200",
                },
            },
            "required": ["parent_name"],
        },
    },
}


_NFD_DIRECTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_nfd_directory",
        "description": (
            "Resolve an Algorand NFD (.algo name) to its owner address, or "
            "reverse-resolve an address to the .algo name(s) it owns, via "
            "NFDomains' public API. Use to verify a claimed .algo identity "
            "actually resolves on-chain, or find the name behind an address. A "
            "name lookup also returns Discord/GitHub/X/Bluesky/Telegram handles "
            "and other addresses the owner has cryptographically VERIFIED as "
            "theirs — real corroboration, not self-reported; a missing field "
            "means unverified, not confirmed absent."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "an NFD name, e.g. 'gazer.algo' (the .algo suffix is optional)",
                },
                "address": {
                    "type": "string",
                    "description": "an Algorand address to reverse-lookup",
                },
            },
        },
    },
}


def _tool_app_store_metrics(term: str = "") -> dict[str, Any]:
    """Apple App Store listing(s) matching a search term, via Apple's free public iTunes Search API: app name, bundle id, rating count and average rating as a real third-party adoption/quality signal. No equivalent free public API exists for Google Play (its Console API only covers apps you own) — this is iOS-only."""
    q = (term or "").strip()
    if not q:
        return {"error": "term is required"}
    try:
        resp = _guarded_get(
            "https://itunes.apple.com/search",
            params={"term": q, "entity": "software", "limit": 5},
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"term": q, "error": str(exc)[:200]}
    results = [
        {
            "app_name": r.get("trackName"),
            "bundle_id": r.get("bundleId"),
            "seller": r.get("sellerName"),
            "rating_count": r.get("userRatingCount"),
            "average_rating": r.get("averageUserRating"),
            "current_version_rating_count": r.get("userRatingCountForCurrentVersion"),
        }
        for r in (data.get("results") or [])
        if isinstance(r, dict)
    ]
    return {"term": q, "platform": "ios", "count": len(results), "results": results}


_APP_STORE_METRICS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "app_store_metrics",
        "description": (
            "iOS App Store rating count and average rating for apps matching "
            "a search term — a real, third-party adoption/quality proxy "
            "(e.g. for a mobile wallet), via Apple's free public iTunes "
            "Search API. iOS only: there is no equivalent free public API "
            "for Google Play download counts (its Console API only covers "
            "apps you own), so don't claim Android numbers from this."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "term": {
                    "type": "string",
                    "description": "app name / search term, e.g. 'Pera Wallet Algorand'",
                },
            },
            "required": ["term"],
        },
    },
}


def _tool_package_download_stats(registry: str = "", package: str = "") -> dict[str, Any]:
    """Download counts for an npm or PyPI package (e.g. an AlgoKit utility) — free, unauthenticated third-party adoption numbers. registry is 'npm' or 'pypi'; package is the exact published name (npm scoped names like '@algorandfoundation/algokit-utils' are fine as-is)."""
    import urllib.parse

    reg = (registry or "").strip().lower()
    pkg = (package or "").strip()
    if not pkg:
        return {"error": "package is required"}
    if reg not in {"npm", "pypi"}:
        return {"error": "registry must be 'npm' or 'pypi'"}
    encoded = urllib.parse.quote(pkg, safe="")
    try:
        if reg == "npm":
            week = _guarded_get(f"https://api.npmjs.org/downloads/point/last-week/{encoded}")
            month = _guarded_get(f"https://api.npmjs.org/downloads/point/last-month/{encoded}")
            if week.status_code == 404 or month.status_code == 404:
                return {"registry": reg, "package": pkg, "error": "package not found on npm"}
            week.raise_for_status()
            month.raise_for_status()
            return {
                "registry": reg,
                "package": pkg,
                "downloads_last_week": week.json().get("downloads"),
                "downloads_last_month": month.json().get("downloads"),
                "source": "npmjs.org",
            }
        resp = _guarded_get(f"https://pypistats.org/api/packages/{encoded}/recent")
        if resp.status_code == 404:
            return {"registry": reg, "package": pkg, "error": "package not found on PyPI"}
        resp.raise_for_status()
        data = (resp.json() or {}).get("data") or {}
        return {
            "registry": reg,
            "package": pkg,
            "downloads_last_day": data.get("last_day"),
            "downloads_last_week": data.get("last_week"),
            "downloads_last_month": data.get("last_month"),
            "source": "pypistats.org",
        }
    except Exception as exc:
        return {"registry": reg, "package": pkg, "error": str(exc)[:200]}


_PACKAGE_DOWNLOADS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "package_download_stats",
        "description": (
            "Download counts for a published npm or PyPI package — concrete, "
            "third-party adoption numbers for an SDK/CLI/utility (e.g. AlgoKit's "
            "TypeScript or Python packages) rather than a claim from the project's "
            "own marketing. Use the package's exact published name (npm scoped "
            "names like '@algorandfoundation/algokit-utils' work as-is)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "registry": {
                    "type": "string",
                    "enum": ["npm", "pypi"],
                    "description": "which registry the package is published on",
                },
                "package": {
                    "type": "string",
                    "description": "exact published package name",
                },
            },
            "required": ["registry", "package"],
        },
    },
}


_NODELY_DS_QUERY = "https://g.nodely.io/api/ds/query"
_NODELY_CH_UID = "fc25640e-50ee-4e04-aad6-2a5336c09eaf"
# In-process hourly cache: this is Nodely's free dashboard infra and the daily
# estimate barely moves, so read it at most once an hour per worker.
_node_stats_cache: dict[str, Any] = {}


def _tool_get_node_stats() -> dict[str, Any]:
    """Algorand mainnet NODE telemetry from Nodely's public dashboard: the latest daily estimate of full-time running nodes (Chao-1) plus the recent trend, for network decentralization / participation-scale context. This is a NODE count (off-chain telemetry, source g.nodely.io); for on-chain online STAKE use the get_consensus_stats tool."""
    import time

    now = time.time()
    cached = _node_stats_cache.get("data")
    if cached is not None and now - float(_node_stats_cache.get("at", 0)) < 3600:
        return cached

    now_ms = int(now * 1000)
    body = {
        "from": str(now_ms - 30 * 86_400_000),
        "to": str(now_ms),
        "queries": [
            {
                "refId": "A",
                "datasource": {"type": "grafana-clickhouse-datasource", "uid": _NODELY_CH_UID},
                "rawSql": "select * from nodely.v_node_cnt_daily order by ts desc limit 30",
                "format": 1,
                "queryType": "table",
                "intervalMs": 86_400_000,
                "maxDataPoints": 100,
            }
        ],
    }
    try:
        resp = _guarded_post(_NODELY_DS_QUERY, json=body)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"error": str(exc)[:200], "source": "g.nodely.io"}

    try:
        frame = data["results"]["A"]["frames"][0]
        fields = [f["name"] for f in frame["schema"]["fields"]]
        cols = frame["data"]["values"]
    except (KeyError, IndexError, TypeError):
        return {"error": "unexpected response shape from Nodely", "source": "g.nodely.io"}
    idx = {name: i for i, name in enumerate(fields)}
    if "ts" not in idx or "nodes" not in idx or not cols or not cols[idx["ts"]]:
        return {"error": f"missing ts/nodes columns (got {fields})", "source": "g.nodely.io"}

    from datetime import UTC, datetime

    series = []
    for ts_ms, nodes in zip(cols[idx["ts"]], cols[idx["nodes"]], strict=False):
        try:
            day = datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
        except Exception:
            day = str(ts_ms)
        series.append({"date": day, "nodes": nodes})
    # ts DESC, so newest first.
    result = {
        "source": "Nodely (g.nodely.io/d/network)",
        "metric": "full-time mainnet node estimate (Chao-1, daily)",
        "latest_date": series[0]["date"],
        "node_count": series[0]["nodes"],
        "recent": series[:14],
    }
    _node_stats_cache["data"] = result
    _node_stats_cache["at"] = now
    return result


def _discourse_about(base: str, hdr: dict[str, str]) -> dict[str, Any]:
    """Site title/description/stats from /about.json, or an {"error": ...} dict when the host isn't reachable as a Discourse forum."""
    try:
        ab = (_guarded_get(f"{base}/about.json", headers=hdr).json() or {}).get("about", {})
        stats = ab.get("stats", {}) or {}
        return {
            "title": ab.get("title"),
            "description": (ab.get("description") or "")[:300],
            "stats": {
                k: stats[k]
                for k in (
                    "topic_count",
                    "post_count",
                    "user_count",
                    "topics_last_day",
                    "posts_last_day",
                    "active_users_last_day",
                )
                if k in stats
            },
        }
    except Exception as exc:
        # A non-Discourse site (or one with the API disabled) — say so plainly.
        return {"error": f"not reachable as a Discourse forum: {str(exc)[:140]}"}


def _discourse_search(base: str, hdr: dict[str, str], q: str, n: int) -> dict[str, Any]:
    """Search results (topic/excerpt/author/date/replies/url) from the forum's public /search.json."""
    try:
        data = _guarded_get(f"{base}/search.json", headers=hdr, params={"q": q[:200]}).json() or {}
        topics_by_id = {t.get("id"): t for t in data.get("topics", []) or [] if isinstance(t, dict)}
        results = []
        for p in (data.get("posts", []) or [])[:n]:
            if not isinstance(p, dict):
                continue
            topic = topics_by_id.get(p.get("topic_id"), {})
            results.append(
                {
                    "topic": topic.get("title") or "",
                    "excerpt": (p.get("blurb") or "")[:300],
                    "author": p.get("username"),
                    "date": (p.get("created_at") or "")[:10],
                    "replies": topic.get("posts_count"),
                    "url": (
                        f"{base}/t/{topic.get('slug')}/{topic.get('id')}"
                        if topic.get("slug") and topic.get("id")
                        else ""
                    ),
                }
            )
        return {"results": results, "count": len(results)}
    except Exception as exc:
        return {"error": f"search failed: {str(exc)[:160]}"}


def _discourse_categories(base: str, hdr: dict[str, str]) -> tuple[list[dict[str, Any]], dict, str]:
    """Top categories (name/topic count/description) plus an id -> name lookup for topic annotation. Returns (categories, id_to_name, error_message) -- mirrors _discourse_recent_topics's (data, error) shape so a fetch failure surfaces to the caller instead of reading as "this forum has zero categories"."""
    try:
        clist = (
            (_guarded_get(f"{base}/categories.json", headers=hdr).json() or {}).get(
                "category_list", {}
            )
            or {}
        ).get("categories", []) or []
        categories = [
            {
                "name": c.get("name"),
                "topics": c.get("topic_count"),
                "description": (c.get("description_text") or "")[:160],
            }
            for c in clist[:15]
            if isinstance(c, dict)
        ]
        cat_names = {c.get("id"): c.get("name") or "" for c in clist if isinstance(c, dict)}
        return categories, cat_names, ""
    except Exception as exc:
        return [], {}, str(exc)[:160]


def _discourse_recent_topics(
    base: str, hdr: dict[str, str], n: int, cat_names: dict
) -> tuple[list[dict[str, Any]], str]:
    """Recent topics from /latest.json, annotated with category names. Returns (topics, error_message)."""
    try:
        topics = (
            (_guarded_get(f"{base}/latest.json", headers=hdr).json() or {}).get("topic_list", {})
            or {}
        ).get("topics", []) or []
        return [
            {
                "title": t.get("title"),
                "replies": t.get("reply_count", t.get("posts_count")),
                "views": t.get("views"),
                "category": cat_names.get(t.get("category_id"), ""),
                "last_activity": t.get("last_posted_at") or t.get("bumped_at"),
                "url": (
                    f"{base}/t/{t.get('slug')}/{t.get('id')}"
                    if t.get("slug") and t.get("id")
                    else ""
                ),
            }
            for t in topics[:n]
            if isinstance(t, dict)
        ], ""
    except Exception as exc:
        return [], str(exc)[:160]


def _tool_discourse_forum(forum_url: str, limit: int = 10, query: str = "") -> dict[str, Any]:
    """Live activity from a Discourse community forum (most crypto project forums, incl. Folks Finance) via its public JSON API — site stats, top categories, and recent topics with reply/view counts. Pass ``query`` to search the forum's public /search.json instead of listing latest topics (prod writers kept wanting 'search the Algorand forum for <project>', which latest-topics can't answer). Read this instead of a static page snapshot to gauge what the community is actually discussing right now."""
    from urllib.parse import urlsplit

    raw = (forum_url or "").strip()
    if not raw:
        return {"error": "forum_url required"}
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    # Discourse JSON endpoints hang off the site root — drop any path the model passed.
    parts = urlsplit(raw)
    if not parts.netloc:
        return {"error": f"could not parse forum_url '{forum_url}'"}
    host = parts.netloc.lower()
    # The model plausibly guesses this host for the Algorand forum (seen in prod);
    # it doesn't resolve — the real forum lives at forum.algorand.org.
    if host == "forum.algorand.foundation":
        host = "forum.algorand.org"
    base = f"{parts.scheme}://{host}"
    n = max(1, min(int(limit), 25))
    hdr = {"Accept": "application/json"}
    out: dict[str, Any] = {"forum": base}

    about = _discourse_about(base, hdr)
    if "error" in about:
        out.update(about)
        return out
    out.update(about)

    q = (query or "").strip()
    if q:
        out["query"] = q
        out.update(_discourse_search(base, hdr, q, n))
        return out

    categories, cat_names, categories_error = _discourse_categories(base, hdr)
    out["categories"] = categories
    if categories_error:
        out["categories_error"] = categories_error
    recent_topics, latest_error = _discourse_recent_topics(base, hdr, n, cat_names)
    out["recent_topics"] = recent_topics
    if latest_error:
        out["latest_error"] = latest_error
    return out


def _tool_telegram_channel_lookup(handle: str = "") -> dict[str, Any]:
    """Check whether a specific @handle is a real public Telegram channel/group, via the platform's own posting bot (Bot API's getChat resolves any public handle without needing the bot to be a member). NOT a search — you must already have a candidate handle (try search_web first, or the project's own name). Returns existence, title/type/description, visible member count, and the most recent post date seen on the channel's public web preview (a real activity signal — a channel that exists but hasn't posted in years is itself a notable fact, not evidence of an active community)."""
    import re

    from app.core.config import TELEGRAM_BOT_TOKEN

    h = (handle or "").strip().lstrip("@")
    if not h:
        return {"error": "handle is required"}
    if not TELEGRAM_BOT_TOKEN:
        return {"handle": h, "error": "telegram lookup not configured"}
    base = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    try:
        resp = _guarded_get(f"{base}/getChat", params={"chat_id": f"@{h}"})
        data = resp.json()
    except Exception as exc:
        return {"handle": h, "error": str(exc)[:200]}
    if not data.get("ok"):
        return {"handle": h, "exists": False}
    result = data.get("result") or {}
    out: dict[str, Any] = {
        "handle": h,
        "exists": True,
        "title": result.get("title"),
        "type": result.get("type"),
        "description": (result.get("description") or "")[:300] or None,
    }
    try:
        count_data = _guarded_get(f"{base}/getChatMemberCount", params={"chat_id": f"@{h}"}).json()
        if count_data.get("ok"):
            out["member_count"] = count_data.get("result")
    except Exception:
        pass
    try:
        preview = _guarded_get(f"https://t.me/s/{h}", headers={"User-Agent": "Mozilla/5.0"})
        if preview.status_code == 200:
            times = sorted(set(re.findall(r'<time datetime="([^"]+)"', preview.text)))
            if times:
                out["most_recent_post_at"] = times[-1]
    except Exception:
        pass
    return out


def _tool_lookup_discord_invite_stats(invite: str) -> dict[str, Any]:
    """Real member/online counts for a Discord server, via Discord's own public invite-resolution endpoint (no bot token needed — this is the same unauthenticated API Discord itself uses to preview an invite before you join). Accepts a full discord.gg/invite URL or a bare invite code.

    Root-caused 2026-08-06: a compose could only confirm that a Discord
    invite LINK exists, never how large or active the community behind it
    actually is — "links to a Discord" and "32 members, 8 online" are very
    different claims, and only the second is a real signal.
    """
    import re

    raw = (invite or "").strip()
    if not raw:
        return {"error": "invite is required"}
    match = re.search(r"(?:discord\.gg/|discord\.com/invite/)([A-Za-z0-9-]+)", raw)
    code = match.group(1) if match else raw.rstrip("/").split("/")[-1]
    if not code:
        return {"error": "could not extract an invite code from input"}
    try:
        resp = _guarded_get(
            f"https://discord.com/api/v10/invites/{code}", params={"with_counts": "true"}
        )
    except Exception as exc:
        return {"invite_code": code, "error": str(exc)[:200]}
    if resp.status_code == 404:
        return {"invite_code": code, "exists": False, "error": "invite not found or expired"}
    if resp.status_code != 200:
        return {"invite_code": code, "error": f"discord API {resp.status_code}"}
    try:
        data = resp.json()
    except Exception:
        return {"invite_code": code, "error": "unexpected discord response"}
    guild = data.get("guild") or {}
    profile = data.get("profile") or {}
    return {
        "invite_code": code,
        "exists": True,
        "guild_name": guild.get("name"),
        "member_count": profile.get("member_count"),
        "online_count": profile.get("online_count"),
        "description": (guild.get("description") or profile.get("description") or "")[:300] or None,
    }


def _tool_lookup_world_population() -> dict[str, Any]:
    """Latest total world population figure, via the World Bank's public API (no key needed) — for cross-checking a claim that some on-chain counter, token supply, or tracker is meant to mirror world population. Returns the most recent available year's figure; World Bank publishes annual estimates, not a live/real-time count, so treat this as 'the real figure as of the cited year', not today's exact population."""
    try:
        # No mrnev param: it 400s on this network path for reasons unclear
        # (reproduced with plain curl, not a client bug) — the API already
        # sorts newest-first by default, so per_page=1 alone gets the same
        # latest-year record.
        resp = _guarded_get(
            "https://api.worldbank.org/v2/country/WLD/indicator/SP.POP.TOTL",
            params={"format": "json", "per_page": "1"},
        )
    except Exception as exc:
        return {"error": str(exc)[:200]}
    if resp.status_code != 200:
        return {"error": f"World Bank API {resp.status_code}"}
    try:
        data = resp.json()
        record = data[1][0]
    except Exception:
        return {"error": "unexpected World Bank response shape"}
    return {
        "population": record.get("value"),
        "year": record.get("date"),
        "source": "World Bank (indicator SP.POP.TOTL)",
    }


_WORLD_POPULATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lookup_world_population",
        "description": (
            "Latest total world population figure from the World Bank's "
            "public data API (no key needed, no arguments). Use to check a "
            "claim that some on-chain counter, token supply, or tracker is "
            "meant to mirror world population, instead of reporting the "
            "claim unverified. Figure is the most recent annual estimate, "
            "not a live count."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


_DISCORD_INVITE_STATS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lookup_discord_invite_stats",
        "description": (
            "Real member and online counts for a Discord server, via its own "
            "public invite preview (no bot/auth needed). Pass a discord.gg "
            "invite URL or bare code. A project's Discord link existing tells "
            "you nothing about community size — 'has a Discord' and '32 "
            "members, 8 online' are very different claims; use this before "
            "characterizing a community as active or substantial."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "invite": {
                    "type": "string",
                    "description": "discord.gg/XXXX URL, or the bare invite code",
                },
            },
            "required": ["invite"],
        },
    },
}


_TELEGRAM_LOOKUP_SCHEMA = {
    "type": "function",
    "function": {
        "name": "telegram_channel_lookup",
        "description": (
            "Check whether a specific Telegram @handle is a real, public "
            "channel/group and how active it actually is — existence, "
            "title/description, visible member count, and the most recent "
            "post date from its public web preview. This is a LOOKUP, not a "
            "search: you need a candidate handle first (try search_web, or "
            "the project's own name/branding). A channel existing does not "
            "mean it's active — check most_recent_post_at before calling a "
            "community 'active'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": {
                    "type": "string",
                    "description": "the channel's @handle, with or without the leading @",
                },
            },
            "required": ["handle"],
        },
    },
}


def _normalize_repo_slug(repo: str) -> str:
    """'owner/name', a github.com URL, or '...git' -> 'owner/name' ('' if invalid)."""
    slug = (repo or "").strip().rstrip("/")
    if "github.com/" in slug:
        slug = slug.split("github.com/", 1)[1]
    slug = "/".join(slug.split("/")[:2])
    if slug.endswith(".git"):
        slug = slug[:-4]
    if slug.count("/") != 1 or not all(slug.split("/")):
        return ""
    return slug


def _tool_github_repo_contents(repo: str, path: str = "", ref: str = "") -> dict[str, Any]:
    """Inspect a GitHub repo's files via the contents API. Empty path → root directory listing; a directory path → its entries; a file path → the file's decoded text. Use to READ smart-contract source and judge what a project actually shipped (github_activity only gives metadata). GITHUB_TOKEN optional."""
    import base64

    slug = _normalize_repo_slug(repo)
    if not slug:
        return {
            "error": f"expected owner/name, got '{repo}' — for an owner's repo "
            "list, call github_activity with just the owner"
        }
    p = (path or "").strip().lstrip("/")
    params = {"ref": ref.strip()} if ref and ref.strip() else None
    try:
        resp = _github_get(f"https://api.github.com/repos/{slug}/contents/{p}", params=params)
        if resp.status_code == 404:
            return {"repo": slug, "path": p, "error": "path not found"}
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"repo": slug, "path": p, "error": str(exc)[:200]}
    if isinstance(data, list):  # directory listing
        entries = [
            {"name": e.get("name"), "type": e.get("type"), "size": e.get("size")}
            for e in data
            if isinstance(e, dict)
        ]
        return {"repo": slug, "path": p or "/", "kind": "dir", "entries": entries[:100]}
    if isinstance(data, dict) and data.get("type") == "file":
        text = ""
        if data.get("encoding") == "base64" and data.get("content"):
            try:
                text = base64.b64decode(data["content"]).decode("utf-8", "replace")
            except Exception:
                text = ""
        # Files >1MB come back with empty content + a download_url; fetch that.
        if not text and data.get("download_url"):
            try:
                text = _github_get(data["download_url"], timeout=15.0).text
            except Exception:
                text = ""
        cap = 12000
        return {
            "repo": slug,
            "path": data.get("path"),
            "kind": "file",
            "size": data.get("size"),
            "text": text[:cap],
            "truncated": len(text) > cap,
        }
    return {"repo": slug, "path": p, "error": "unexpected contents response"}


def _medium_feed_url_for(source: str) -> str:
    """Normalize an @handle, a medium.com URL, or a Medium-backed custom domain to its RSS feed URL."""
    from urllib.parse import urlsplit

    s = source
    has_scheme = s.startswith(("http://", "https://"))
    if s.startswith("@") or (not has_scheme and "." not in s.split("/")[0]):
        # an @handle or a bare handle (no dot, no scheme)
        return f"https://medium.com/feed/{s if s.startswith('@') else '@' + s}"
    # a URL or a bare domain/path — normalize to one URL code path
    if not has_scheme:
        s = "https://" + s
    parts = urlsplit(s)
    path = parts.path.strip("/")
    if "medium.com" in parts.netloc:
        if path.startswith("feed"):  # already a feed URL — use as-is
            return f"https://medium.com/{path}"
        if path:  # /@handle or /publication
            return f"https://medium.com/feed/{path.split('/')[0]}"
        return "https://medium.com/feed"
    if path.startswith("feed"):  # custom domain feed URL
        return f"{parts.scheme}://{parts.netloc}/{path}"
    return f"{parts.scheme}://{parts.netloc}/feed"  # custom domain backed by Medium


def _parse_medium_rss(feed_url: str, n: int) -> dict[str, Any]:
    """Fetch and parse a Medium RSS feed into (title, link, published, categories) articles."""
    from lxml import etree

    try:
        resp = _guarded_get(
            feed_url,
            headers={"Accept": "application/rss+xml, application/xml, text/xml"},
            timeout=12.0,
        )
        resp.raise_for_status()
        # recover=True tolerates the slightly-malformed XML some Medium custom-domain
        # feeds emit (stray entities/tags) instead of hard-failing the whole feed.
        root = etree.fromstring(resp.content, parser=etree.XMLParser(recover=True))
    except Exception as exc:
        return {"feed": feed_url, "error": str(exc)[:200], "articles": []}
    if root is None:
        return {"feed": feed_url, "error": "feed not parseable as RSS", "articles": []}
    articles = []
    for it in root.findall(".//item")[:n]:
        cats = [c.text.strip() for c in it.findall("category") if c.text]
        articles.append(
            {
                "title": (it.findtext("title") or "").strip(),
                "link": (it.findtext("link") or "").strip(),
                "published": (it.findtext("pubDate") or "").strip(),
                "categories": cats[:8],
            }
        )
    return {"feed": feed_url, "count": len(articles), "articles": articles}


def _tool_medium_articles(source: str, limit: int = 15) -> dict[str, Any]:
    """List a Medium author's or publication's recent articles via its public RSS feed (no auth). Accepts an @handle, a medium.com URL, or a Medium-backed custom domain (e.g. algonaut.space). Returns title, link, published date and tags — use to quantify a blog's output and spot cross-posting patterns."""
    s = (source or "").strip()
    if not s:
        return {"error": "source required"}
    feed_url = _medium_feed_url_for(s)
    n = max(1, min(int(limit), 30))
    return _parse_medium_rss(feed_url, n)


def _tool_reddit_history(user: str, _kind: str = "submitted", _limit: int = 15) -> dict[str, Any]:
    """PHASED OUT (owner decision 2026-07-16): reddit blocks this server's IP outright — every live call 403'd in prod traces (both compose sessions audited on 2026-07-15 burned a call each discovering this). The tool is no longer offered in the schema list; this stub stays registered so any cached/replayed prompt that still names it gets a truthful answer with ZERO network round-trips instead of a guaranteed 403."""
    return {
        "user": (user or "").strip(),
        "error": "reddit blocks requests from this server — no reddit data is "
        "available; use Bluesky or forum search for community sentiment",
        "items": [],
    }


_XGOV_RAW = "https://raw.githubusercontent.com/algorandfoundation/xGov/main"
_XGOV_API = "https://api.github.com/repos/algorandfoundation/xGov/contents"


def _xgov_frontmatter(md: str) -> dict[str, str]:
    """The `--- key: value ---` header every xGov proposal file starts with.

    Flat string values only — no YAML dependency needed.
    """
    lines = (md or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    fields: dict[str, str] = {}
    for line in lines[1:60]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fields[key.strip().lower()] = value.strip()
    return fields


def _xgov_abstract(md: str) -> str:
    m = re.search(r"(?im)^##\s*abstract\s*$([\s\S]*?)(?=^#|\Z)", md or "")
    return " ".join(m.group(1).split())[:400] if m else ""


def _xgov_fetch_one(pid: int, *, with_abstract: bool) -> dict[str, Any] | None:
    """One proposal's frontmatter summary (plus abstract if requested); None if it doesn't exist, or an {"id","error"} dict on fetch/parse failure."""
    try:
        resp = _guarded_get(f"{_XGOV_RAW}/Proposals/xgov-{pid}.md")
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except Exception as exc:
        return {"id": pid, "error": str(exc)[:160]}
    fm = _xgov_frontmatter(resp.text)
    if not fm:
        return {"id": pid, "error": "no frontmatter in proposal file"}
    entry: dict[str, Any] = {
        "id": pid,
        "title": fm.get("title", ""),
        "status": fm.get("status", ""),
        "author": fm.get("author", ""),
        "amount_requested_algo": fm.get("amount_requested", ""),
        "category": fm.get("category", ""),
        "period": fm.get("period", ""),
        "discussion": fm.get("discussions-to", ""),
        "url": f"https://github.com/algorandfoundation/xGov/blob/main/Proposals/xgov-{pid}.md",
    }
    if with_abstract:
        entry["abstract"] = _xgov_abstract(resp.text)
    return entry


def _xgov_list_recent(n: int) -> dict[str, Any]:
    """Newest xGov proposal summaries (no abstract), via the repo's Proposals directory listing."""
    try:
        resp = _github_get(f"{_XGOV_API}/Proposals")
        resp.raise_for_status()
        listing = resp.json() or []
    except Exception as exc:
        return {"error": f"could not list proposals: {str(exc)[:160]}"}
    ids: list[int] = []
    for item in listing:
        m = re.fullmatch(r"xgov-(\d+)\.md", str(item.get("name", "")))
        if m:
            ids.append(int(m.group(1)))
    ids.sort(reverse=True)
    proposals = [p for pid in ids[:n] if (p := _xgov_fetch_one(pid, with_abstract=False))]
    return {
        "source": "github.com/algorandfoundation/xGov",
        "total_proposals": len(ids),
        "count": len(proposals),
        "proposals": proposals,
        "note": "pass proposal_id for full detail incl. abstract",
    }


def _tool_xgov_proposal(proposal_id: int = 0, limit: int = 8) -> dict[str, Any]:
    """Status of Algorand xGov grant proposals from the canonical algorandfoundation/xGov repo: frontmatter (title, author, amount_requested, category, status Draft/Final/Approved/Rejected/Withdrawn, forum link) plus an abstract snippet. With proposal_id, one proposal in full; without, the newest proposals' summaries."""
    if proposal_id:
        entry = _xgov_fetch_one(int(proposal_id), with_abstract=True)
        if entry is None:
            return {
                "id": int(proposal_id),
                "error": "no such proposal in the xGov repo — note proposal ids are "
                "the repo's small sequential numbers (e.g. 100), not on-chain "
                "app/asset ids; for live on-chain vote tallies use "
                "lookup_application on the voting app instead",
            }
        return entry
    n = max(1, min(int(limit), 10))
    return _xgov_list_recent(n)


_GITHUB_SCHEMA = {
    "type": "function",
    "function": {
        "name": "github_activity",
        "description": (
            "Recent GitHub activity for a repo (metadata, releases, commits, top "
            "contributors) — for shipped updates, dev momentum, or who builds a "
            "project. Pass 'owner/name' or a github.com URL; a bare owner/org lists "
            "its repos to pick from — that list is only the 8 MOST RECENTLY PUSHED "
            "repos, sorted by recency not stars, so use total_stars_across_all_repos "
            "(not the per-repo 'stars' in that list) for any org-wide star/adoption "
            "claim. An 'archived: true' repo does NOT mean the project is dead — it "
            "routinely means migration. When archived, this also returns "
            "'owner_liveness' (the owner's other repos); if the owner is still "
            "pushing elsewhere, the project is alive — never call it defunct on the "
            "archived flag alone."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {
                    "type": "string",
                    "description": "owner/name, a github.com URL, or a bare owner/org",
                },
                "limit": {"type": "integer", "description": "1-10 releases/commits, default 5"},
            },
            "required": ["repo"],
        },
    },
}

_GITHUB_SEARCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "github_repository_search",
        "description": (
            "Search all of GitHub by keyword for repos matching a project — use "
            "when github_activity's owner/repo guess 404s and the real GitHub "
            "org/owner isn't known (a project's own site rarely spells it out). "
            "Not scoped to one owner."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "keywords, e.g. 'CompX Algorand' or a project name",
                },
                "limit": {"type": "integer", "description": "1-10 results, default 5"},
            },
            "required": ["query"],
        },
    },
}

_TOKEN_LISTINGS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_token_listings",
        "description": (
            "Check whether an Algorand ASA is actually listed/tradeable on "
            "Tinyman and Pact (the two biggest Algorand DEXs) — real liquidity, "
            "price, and 24h/7d volume in USD, or confirmation it's not listed "
            "anywhere. Use before reporting a token as tradeable, and use a real "
            "'not listed anywhere' result as a notable fact, not a dead end."
        ),
        "parameters": {
            "type": "object",
            "properties": {"asset_id": {"type": "integer", "description": "numeric ASA id"}},
            "required": ["asset_id"],
        },
    },
}

_FETCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": (
            "Fetch a web page and return its cleaned text — the actual article "
            "or blog post behind a search_web snippet. Long pages return one "
            "window at a time. When has_more is true, call fetch_url AGAIN with "
            "the SAME url and continue_reading=true to read the next section. "
            "In-content links are kept inline as 'label (url)' and, on the first "
            "window only, returned as a `links` array of {text,url}."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "max_chars": {
                    "type": "integer",
                    "description": "Characters per window, default 6000 (max 12000)",
                },
                "continue_reading": {
                    "type": "boolean",
                    "description": (
                        "false (default) = read from the start of the page. "
                        "true = continue the same url from where the previous "
                        "fetch_url left off when has_more was true."
                    ),
                },
            },
            "required": ["url"],
        },
    },
}

_CLICK_ELEMENT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "click_element",
        "description": (
            "Click a specific element (button, tab, footer item) on a page "
            "by its visible text, and return the page's content AFTER the "
            "click. Many SPA 'links' are actually buttons with no real "
            "href — fetch_url can't see content that only appears via a JS "
            "action (an in-page modal, a tab switch, a non-standard "
            "toggle). Use when a page visibly has such a control and its "
            "content matters to a claim you're making. Slow (loads a full "
            "browser) — expect several seconds. On no match, the error "
            "lists what text WAS clickable so you can retry."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "click_text": {
                    "type": "string",
                    "description": "visible text of the element to click, e.g. 'About this project'",
                },
            },
            "required": ["url", "click_text"],
        },
    },
}

_TYPE_INTO_PAGE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "type_into_page",
        "description": (
            "Type into a search box, filter, or form field on a page "
            "(matched by its label/placeholder) and return the page's "
            "content afterward. Use when real content only appears after "
            "typing — an on-chain explorer's address search, a "
            "directory's filter field, a docs site's search box. Set "
            "submit=true to press Enter after filling (e.g. a search box "
            "that navigates on submit); leave it false for a filter that "
            "reacts live as you type. Slow (loads a full browser) — "
            "expect several seconds. On no match, the error lists what "
            "fields WERE on the page so you can retry."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "field_text": {
                    "type": "string",
                    "description": "label, placeholder, or name of the field to type into, e.g. 'Search address'",
                },
                "value": {"type": "string", "description": "text to type into the field"},
                "submit": {
                    "type": "boolean",
                    "description": "press Enter after typing, default false",
                },
            },
            "required": ["url", "field_text", "value"],
        },
    },
}

_CAPTURE_SCREENSHOT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "capture_screenshot",
        "description": (
            "Screenshot a live page and get back a public image_url to "
            "illustrate the article — real visual evidence (a game's "
            "actual UI, a dashboard's current state, a marketplace "
            "listing) instead of describing it in prose alone. Use "
            "narrowly: reach for this when a screenshot genuinely shows "
            "something prose can't, not as a decorative addition — most "
            "articles are already well served by the source's own "
            "automatically-resolved og:image. full_page captures the "
            "whole scrollable page (e.g. a long leaderboard); the default "
            "(viewport only) is what a real visitor sees without "
            "scrolling. Slow (loads a full browser) — expect several "
            "seconds. You will be shown the actual image right after this "
            "call returns — look at it before deciding. If it genuinely "
            "earns a place in the article, EMBED IT YOURSELF in the "
            "article body as markdown: ![caption](image_url), using the "
            "image_url value from this tool's own result. Nothing does "
            "this for you — if you don't write that line, the screenshot "
            "never appears to a reader no matter how useful it looked."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "full_page": {
                    "type": "boolean",
                    "description": "capture the whole scrollable page instead of just the viewport, default false",
                },
            },
            "required": ["url"],
        },
    },
}

_PLAY_INTERACTIVE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "play_interactive",
        "description": (
            "Explore a live web app/game across MULTIPLE steps on the SAME "
            "page state, to discover how its mechanics actually work — "
            "what a button does, what a menu reveals, how a form "
            "responds. Unlike fetch_url/click_element/type_into_page, "
            "which each start from a fresh page every call, this keeps "
            "ONE page open across a short sequence of actions so you can "
            "act on what actually happened, not just the page's start "
            "state. Use to discover mechanics, NOT to master, complete, "
            "or grind a game — a handful of exploratory steps is the "
            "point. Bounded to a small step budget per compose (see the "
            "budget field in each response); once it's spent, no more "
            "steps are available this compose. "
            "action='open' (needs url) starts a session, closing any "
            "previous one. "
            "action='click' (needs target) clicks visible text on the "
            "current page. "
            "action='type' (needs target + value, optional submit) types "
            "into a field on the current page. "
            "action='read' re-reads the current page with no action — "
            "useful after a timer/animation. "
            "action='close' ends the session early, before the budget "
            "runs out. "
            "Slow (loads/interacts with a full browser) — expect several "
            "seconds per step."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["open", "click", "type", "read", "close"],
                },
                "url": {"type": "string", "description": "required for action='open'"},
                "target": {
                    "type": "string",
                    "description": "visible text to click (action='click'), or field label/placeholder to type into (action='type')",
                },
                "value": {"type": "string", "description": "text to type, for action='type'"},
                "submit": {
                    "type": "boolean",
                    "description": "press Enter after typing, for action='type', default false",
                },
            },
            "required": ["action"],
        },
    },
}

_EXTRACT_PDF_SCHEMA = {
    "type": "function",
    "function": {
        "name": "extract_pdf_from_page",
        "description": (
            "Find and read the actual PDF behind a page that embeds it in a "
            "JS-based viewer (Google Docs viewer, PDF.js, a slide-deck "
            "embed) instead of linking it directly — fetch_url only reads "
            "the wrapper page's own chrome/text in that case, not the "
            "document itself. Also works directly if url is already a "
            "plain PDF link. Returns up to 40 pages of extracted text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "the page that displays/embeds the PDF (or a direct PDF URL)",
                },
            },
            "required": ["url"],
        },
    },
}

_FETCH_GOOGLE_DOC_SCHEMA = {
    "type": "function",
    "function": {
        "name": "fetch_google_doc",
        "description": (
            "Read a publicly-shared Google Doc's full plain text — "
            "fetch_url on a docs.google.com/document/... URL only sees the "
            "JS editor shell loading, not the document's words. Only works "
            "when the doc's sharing is 'Anyone with the link can view'; a "
            "private doc reports that plainly instead of an empty page. "
            "Paginated like fetch_url — call again with the same url and a "
            "later offset (from has_more) to keep reading."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "the docs.google.com/document/d/... URL"},
                "max_chars": {
                    "type": "integer",
                    "description": "characters per window, default 6000 (max 12000)",
                },
                "offset": {
                    "type": "integer",
                    "description": "0 (default) = start; pass a later offset to continue reading",
                },
            },
            "required": ["url"],
        },
    },
}

_INSPECT_NETWORK_HOSTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "inspect_network_hosts",
        "description": (
            "Load a page and report which hosts it ACTUALLY made network "
            "requests to, with a best-effort mainnet-vs-testnet call from "
            "known Algorand algod/indexer hostnames -- ground truth for "
            "what a dapp really does, immune to stale or wrong page copy. "
            "Use this for any 'is this mainnet or testnet' / 'are fees "
            "real' claim instead of quoting the page's own text or "
            "grep_frontend_bundle, either of which can be stale relative "
            "to what the app's code is actually wired to run against. Pass "
            "click_text (e.g. 'Connect Wallet') when the relevant network "
            "call only fires after an interaction. Slow (loads a full "
            "browser) — expect several seconds. "
            "detected_network:'unknown' does NOT mean the network can't be "
            "determined -- it means this specific method (watching the "
            "BROWSER's own requests) found no algod/indexer host, which "
            "happens whenever a site's real chain calls run server-side "
            "behind its own backend (e.g. a Base44-hosted app) rather than "
            "client-side -- confirmed live 2026-08-15 on lumirogue.com, "
            "where even clicking through to a specific wallet only revealed "
            "a WalletConnect relay host, never an Algorand endpoint, "
            "because the app's chain calls never cross the browser at all. "
            "On 'unknown', do NOT default to trusting the page's own "
            "Mainnet/Testnet label -- instead resolve it independently via "
            "an on-chain query tool (application_boxes, lookup_asset, "
            "lookup_application) against any app/asset ID already found on "
            "the page: if the ID resolves on one network, that's your "
            "answer, immune to this specific site's backend architecture."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "click_text": {
                    "type": "string",
                    "description": "optional: visible text of an element to click first, e.g. 'Connect Wallet'",
                },
            },
            "required": ["url"],
        },
    },
}

_GREP_BUNDLE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "grep_frontend_bundle",
        "description": (
            "Search a page's own client-side JS bundles for a literal string "
            "— use when a claim depends on what a single-page app's code "
            "actually DOES (requires a wallet connection, enforces a fee, "
            "gates a feature) rather than what its rendered page shows. "
            "fetch_url only sees rendered/DOM text; SPA logic that never "
            "prints to the page is invisible to it. A miss across every "
            "bundle means the term genuinely isn't in the client code, not "
            "that the check failed. NOT reliable for mainnet-vs-testnet or "
            "'where does this button actually go' -- a minified bundle "
            "often defines several config objects (a wallet library's "
            "mainnet AND testnet blocks both exist as text) and resolves "
            "which one is actually used through variable indirection this "
            "text search can't trace, and a click handler's real target is "
            "rarely textually near the button's own label. Use "
            "inspect_network_hosts for network identity and click_element "
            "for where a button actually goes; both observe real behavior "
            "instead of reading text."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "the app's page URL"},
                "search_term": {
                    "type": "string",
                    "description": "literal substring to search for, e.g. 'connectWallet' or 'requireSignature'",
                },
                "max_matches": {
                    "type": "integer",
                    "description": "1-20 matches to return, default 5",
                },
            },
            "required": ["url", "search_term"],
        },
    },
}

_DEFILLAMA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_defi_tvl",
        "description": (
            "Current DeFi Total Value Locked (USD) from DeFiLlama. No argument → "
            "Algorand chain TVL; a protocol slug (tinyman, folks-finance, pact) → "
            "that protocol's TVL. Use for concrete adoption/size numbers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "protocol": {
                    "type": "string",
                    "description": "optional protocol slug, e.g. 'tinyman'",
                },
            },
        },
    },
}

_DEFILLAMA_HISTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_defi_tvl_history",
        "description": (
            "Historical DeFi TVL trend for one protocol from DeFiLlama (USD), "
            "downsampled to one point per month. Use to show an adoption or "
            "decline CURVE over time — get_defi_tvl only gives today's single "
            "number, so a 'growing' or 'shrinking' claim needs this instead of "
            "inferring a trend from one snapshot."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "protocol": {
                    "type": "string",
                    "description": "protocol slug, e.g. 'tinyman' (required — this has no chain-wide mode)",
                },
                "months": {
                    "type": "integer",
                    "description": "how many trailing months to return, 1-60, default 24",
                },
            },
            "required": ["protocol"],
        },
    },
}


_DISCOURSE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "discourse_forum",
        "description": (
            "Live activity from a Discourse community forum (most crypto project "
            "forums, including Folks Finance) via its public JSON API: site stats "
            "(topic/post/user counts, last-day activity), top categories, and recent "
            "topics with reply/view counts. Pass `query` to SEARCH the forum's "
            "discussions for a project/term instead of listing latest topics. Use to "
            "gauge what the community is discussing NOW instead of a stale page "
            "snapshot. Pass the forum's base URL, e.g. https://forum.folks.finance — "
            "the official Algorand forum is https://forum.algorand.org."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "forum_url": {
                    "type": "string",
                    "description": "forum base URL, e.g. https://forum.folks.finance",
                },
                "query": {
                    "type": "string",
                    "description": (
                        "optional search term (project name, topic) — searches the "
                        "forum instead of listing latest topics"
                    ),
                },
                "limit": {"type": "integer", "description": "1-25 results, default 10"},
            },
            "required": ["forum_url"],
        },
    },
}

_NODE_STATS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "get_node_stats",
        "description": (
            "Algorand mainnet node telemetry from Nodely: the latest daily estimate "
            "of full-time running NODES plus the recent trend. Use for network "
            "decentralization / health / participation-scale context. NOTE: this is a "
            "NODE count (off-chain telemetry from g.nodely.io); for on-chain online "
            "STAKE use get_consensus_stats instead."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_GITHUB_CONTENTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "github_repository_contents",
        "description": (
            "Inspect a GitHub repo's files. Empty path → root listing; a directory "
            "path → its entries; a file path → the file's decoded text. Use to READ "
            "smart-contract source and assess what a project actually shipped "
            "(github_activity only gives metadata/commits). Pass 'owner/name' or a "
            "github.com URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "owner/name or github.com URL"},
                "path": {"type": "string", "description": "file or dir path; empty = repo root"},
                "ref": {"type": "string", "description": "branch/tag/commit, optional"},
            },
            "required": ["repo"],
        },
    },
}

_MEDIUM_SCHEMA = {
    "type": "function",
    "function": {
        "name": "medium_api_article_list",
        "description": (
            "List a Medium author's or publication's recent articles via its public "
            "RSS feed (no auth): title, link, published date and tags. Use to quantify "
            "a blog's output or spot cross-posting. Pass an @handle, a medium.com URL, "
            "or a Medium-backed custom domain (e.g. algonaut.space)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": "@handle, medium URL, or custom domain",
                },
                "limit": {"type": "integer", "description": "1-30, default 15"},
            },
            "required": ["source"],
        },
    },
}

_XGOV_SCHEMA = {
    "type": "function",
    "function": {
        "name": "xgov_proposal_status",
        "description": (
            "Status of Algorand xGov grant proposals from the canonical "
            "algorandfoundation/xGov GitHub repo: title, author, ALGO amount "
            "requested, category, forum discussion link and status (Draft/Final/"
            "Approved/Rejected/Withdrawn). Pass proposal_id (the small xGov "
            "number, e.g. 100 — NOT an on-chain app/asset id) for one proposal "
            "with its abstract; omit it to list the newest proposals. For live "
            "on-chain vote tallies use lookup_application on the voting app."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "proposal_id": {
                    "type": "integer",
                    "description": "xGov proposal number (e.g. 100); omit to list newest",
                },
                "limit": {
                    "type": "integer",
                    "description": "1-10 proposals when listing, default 8",
                },
            },
            "required": [],
        },
    },
}


def research_tools() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enabled external research tools as (schemas, handlers).

    search_web needs SEARXNG_URL, search_bluesky needs an app-password, and
    telegram_channel_lookup needs TELEGRAM_BOT_TOKEN (already configured for
    Telegram distribution), so they register only when usable. github_activity,
    github_repository_search, github_repository_contents, search_token_listings,
    fetch_url, click_element, get_defi_tvl, get_defi_tvl_history, discourse_forum, get_node_stats,
    medium_api_article_list, package_download_stats, search_nfd_directory,
    app_store_metrics, reddit_api_post_history, xgov_proposal_status and
    lookup_asset_market_data hit free public APIs and are always available
    (GITHUB_TOKEN optional).
    """
    import os

    from app.core.config import BLUESKY_SEARCH_ENABLED, SEARXNG_URL

    schemas: list[dict[str, Any]] = [
        _GITHUB_SCHEMA,
        _GITHUB_SEARCH_SCHEMA,
        _GITHUB_CONTENTS_SCHEMA,
        _TOKEN_LISTINGS_SCHEMA,
        _FETCH_SCHEMA,
        _CLICK_ELEMENT_SCHEMA,
        _TYPE_INTO_PAGE_SCHEMA,
        _CAPTURE_SCREENSHOT_SCHEMA,
        _PLAY_INTERACTIVE_SCHEMA,
        _EXTRACT_PDF_SCHEMA,
        _INSPECT_NETWORK_HOSTS_SCHEMA,
        _GREP_BUNDLE_SCHEMA,
        _FETCH_GOOGLE_DOC_SCHEMA,
        _DEFILLAMA_SCHEMA,
        _DEFILLAMA_HISTORY_SCHEMA,
        _DISCOURSE_SCHEMA,
        _NODE_STATS_SCHEMA,
        _MEDIUM_SCHEMA,
        _PACKAGE_DOWNLOADS_SCHEMA,
        _NFD_DIRECTORY_SCHEMA,
        _LIST_NFD_SEGMENTS_SCHEMA,
        _APP_STORE_METRICS_SCHEMA,
        # reddit_api_post_history deliberately has NO schema (2026-07-16):
        # reddit blocks this server's IP — offering the tool just burned one
        # 403 per session. Its stub handler (still registered below) answers
        # any stale reference truthfully with zero network round-trips.
        _XGOV_SCHEMA,
    ]
    handlers: dict[str, Any] = {
        "github_activity": _tool_github_activity,
        "github_repository_search": _tool_github_repository_search,
        "github_repository_contents": _tool_github_repo_contents,
        "search_token_listings": _tool_search_token_listings,
        "fetch_url": _tool_fetch_url,
        "click_element": _tool_click_element,
        "type_into_page": _tool_type_into_page,
        "capture_screenshot": _tool_capture_screenshot,
        "play_interactive": _tool_play_interactive,
        "extract_pdf_from_page": _tool_extract_pdf_from_page,
        "inspect_network_hosts": _tool_inspect_network_hosts,
        "grep_frontend_bundle": _tool_grep_frontend_bundle,
        "fetch_google_doc": _tool_fetch_google_doc,
        "get_defi_tvl": _tool_get_defi_tvl,
        "get_defi_tvl_history": _tool_get_defi_tvl_history,
        "discourse_forum": _tool_discourse_forum,
        "get_node_stats": _tool_get_node_stats,
        "medium_api_article_list": _tool_medium_articles,
        "package_download_stats": _tool_package_download_stats,
        "search_nfd_directory": _tool_search_nfd_directory,
        "list_nfd_segments": _tool_list_nfd_segments,
        "app_store_metrics": _tool_app_store_metrics,
        "reddit_api_post_history": _tool_reddit_history,
        "xgov_proposal_status": _tool_xgov_proposal,
    }
    if SEARXNG_URL:
        schemas.append(_WEB_SCHEMA)
        handlers["search_web"] = _tool_search_web
    bsky_ready = bool(
        os.getenv("BLUESKY_IDENTIFIER", "").strip()
        and os.getenv("BLUESKY_APP_PASSWORD", "").strip()
    )
    if BLUESKY_SEARCH_ENABLED and bsky_ready:
        schemas.append(_BLUESKY_SCHEMA)
        handlers["search_bluesky"] = _tool_search_bluesky
    from app.core.config import X_SEARCH_ENABLED

    # No X_BEARER_TOKEN check here (unlike before 2026-08-25): the tool now
    # only ever reads the weekly-swept Cassandra cache, never calls X
    # directly -- the token is needed by the sweep task alone. Gating on
    # X_SEARCH_ENABLED still keeps this the feature's single master switch.
    if X_SEARCH_ENABLED:
        schemas.append(_X_SEARCH_SCHEMA)
        handlers["search_x"] = _tool_search_x
    from app.core.config import TELEGRAM_BOT_TOKEN

    if TELEGRAM_BOT_TOKEN:
        schemas.append(_TELEGRAM_LOOKUP_SCHEMA)
        handlers["telegram_channel_lookup"] = _tool_telegram_channel_lookup
    schemas.append(_DISCORD_INVITE_STATS_SCHEMA)
    handlers["lookup_discord_invite_stats"] = _tool_lookup_discord_invite_stats
    schemas.append(_WORLD_POPULATION_SCHEMA)
    handlers["lookup_world_population"] = _tool_lookup_world_population
    schemas.append(_ASSET_MARKET_DATA_SCHEMA)
    handlers["lookup_asset_market_data"] = _tool_lookup_asset_market_data
    return schemas, handlers
