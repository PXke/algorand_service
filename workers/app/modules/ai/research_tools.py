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

import re
from typing import Any

_UA = "algorand-platform-newspaper/1.0 (+https://algorand.pxke.me)"
# searchPosts requires an authenticated session (the public AppView 403s it), so
# we mint an app-password session against the entryway and call it with a Bearer.
_BSKY_CREATE_SESSION = "https://bsky.social/xrpc/com.atproto.server.createSession"
_BSKY_SEARCH = "https://bsky.social/xrpc/app.bsky.feed.searchPosts"
_bsky_token_cache: dict[str, float | str] = {}


def _tool_search_web(query: str, limit: int = 6) -> dict[str, Any]:
    """General web search via SearXNG: titles, URLs and snippets a journalist
    would skim before writing. Use to discover sources and context you were not
    handed; then fetch the most relevant URL with the safe fetch tool."""
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
                params={"q": q, "format": "json", "categories": "general", "language": "en"},
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        return {"query": query, "error": str(exc)[:200], "results": []}
    results = []
    for r in (data.get("results") or [])[:n]:
        results.append(
            {
                "title": (r.get("title") or "")[:200],
                "url": r.get("url") or "",
                "snippet": (r.get("content") or "")[:300],
            }
        )
    return {"query": query, "count": len(results), "results": results}


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
    """Recent public Bluesky posts matching a query — community sentiment and
    discussion. Returns post text + engagement so the writer judges the mood;
    a post is social opinion, never cited as established fact."""
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
            "General web search (SearXNG) — titles, URLs and snippets to discover "
            "sources and context you were not handed. Use this first when you need "
            "to research a topic; then fetch the best URL with the safe fetch tool."
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
):
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


def _fetch_backoff_seconds(attempt: int, resp: Any = None) -> float:
    """Backoff before retrying after `attempt` (0-based) fails. A 429 means the
    server is actively throttling us, so it honors Retry-After when sent and
    otherwise backs off harder than the plain exponential schedule used for
    transient network errors / 5xx."""
    if resp is not None and getattr(resp, "status_code", None) == 429:
        from app.modules.ai.mistral_client import _retry_after_seconds

        retry_after = _retry_after_seconds(resp)
        if retry_after is not None:
            return min(_FETCH_BACKOFF_MAX_SECONDS, retry_after)
        return min(_FETCH_BACKOFF_MAX_SECONDS, _FETCH_BACKOFF_BASE_SECONDS * (3**attempt))
    return min(_FETCH_BACKOFF_MAX_SECONDS, _FETCH_BACKOFF_BASE_SECONDS * (2**attempt))


def _guarded_get_with_retry(url: str, *, headers: dict | None = None, timeout: float = 12.0):
    """`_guarded_get` with retry: transient network errors and 429/5xx responses
    get up to 5 attempts with exponential backoff, capped at 60s per wait (429
    backs off harder, honoring Retry-After when the server sends one). SSRF
    rejections and real 4xx responses are permanent, so they fail immediately."""
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
    url: str, *, json: Any = None, headers: dict | None = None, timeout: float = 12.0
):
    """SSRF-guarded POST for a known external JSON API. Validates the host is
    public and does NOT follow redirects (so it can't be bounced to an internal
    one). Used for fixed endpoints we choose, not LLM-supplied URLs."""
    import httpx

    from app.core.net_guard import assert_public_url

    assert_public_url(url)
    h = {"User-Agent": _UA, "Content-Type": "application/json"}
    if headers:
        h.update(headers)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        return client.post(url, json=json, headers=h)


def _github_owner_repos(owner: str, headers: dict) -> dict[str, Any]:
    """Repo list for a GitHub org/user, most recently pushed first — returned when
    the model passes an owner instead of owner/name (the top prod failure mode for
    this tool), so it can pick a repo and call again instead of dead-ending."""
    try:
        resp = _guarded_get(
            f"https://api.github.com/users/{owner}/repos",
            params={"sort": "pushed", "per_page": 8},
            headers=headers,
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
            }
            for r in repos
            if isinstance(r, dict)
        ][:8],
        "hint": "call github_activity again with one of these 'owner/name' repos",
    }


