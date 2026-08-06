"""External research tools the writer can call on demand.

- search_web: general web research via self-hosted SearXNG (no Google, no key,
  no per-query cost). SEARXNG_URL is operator-configured and trusted, so it is
  called directly; any RESULT url the model later fetches still goes through the
  SSRF-guarded fetch tool.
- search_bluesky: free public Bluesky post search for community sentiment.
  Public AppView needs no auth. Bluesky is a public host, so it rides the SSRF
  guard like any other untrusted fetch.

Every handler is failure-tolerant: an error returns {"error": ...} and never
aborts the article.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

logger = logging.getLogger(__name__)

_UA = "algorand-platform-newspaper/1.0 (+https://algorand.pxke.me)"
# searchPosts requires an authenticated session (the public AppView 403s it), so
# we mint an app-password session against the entryway and call it with a Bearer.
_BSKY_CREATE_SESSION = "https://bsky.social/xrpc/com.atproto.server.createSession"
_BSKY_SEARCH = "https://bsky.social/xrpc/app.bsky.feed.searchPosts"
_bsky_token_cache: dict[str, float | str] = {}


def _tool_search_web(query: str, limit: int = 6) -> dict[str, Any]:
    """General web search via SearXNG: titles, URLs and snippets a journalist would skim before writing. Use to discover sources and context you were not handed; then fetch the most relevant URL with the safe fetch tool. Also queries news-specific engines (Bing News, DuckDuckGo News, Google News) for a real publish-date signal — general engines rarely return one at all."""
    import httpx

    from app.core.config import SEARXNG_URL

    if not SEARXNG_URL:
        return {"query": query, "error": "web search not configured", "results": []}
    q = (query or "").strip()
    if not q:
        return {"query": query, "results": []}
    n = max(1, min(int(limit), 12))
    try:
        with httpx.Client(timeout=12.0, headers={"User-Agent": _UA}) as client:
            resp = client.get(
                f"{SEARXNG_URL}/search",
                params={
                    "q": q,
                    "format": "json",
                    "categories": "general,news",
                    "language": "en",
                },
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


def _bsky_access_token() -> str:
    """App-password session token, cached ~50 min. Empty when not configured."""
    import os
    import time

    import httpx

    ident = os.getenv("BLUESKY_IDENTIFIER", "").strip()
    pw = os.getenv("BLUESKY_APP_PASSWORD", "").strip()
    if not ident or not pw:
        return ""
    cached = _bsky_token_cache.get("token")
    expires = _bsky_token_cache.get("expires", 0.0)
    if isinstance(cached, str) and isinstance(expires, float) and time.time() < expires:
        return cached
    try:
        resp = httpx.post(
            _BSKY_CREATE_SESSION,
            json={"identifier": ident, "password": pw},
            headers={"User-Agent": _UA},
            timeout=12.0,
        )
        resp.raise_for_status()
        token = str(resp.json().get("accessJwt") or "")
    except Exception:
        return ""
    if token:
        _bsky_token_cache["token"] = token
        _bsky_token_cache["expires"] = time.time() + 3000.0
    return token


def _tool_search_bluesky(query: str, limit: int = 10) -> dict[str, Any]:
    """Recent public Bluesky posts matching a query — community sentiment and discussion. Returns post text + engagement so the writer judges the mood; a post is social opinion, never cited as established fact."""
    from app.core.net_guard import guarded_get

    q = (query or "").strip()
    if not q:
        return {"query": query, "posts": []}
    token = _bsky_access_token()
    if not token:
        return {"query": query, "error": "bluesky not configured", "posts": []}
    n = max(1, min(int(limit), 25))
    try:
        resp = guarded_get(
            _BSKY_SEARCH,
            params={"q": q, "limit": n, "sort": "top"},
            headers={"User-Agent": _UA, "Authorization": f"Bearer {token}"},
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
        from app.modules.ai.mistral_client import _retry_after_seconds

        retry_after = _retry_after_seconds(resp)
        if retry_after is not None:
            return min(_FETCH_BACKOFF_MAX_SECONDS, retry_after)
        return min(_FETCH_BACKOFF_MAX_SECONDS, _FETCH_BACKOFF_BASE_SECONDS * (3**attempt))
    return min(_FETCH_BACKOFF_MAX_SECONDS, _FETCH_BACKOFF_BASE_SECONDS * (2**attempt))


def _guarded_get_with_retry(
    url: str, *, headers: dict | None = None, timeout: float = 12.0
) -> httpx.Response:
    """`_guarded_get` with retry: transient network errors and 429/5xx responses get up to 5 attempts with exponential backoff, capped at 60s per wait (429 backs off harder, honoring Retry-After when the server sends one). SSRF rejections and real 4xx responses are permanent, so they fail immediately."""
    import time

    from app.core.net_guard import UnsafeUrlError

    resp = None
    last_exc: Exception | None = None
    for attempt in range(_FETCH_MAX_ATTEMPTS):
        try:
            resp = _guarded_get(url, headers=headers, timeout=timeout)
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
    import httpx

    from app.core.net_guard import assert_public_url

    assert_public_url(url)
    h = {"User-Agent": _UA, "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        return client.post(url, json=json, headers=h)


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


def _github_owner_repos(owner: str) -> dict[str, Any]:
    """Repo list for a GitHub org/user, most recently pushed first — returned when the model passes an owner instead of owner/name (the top prod failure mode for this tool), so it can pick a repo and call again instead of dead-ending."""
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
    return {
        "owner": owner,
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
        "hint": "call github_activity again with one of these 'owner/name' repos",
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
    except Exception:
        return []


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
    except Exception:
        return []


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
    except Exception:
        return []


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
    """Whether an Algorand ASA is actually listed/tradeable on the two biggest Algorand DEXs (Tinyman, Pact) — real liquidity, price, and 24h/7d volume in USD, or confirmation it's NOT listed anywhere. Use this instead of assuming a token trades just because it exists; a real supply with zero listings is itself a notable fact worth reporting."""
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
            "snapshot of it if the historical content matters"
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
) -> tuple[str, str, list[dict[str, str]], str]:
    """Retry a thin/SPA-shaped response (React/Vue/Next shell, or a "please enable JavaScript" page) with the Playwright renderer — same signal the web crawler uses. Returns (title, text, links, base), rendered values if it helped, the original ones otherwise (a failed render beats no result at all)."""
    from app.modules.scraper.crawler_registry import is_web_spa_enabled
    from app.modules.scraper.crawlers.web_crawler import needs_spa_fallback

    if not (is_web_spa_enabled() and needs_spa_fallback(plain_text, raw_html=resp.text)):
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
    base = str(resp.url)
    # PDFs (whitepapers, audits, tokenomics docs) are common source links; read
    # their text instead of refusing them. pypdf is already a dependency.
    if "pdf" in ctype.lower() or base.lower().split("?")[0].endswith(".pdf"):
        return _fetch_pdf_document(resp, base=base, cap=cap, offset=offset)
    if "html" not in ctype and "text" not in ctype:
        return {"url": u, "error": f"unsupported content-type: {ctype[:60]}"}

    title, text, plain_text, links = _extract_html_text_and_links(resp, base=base)
    title, text, links, base = _maybe_render_spa_fallback(
        resp, base=base, title=title, text=text, plain_text=plain_text, links=links
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


def _tool_fetch_url(
    url: str,
    max_chars: int = 6000,
    offset: int = 0,
) -> dict[str, Any]:
    """Fetch a web page and return its cleaned main text (public tool result)."""
    raw = _fetch_url_internal(url, max_chars=max_chars, offset=offset)
    if raw.get("error"):
        return raw
    result = _publicize_fetch_result(raw)
    try:
        result = _augment_github_archived(url, result)
    except Exception:
        logger.debug("github-archived augmentation failed", exc_info=True)
    return result


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
            verified = ((data.get("properties") or {}).get("verified") or {})
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


_NFD_DIRECTORY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_nfd_directory",
        "description": (
            "Resolve an Algorand NFD (.algo human-readable name) to its owner "
            "address, or reverse-resolve an address to the .algo name(s) it "
            "owns — via NFDomains' own public API. Use to verify a claimed "
            ".algo identity actually resolves on-chain, or to find the name "
            "behind an address you already have. A name lookup also returns "
            "any Discord/GitHub/X/Bluesky/Telegram handles and additional "
            "Algorand addresses the owner has cryptographically VERIFIED as "
            "theirs — real corroboration for who is behind a project, not "
            "self-reported claims; a missing field means unverified, not "
            "confirmed absent."
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


def _discourse_categories(base: str, hdr: dict[str, str]) -> tuple[list[dict[str, Any]], dict]:
    """Top categories (name/topic count/description) plus an id -> name lookup for topic annotation."""
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
        return categories, cat_names
    except Exception:
        return [], {}


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

    categories, cat_names = _discourse_categories(base, hdr)
    out["categories"] = categories
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
        "description": (guild.get("description") or profile.get("description") or "")[:300]
        or None,
    }


def _rdap_registrar_name(entities: list[Any]) -> str | None:
    for e in entities:
        if not isinstance(e, dict) or "registrar" not in (e.get("roles") or []):
            continue
        for field in e.get("vcardArray") or []:
            if not isinstance(field, list) or len(field) != 2:
                continue
            for entry in field[1:]:
                if isinstance(entry, list) and len(entry) == 4 and entry[0] == "fn":
                    return entry[3]
    return None


def _tool_lookup_domain_registration(domain: str) -> dict[str, Any]:
    """Registration/expiration date and registrar for a domain, via RDAP (the standardized, free WHOIS successor — rdap.org auto-routes to the correct registry, no key needed). A domain registered weeks ago vs. years ago is a real legitimacy/maturity signal a reader deserves, and this is the on-the-record source for it rather than guessing from how polished a site looks."""
    import re

    raw = (domain or "").strip().lower()
    raw = re.sub(r"^https?://", "", raw).split("/")[0]
    host = re.sub(r"^www\.", "", raw)
    if not host or "." not in host:
        return {"error": "a valid domain is required, e.g. example.com"}
    try:
        resp = _guarded_get(f"https://rdap.org/domain/{host}", timeout=15.0)
    except Exception as exc:
        return {"domain": host, "error": str(exc)[:200]}
    if resp.status_code == 404:
        return {"domain": host, "found": False, "error": "no RDAP record (unregistered, or a ccTLD RDAP.org doesn't route)"}
    if resp.status_code != 200:
        return {"domain": host, "error": f"RDAP {resp.status_code}"}
    try:
        data = resp.json()
    except Exception:
        return {"domain": host, "error": "unexpected RDAP response"}
    events = {e.get("eventAction"): e.get("eventDate") for e in data.get("events") or []}
    return {
        "domain": host,
        "found": True,
        "registered_at": events.get("registration"),
        "expires_at": events.get("expiration"),
        "last_changed_at": events.get("last changed"),
        "registrar": _rdap_registrar_name(data.get("entities") or []),
    }


_DOMAIN_REGISTRATION_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lookup_domain_registration",
        "description": (
            "Registration date, expiration date, and registrar for a domain, "
            "via RDAP (the standardized WHOIS successor, no key needed). Use "
            "to check whether a project's site is brand-new or established — "
            "a domain registered weeks ago is a real, checkable signal, not "
            "a guess from how the site looks."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "bare domain or a URL containing one, e.g. 'example.com'",
                },
            },
            "required": ["domain"],
        },
    },
}


