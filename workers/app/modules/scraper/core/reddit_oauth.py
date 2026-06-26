from __future__ import annotations

import time

import httpx

from app.core.config import (
    REDDIT_CLIENT_ID,
    REDDIT_CLIENT_SECRET,
    REDDIT_OAUTH_ENABLED,
    REDDIT_USER_AGENT,
)

_token_cache: tuple[str, float] | None = None


class RedditOAuthError(Exception):
    pass


def get_reddit_bearer_token() -> str | None:
    """Client-credentials token for reddit.com OAuth (higher rate limits than anonymous JSON)."""
    global _token_cache
    if not REDDIT_OAUTH_ENABLED or not REDDIT_CLIENT_ID or not REDDIT_CLIENT_SECRET:
        return None

    if _token_cache is not None:
        token, expires_at = _token_cache
        if time.time() < expires_at - 60:
            return token

    auth = (REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET)
    headers = {"User-Agent": REDDIT_USER_AGENT}
    data = {"grant_type": "client_credentials"}
    with httpx.Client(timeout=20.0) as client:
        resp = client.post(
            "https://www.reddit.com/api/v1/access_token",
            auth=auth,
            headers=headers,
            data=data,
        )
        if resp.status_code >= 400:
            msg = f"reddit oauth failed: {resp.status_code}"
            raise RedditOAuthError(msg)
        body = resp.json()
    token = str(body.get("access_token", ""))
    if not token:
        raise RedditOAuthError("reddit oauth missing access_token")
    ttl = int(body.get("expires_in", 3600))
    _token_cache = (token, time.time() + ttl)
    return token