def _tool_github_activity(repo: str, limit: int = 5) -> dict[str, Any]:
    """Recent activity for a GitHub repo: metadata, latest releases and commits.
    Accepts 'owner/name' or a github.com URL; a bare owner/org lists its repos."""
    import os

    slug = (repo or "").strip().rstrip("/")
    if "github.com/" in slug:
        slug = slug.split("github.com/", 1)[1]
    slug = "/".join(slug.split("/")[:2])
    if slug.endswith(".git"):
        slug = slug[:-4]
    n = max(1, min(int(limit), 10))
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if "/" not in slug and slug:
        # An org/user, not a repo — list its repos instead of erroring.
        return _github_owner_repos(slug, headers)
    if slug.count("/") != 1 or not all(slug.split("/")):
        return {"error": f"expected owner/name, got '{repo}'"}
    out: dict[str, Any] = {"repo": slug}
    try:
        meta_resp = _guarded_get(f"https://api.github.com/repos/{slug}", headers=headers)
        if meta_resp.status_code == 404:
            # A wrong repo guess under a real owner (prod: 'AlgoNode/algonode') —
            # surface the owner's actual repos rather than a dead end.
            owner = slug.split("/")[0]
            listing = _github_owner_repos(owner, headers)
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
    except Exception as exc:
        return {"repo": slug, "error": str(exc)[:200]}
    try:
        rel = _guarded_get(
            f"https://api.github.com/repos/{slug}/releases",
            params={"per_page": n},
            headers=headers,
        ).json()
        out["releases"] = [
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
        out["releases"] = []
    try:
        commits = _guarded_get(
            f"https://api.github.com/repos/{slug}/commits",
            params={"per_page": n},
            headers=headers,
        ).json()
        out["recent_commits"] = [
            {
                "message": (c.get("commit", {}).get("message") or "").splitlines()[0][:140],
                "date": c.get("commit", {}).get("author", {}).get("date"),
                "author": (c.get("author") or {}).get("login"),
            }
            for c in commits
            if isinstance(c, dict)
        ][:n]
    except Exception:
        out["recent_commits"] = []
    try:
        # Top contributors by total commit count — who really built the project,
        # a stronger "anonymous team" signal than the last few commit authors.
        contributors = _guarded_get(
            f"https://api.github.com/repos/{slug}/contributors",
            params={"per_page": n},
            headers=headers,
        ).json()
        out["top_contributors"] = [
            {"login": c.get("login"), "contributions": c.get("contributions")}
            for c in contributors
            if isinstance(c, dict)
        ][:n]
    except Exception:
        out["top_contributors"] = []
    return out


def _tool_github_repository_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search ALL of GitHub for repos matching a keyword query — use this when
    github_activity's owner/repo guess 404s and you don't know the real owner
    (e.g. a project's site names it but not its GitHub org). Not scoped to one
    owner, unlike github_activity's owner-repo-listing fallback."""
    import os

    q = (query or "").strip()
    if not q:
        return {"error": "query must not be empty"}
    n = max(1, min(int(limit), 10))
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp = _guarded_get(
            "https://api.github.com/search/repositories",
            params={"q": q, "per_page": n, "sort": "stars"},
            headers=headers,
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


def _tool_search_token_listings(asset_id: Any) -> dict[str, Any]:
    """Whether an Algorand ASA is actually listed/tradeable on the two biggest
    Algorand DEXs (Tinyman, Pact) — real liquidity, price, and 24h/7d volume in
    USD, or confirmation it's NOT listed anywhere. Use this instead of assuming
    a token trades just because it exists; a real supply with zero listings
    is itself a notable fact worth reporting."""
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
        resp = _guarded_get(
            "https://api.pact.fi/api/pools", params={"asset_id": aid, "limit": 10}
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
    """Steer the writer to the dedicated tool (or a different strategy) for
    fetches that failed in a known way.

    Prod transcripts show the model repeatedly fetch_url-ing medium.com (403) and
    reddit.com (403) while the purpose-built tools sit unused — a hint inside the
    error result is followed far more reliably than a schema description. The
    status_code checks come first since they're precise (host checks below are
    best-effort text matching that a 401/403/429/5xx would otherwise fall through)."""
    from urllib.parse import urlsplit

    host = urlsplit(url).netloc.lower()
    if host.endswith("medium.com"):
        return (
            "medium.com blocks direct fetches — use medium_api_article_list "
            "with the @handle or publication URL instead"
        )
    if host.endswith("reddit.com"):
        return (
            "reddit.com blocks server fetches — use reddit_api_post_history "
            "for a user's posts/comments instead"
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
        status = exc.response.status_code
        out: dict[str, Any] = {"url": u, "error": str(exc)[:200], "status_code": status}
        hint = _fetch_failure_hint(u, out["error"], status_code=status)
        if hint:
            out["hint"] = hint
        return out
    except Exception as exc:
        out = {"url": u, "error": str(exc)[:200]}
        hint = _fetch_failure_hint(u, out["error"])
        if hint:
            out["hint"] = hint
        return out
    ctype = resp.headers.get("content-type", "")
    base = str(resp.url)
    # PDFs (whitepapers, audits, tokenomics docs) are common source links; read
    # their text instead of refusing them. pypdf is already a dependency.
    if "pdf" in ctype.lower() or base.lower().split("?")[0].endswith(".pdf"):
        try:
            from io import BytesIO

            from pypdf import PdfReader

            reader = PdfReader(BytesIO(resp.content))
            md = reader.metadata
            title = (getattr(md, "title", None) or "")[:200] if md else ""
            text = "\n".join((pg.extract_text() or "") for pg in reader.pages[:40]).strip()
        except Exception as exc:
            return {"url": base, "error": f"pdf parse failed: {str(exc)[:160]}"}
        return _slice_document_text(
            text,
            url=base,
            title=title,
            links=[],
            max_chars=cap,
            offset=offset,
        )
    if "html" not in ctype and "text" not in ctype:
        return {"url": u, "error": f"unsupported content-type: {ctype[:60]}"}
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

    # Thin / SPA-shaped response (React/Vue/Next shell, or a "please enable
    # JavaScript" fallback page) — same signal the web crawler uses to decide a
    # page needs its Playwright fallback. Retry rendered before reporting a
    # near-empty page as the page's real content.
    from app.modules.scraper.crawler_registry import is_web_spa_enabled
    from app.modules.scraper.crawlers.web_crawler import needs_spa_fallback

    if is_web_spa_enabled() and needs_spa_fallback(plain_text, raw_html=resp.text):
        try:
            from app.modules.scraper.core.browser_scraper import BrowserScraper

            rendered = BrowserScraper().scrape(base, "research-fetch_url")
            title = rendered.title or title
            text = rendered.text or text
            links = rendered.links or links
            base = rendered.url or base
        except Exception:
            pass  # keep the HTTP result — a failed render beats no result at all

    return _slice_document_text(
        text,
        url=base,
        title=title,
        links=links,
        max_chars=cap,
        offset=offset,
    )


def _tool_fetch_url(
    url: str,
    max_chars: int = 6000,
    offset: int = 0,
) -> dict[str, Any]:
    """Fetch a web page and return its cleaned main text (public tool result)."""
    raw = _fetch_url_internal(url, max_chars=max_chars, offset=offset)
    if raw.get("error"):
        return raw
    return _publicize_fetch_result(raw)


def _tool_get_defi_tvl(protocol: str = "") -> dict[str, Any]:
    """Current DeFi TVL from DeFiLlama (USD). No protocol → Algorand chain TVL;
    a protocol slug (e.g. 'tinyman', 'folks-finance', 'pact') → that protocol's TVL."""
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


_NODELY_DS_QUERY = "https://g.nodely.io/api/ds/query"
_NODELY_CH_UID = "fc25640e-50ee-4e04-aad6-2a5336c09eaf"
# In-process hourly cache: this is Nodely's free dashboard infra and the daily
# estimate barely moves, so read it at most once an hour per worker.
_node_stats_cache: dict[str, Any] = {}


def _tool_get_node_stats() -> dict[str, Any]:
    """Algorand mainnet NODE telemetry from Nodely's public dashboard: the latest
    daily estimate of full-time running nodes (Chao-1) plus the recent trend, for
    network decentralization / participation-scale context. This is a NODE count
    (off-chain telemetry, source g.nodely.io); for on-chain online STAKE use the
    get_consensus_stats tool."""
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


def _tool_discourse_forum(forum_url: str, limit: int = 10, query: str = "") -> dict[str, Any]:
    """Live activity from a Discourse community forum (most crypto project forums,
    incl. Folks Finance) via its public JSON API — site stats, top categories, and
    recent topics with reply/view counts. Pass ``query`` to search the forum's
    public /search.json instead of listing latest topics (prod writers kept
    wanting 'search the Algorand forum for <project>', which latest-topics can't
    answer). Read this instead of a static page snapshot to gauge what the
    community is actually discussing right now."""
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

    try:
        ab = (_guarded_get(f"{base}/about.json", headers=hdr).json() or {}).get("about", {})
        stats = ab.get("stats", {}) or {}
        out["title"] = ab.get("title")
        out["description"] = (ab.get("description") or "")[:300]
        out["stats"] = {
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
        }
    except Exception as exc:
        # A non-Discourse site (or one with the API disabled) — say so plainly.
        return {"forum": base, "error": f"not reachable as a Discourse forum: {str(exc)[:140]}"}

    q = (query or "").strip()
    if q:
        out["query"] = q
        try:
            data = (
                _guarded_get(f"{base}/search.json", headers=hdr, params={"q": q[:200]}).json()
                or {}
            )
            topics_by_id = {
                t.get("id"): t for t in data.get("topics", []) or [] if isinstance(t, dict)
            }
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
            out["results"] = results
            out["count"] = len(results)
        except Exception as exc:
            out["error"] = f"search failed: {str(exc)[:160]}"
        return out

    cat_names: dict[Any, str] = {}
    try:
        clist = (
            (_guarded_get(f"{base}/categories.json", headers=hdr).json() or {}).get(
                "category_list", {}
            )
            or {}
        ).get("categories", []) or []
        out["categories"] = [
            {
                "name": c.get("name"),
                "topics": c.get("topic_count"),
                "description": (c.get("description_text") or "")[:160],
            }
            for c in clist[:15]
            if isinstance(c, dict)
        ]
        cat_names = {c.get("id"): c.get("name") or "" for c in clist if isinstance(c, dict)}
    except Exception:
        out["categories"] = []

    try:
        topics = (
            (_guarded_get(f"{base}/latest.json", headers=hdr).json() or {}).get("topic_list", {})
            or {}
        ).get("topics", []) or []
        out["recent_topics"] = [
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
        ]
    except Exception as exc:
        out["latest_error"] = str(exc)[:160]
        out.setdefault("recent_topics", [])
    return out


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
    """Inspect a GitHub repo's files via the contents API. Empty path → root
    directory listing; a directory path → its entries; a file path → the file's
    decoded text. Use to READ smart-contract source and judge what a project
    actually shipped (github_activity only gives metadata). GITHUB_TOKEN optional."""
    import base64
    import os

    slug = _normalize_repo_slug(repo)
    if not slug:
        return {
            "error": f"expected owner/name, got '{repo}' — for an owner's repo "
            "list, call github_activity with just the owner"
        }
    p = (path or "").strip().lstrip("/")
    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {"ref": ref.strip()} if ref and ref.strip() else None
    try:
        resp = _guarded_get(
            f"https://api.github.com/repos/{slug}/contents/{p}", headers=headers, params=params
        )
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
                text = _guarded_get(data["download_url"], headers=headers, timeout=15.0).text
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


def _tool_medium_articles(source: str, limit: int = 15) -> dict[str, Any]:
    """List a Medium author's or publication's recent articles via its public RSS
    feed (no auth). Accepts an @handle, a medium.com URL, or a Medium-backed custom
    domain (e.g. algonaut.space). Returns title, link, published date and tags — use
    to quantify a blog's output and spot cross-posting patterns."""
    from urllib.parse import urlsplit

    from lxml import etree

    s = (source or "").strip()
    if not s:
        return {"error": "source required"}
    has_scheme = s.startswith(("http://", "https://"))
    if s.startswith("@") or (not has_scheme and "." not in s.split("/")[0]):
        # an @handle or a bare handle (no dot, no scheme)
        feed_url = f"https://medium.com/feed/{s if s.startswith('@') else '@' + s}"
    else:  # a URL or a bare domain/path — normalize to one URL code path
        if not has_scheme:
            s = "https://" + s
        parts = urlsplit(s)
        path = parts.path.strip("/")
        if "medium.com" in parts.netloc:
            if path.startswith("feed"):  # already a feed URL — use as-is
                feed_url = f"https://medium.com/{path}"
            elif path:  # /@handle or /publication
                feed_url = f"https://medium.com/feed/{path.split('/')[0]}"
            else:
                feed_url = "https://medium.com/feed"
        elif path.startswith("feed"):  # custom domain feed URL
            feed_url = f"{parts.scheme}://{parts.netloc}/{path}"
        else:  # custom domain backed by Medium
            feed_url = f"{parts.scheme}://{parts.netloc}/feed"
    n = max(1, min(int(limit), 30))
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


def _tool_reddit_history(user: str, kind: str = "submitted", limit: int = 15) -> dict[str, Any]:
    """Recent Reddit history for a user via Reddit's public JSON (no auth, rate-
    limited). kind: 'submitted' (posts, default) or 'comments'. Returns subreddit,
    title/body snippet, score, comments, date and permalink — use to verify a
    publication timeline or gauge community engagement."""
    from datetime import UTC, datetime

    u = (user or "").strip().lstrip("@")
    if u.lower().startswith("u/"):
        u = u[2:]
    if not u:
        return {"error": "user required"}
    k = "comments" if (kind or "").strip().lower().startswith("comment") else "submitted"
    n = max(1, min(int(limit), 25))
    try:
        resp = _guarded_get(
            f"https://www.reddit.com/user/{u}/{k}.json",
            headers={"Accept": "application/json"},
            params={"limit": n, "raw_json": 1},
            timeout=12.0,
        )
        if resp.status_code == 404:
            return {"user": u, "error": "user not found", "items": []}
        if resp.status_code == 429:
            return {"user": u, "error": "reddit rate-limited (429); try again later", "items": []}
        if resp.status_code == 403:
            return {
                "user": u,
                "error": "reddit blocked this request (403) — it rate-limits server IPs; "
                "treat as unavailable for this story",
                "items": [],
            }
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        return {"user": u, "error": str(exc)[:200], "items": []}

    def _iso(ts: Any) -> str:
        try:
            return datetime.fromtimestamp(float(ts), tz=UTC).strftime("%Y-%m-%d")
        except Exception:
            return ""

    items = []
    for c in ((data.get("data", {}) or {}).get("children", []) or [])[:n]:
        d = (c.get("data") or {}) if isinstance(c, dict) else {}
        perma = f"https://www.reddit.com{d.get('permalink', '')}" if d.get("permalink") else ""
        if k == "submitted":
            items.append(
                {
                    "subreddit": d.get("subreddit"),
                    "title": (d.get("title") or "")[:200],
                    "score": d.get("score"),
                    "num_comments": d.get("num_comments"),
                    "date": _iso(d.get("created_utc")),
                    "permalink": perma,
                }
            )
        else:
            items.append(
                {
                    "subreddit": d.get("subreddit"),
                    "body": (d.get("body") or "")[:300],
                    "score": d.get("score"),
                    "date": _iso(d.get("created_utc")),
                    "permalink": perma,
                }
            )
    return {"user": u, "kind": k, "count": len(items), "items": items}


_XGOV_RAW = "https://raw.githubusercontent.com/algorandfoundation/xGov/main"
_XGOV_API = "https://api.github.com/repos/algorandfoundation/xGov/contents"


def _xgov_frontmatter(md: str) -> dict[str, str]:
    """The `--- key: value ---` header every xGov proposal file starts with.
    Flat string values only — no YAML dependency needed."""
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


def _tool_xgov_proposal(proposal_id: int = 0, limit: int = 8) -> dict[str, Any]:
    """Status of Algorand xGov grant proposals from the canonical
    algorandfoundation/xGov repo: frontmatter (title, author, amount_requested,
    category, status Draft/Final/Approved/Rejected/Withdrawn, forum link) plus an
    abstract snippet. With proposal_id, one proposal in full; without, the
    newest proposals' summaries."""
    import os

    headers = {"Accept": "application/vnd.github+json"}
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def _fetch_one(pid: int, with_abstract: bool) -> dict[str, Any] | None:
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

    if proposal_id:
        entry = _fetch_one(int(proposal_id), with_abstract=True)
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
    try:
        resp = _guarded_get(f"{_XGOV_API}/Proposals", headers=headers)
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
    proposals = [p for pid in ids[:n] if (p := _fetch_one(pid, with_abstract=False))]
    return {
        "source": "github.com/algorandfoundation/xGov",
        "total_proposals": len(ids),
        "count": len(proposals),
        "proposals": proposals,
        "note": "pass proposal_id for full detail incl. abstract",
    }


_GITHUB_SCHEMA = {
    "type": "function",
    "function": {
        "name": "github_activity",
        "description": (
            "Recent GitHub activity for a repo (metadata, latest releases, recent "
            "commits, and top contributors) — use to report shipped updates, version "
            "launches, dev momentum, or who actually builds a project. Pass "
            "'owner/name' or a github.com URL; passing just an owner/org lists its "
            "repositories so you can pick one."
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

_REDDIT_SCHEMA = {
    "type": "function",
    "function": {
        "name": "reddit_api_post_history",
        "description": (
            "Recent Reddit history for a user via Reddit's public JSON (free, rate-"
            "limited): subreddit, title/body, score, comments, date and permalink. Use "
            "to verify a publication timeline or gauge engagement. kind: 'submitted' "
            "(posts) or 'comments'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "user": {"type": "string", "description": "reddit username (with or without u/)"},
                "kind": {"type": "string", "description": "'submitted' (default) or 'comments'"},
                "limit": {"type": "integer", "description": "1-25, default 15"},
            },
            "required": ["user"],
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
                "limit": {"type": "integer", "description": "1-10 proposals when listing, default 8"},
            },
            "required": [],
        },
    },
}


def research_tools() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Enabled external research tools as (schemas, handlers).

    search_web needs SEARXNG_URL and search_bluesky needs an app-password, so they
    register only when usable. github_activity, github_repository_search,
    github_repository_contents, search_token_listings, fetch_url, get_defi_tvl,
    discourse_forum, get_node_stats, medium_api_article_list,
    reddit_api_post_history and xgov_proposal_status hit free public APIs and
    are always available (GITHUB_TOKEN optional).
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
        _REDDIT_SCHEMA,
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
    return schemas, handlers