def _wayback_capture_date(resp: httpx.Response) -> str | None:
    """The capture date (YYYY-MM-DD) from a CDX API response's single data row, or None — row 0 is always a header, not data."""
    try:
        rows = resp.json()
    except Exception:
        return None
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    ts = rows[1][1] if isinstance(rows[1], list) and len(rows[1]) > 1 else None
    if not isinstance(ts, str) or len(ts) < 8:
        return None
    return f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}"


def _tool_lookup_wayback_snapshots(url: str) -> dict[str, Any]:
    """First and most recent known Internet Archive snapshot dates for a URL, via the Wayback Machine's CDX API (free, no key). Use to check how long a site has actually existed, or whether its content changed recently, instead of trusting a fetch_url's current state as the whole history — root-caused 2026-08-06: a compose tried to fetch archive.ph directly for exactly this kind of check and hit a 429, with no fallback."""
    raw = (url or "").strip()
    if not raw:
        return {"error": "url is required"}
    try:
        first_resp = _guarded_get(
            "https://web.archive.org/cdx/search/cdx",
            params={"url": raw, "output": "json", "limit": "1"},
            timeout=20.0,
        )
        last_resp = _guarded_get(
            "https://web.archive.org/cdx/search/cdx",
            params={"url": raw, "output": "json", "limit": "-1"},
            timeout=20.0,
        )
    except Exception as exc:
        return {"url": raw, "error": str(exc)[:200]}
    if first_resp.status_code != 200 or last_resp.status_code != 200:
        return {
            "url": raw,
            "error": f"wayback CDX {first_resp.status_code}/{last_resp.status_code}",
        }
    first_seen = _wayback_capture_date(first_resp)
    last_seen = _wayback_capture_date(last_resp)
    if first_seen is None and last_seen is None:
        return {"url": raw, "found": False, "error": "no archive.org snapshots found"}
    return {"url": raw, "found": True, "first_seen": first_seen, "last_seen": last_seen}


