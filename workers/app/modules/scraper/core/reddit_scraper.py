from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import httpx

from app.core.config import REDDIT_OAUTH_ENABLED, REDDIT_POST_LIMIT, REDDIT_USER_AGENT
from app.modules.scraper.core.base import BaseScraper, ScrapeResult
from app.modules.scraper.core.http_retry import request_with_retry
from app.modules.scraper.core.reddit_oauth import RedditOAuthError, get_reddit_bearer_token
from app.modules.scraper.core.reddit_urls import parse_reddit_target

REDDIT_BASE = "https://www.reddit.com"


class RedditScraperError(Exception):
    pass


class RedditScraper(BaseScraper):
    """Fetch recent subreddit posts via Reddit's public JSON listings (RFC 9110)."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        post_limit: int | None = None,
        timeout: float = 20.0,
    ) -> None:
        self._user_agent = (user_agent if user_agent is not None else REDDIT_USER_AGENT).strip()
        self._post_limit = post_limit if post_limit is not None else REDDIT_POST_LIMIT
        self._timeout = timeout

    def scrape(self, url: str, source_id: str) -> ScrapeResult:
        if not self._user_agent:
            msg = "REDDIT_USER_AGENT is not set (required by Reddit API policy)"
            raise RedditScraperError(msg)

        target = parse_reddit_target(url)
        if not target:
            msg = f"invalid reddit scrape_url: {url!r}"
            raise RedditScraperError(msg)

        headers = {"User-Agent": self._user_agent}
        listing_url = f"{REDDIT_BASE}/r/{target.subreddit}/{target.sort}.json"
        params = {"limit": min(max(self._post_limit, 1), 100), "raw_json": "1"}

        if REDDIT_OAUTH_ENABLED:
            try:
                token = get_reddit_bearer_token()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                    listing_url = f"https://oauth.reddit.com/r/{target.subreddit}/{target.sort}"
            except RedditOAuthError:
                pass

        with httpx.Client(timeout=self._timeout, headers=headers, follow_redirects=True) as client:
            response = request_with_retry(client, "GET", listing_url, params=params)
            if response.status_code in (401, 403, 429):
                msg = f"reddit access denied or rate limited: {response.status_code}"
                raise RedditScraperError(msg)
            response.raise_for_status()
            payload = response.json()

        posts = (payload.get("data") or {}).get("children") or []
        lines = format_reddit_posts(posts)
        text = "\n".join(lines)
        title = f"Reddit r/{target.subreddit} ({target.sort})"
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return ScrapeResult(
            source_id=source_id,
            url=url,
            title=title,
            text=text,
            content_hash=content_hash,
        )


def format_reddit_posts(posts: list[dict]) -> list[str]:
    rows: list[tuple[float, str]] = []
    for child in posts:
        data = child.get("data") or {}
        post_title = (data.get("title") or "").strip()
        body = (data.get("selftext") or "").strip()
        if data.get("removed_by_category"):
            continue
        if not post_title and not body:
            continue
        author = data.get("author") or "unknown"
        created = float(data.get("created_utc") or 0)
        stamp = (
            datetime.fromtimestamp(created, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
            if created
            else "unknown-time"
        )
        snippet = post_title
        if body and body not in ("[removed]", "[deleted]"):
            excerpt = body.replace("\n", " ").strip()[:400]
            snippet = f"{post_title} — {excerpt}" if post_title else excerpt
        rows.append((created, f"[{stamp}] u/{author}: {snippet}"))

    rows.sort(key=lambda row: row[0])
    return [line for _, line in rows]