_WAYBACK_SNAPSHOTS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "lookup_wayback_snapshots",
        "description": (
            "First and most recent Internet Archive (Wayback Machine) "
            "snapshot dates for a URL, free and no key needed. Use to check "
            "how long a site has actually existed, or to catch that its "
            "content changed recently, instead of relying only on what it "
            "shows right now."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "the URL to check"},
            },
            "required": ["url"],
        },
    },
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
            "Recent GitHub activity for a repo (metadata, latest releases, recent "
            "commits, and top contributors) — use to report shipped updates, version "
            "launches, dev momentum, or who actually builds a project. Pass "
            "'owner/name' or a github.com URL; passing just an owner/org lists its "
            "repositories so you can pick one. IMPORTANT: an 'archived: true' repo "
            "does NOT mean the project is dead — projects routinely archive an old "
            "repo after migrating. When a repo is archived this returns "
            "'owner_liveness' showing the owner's OTHER repos; if the owner is still "
            "pushing elsewhere, the project is alive (superseded repo), so never call "
            "it defunct or tell readers to migrate away on the archived flag alone."
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
    fetch_url, get_defi_tvl, discourse_forum, get_node_stats,
    medium_api_article_list, package_download_stats, search_nfd_directory,
    app_store_metrics, reddit_api_post_history and xgov_proposal_status hit
    free public APIs and are always available (GITHUB_TOKEN optional).
    """
    import os

    from app.core.config import BLUESKY_SEARCH_ENABLED, SEARXNG_URL

    schemas: list[dict[str, Any]] = [
        _GITHUB_SCHEMA,
        _GITHUB_SEARCH_SCHEMA,
        _GITHUB_CONTENTS_SCHEMA,
        _TOKEN_LISTINGS_SCHEMA,
        _FETCH_SCHEMA,
        _DEFILLAMA_SCHEMA,
        _DISCOURSE_SCHEMA,
        _NODE_STATS_SCHEMA,
        _MEDIUM_SCHEMA,
        _PACKAGE_DOWNLOADS_SCHEMA,
        _NFD_DIRECTORY_SCHEMA,
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
        "get_defi_tvl": _tool_get_defi_tvl,
        "discourse_forum": _tool_discourse_forum,
        "get_node_stats": _tool_get_node_stats,
        "medium_api_article_list": _tool_medium_articles,
        "package_download_stats": _tool_package_download_stats,
        "search_nfd_directory": _tool_search_nfd_directory,
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
    from app.core.config import TELEGRAM_BOT_TOKEN

    if TELEGRAM_BOT_TOKEN:
        schemas.append(_TELEGRAM_LOOKUP_SCHEMA)
        handlers["telegram_channel_lookup"] = _tool_telegram_channel_lookup
    schemas.append(_DISCORD_INVITE_STATS_SCHEMA)
    handlers["lookup_discord_invite_stats"] = _tool_lookup_discord_invite_stats
    schemas.append(_WORLD_POPULATION_SCHEMA)
    handlers["lookup_world_population"] = _tool_lookup_world_population
    schemas.append(_DOMAIN_REGISTRATION_SCHEMA)
    handlers["lookup_domain_registration"] = _tool_lookup_domain_registration
    schemas.append(_WAYBACK_SNAPSHOTS_SCHEMA)
    handlers["lookup_wayback_snapshots"] = _tool_lookup_wayback_snapshots
    return schemas, handlers
