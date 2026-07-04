"""Bluesky (AT Protocol) source lane — public reads only, no auth, no key.

The public AppView (`public.api.bsky.app`) serves an account's posts through
`app.bsky.feed.getAuthorFeed`, so watching a handful of Algorand accounts costs
one GET per account per poll and no credentials. We ingest original posts (not
reposts/replies) as publish signals, one per post, deduped by the snapshot
store on a per-post service_id — the same pattern the YouTube lane uses.
"""
from __future__ import annotations

from dataclasses import dataclass

_PUBLIC_APPVIEW = "https://public.api.bsky.app"


@dataclass(frozen=True)
class BlueskyPost:
    uri: str  # at:// URI (stable id)
    rkey: str  # record key — the id segment, used for the post's web URL
    handle: str
    text: str
    created_at: str  # ISO8601 from the record, "" when absent
    is_repost: bool
    is_reply: bool

    @property
    def web_url(self) -> str:
        return f"https://bsky.app/profile/{self.handle}/post/{self.rkey}"


def normalize_handle(actor: str) -> str:
    """Accept a bare handle, an @handle, or a bsky.app profile URL → bare handle."""
    a = (actor or "").strip()
    if not a:
        return ""
    if a.startswith("http"):
        # https://bsky.app/profile/<handle>[/...]
        parts = [p for p in a.split("/") if p]
        if "profile" in parts:
            i = parts.index("profile")
            if i + 1 < len(parts):
                return parts[i + 1].lstrip("@").lower()
        return ""
    return a.lstrip("@").lower()


def is_bluesky_scrape_url(url: str) -> bool:
    u = (url or "").lower()
    return "bsky.app/profile/" in u or u.startswith("bluesky:")


def fetch_author_posts(actor: str, *, limit: int = 20) -> tuple[str, list[BlueskyPost]]:
    """(display_name, posts) for an account's recent authored posts. Raises on
    transport/HTTP error so the caller can record + continue (like YouTube)."""
    from app.core.net_guard import guarded_get

    handle = normalize_handle(actor)
    if not handle:
        return "", []
    resp = guarded_get(
        f"{_PUBLIC_APPVIEW}/xrpc/app.bsky.feed.getAuthorFeed",
        params={"actor": handle, "limit": str(min(max(limit, 1), 100)), "filter": "posts_no_replies"},
        timeout=12.0,
        headers={"User-Agent": "algorand-platform-newspaper/1.0"},
    )
    data = resp.json()
    display_name = handle
    posts: list[BlueskyPost] = []
    for item in data.get("feed", []) or []:
        post = item.get("post") or {}
        author = post.get("author") or {}
        record = post.get("record") or {}
        uri = str(post.get("uri") or "")
        if not uri or record.get("$type") != "app.bsky.feed.post":
            continue
        rkey = uri.rsplit("/", 1)[-1] if "/" in uri else uri
        handle_val = str(author.get("handle") or handle).lower()
        if author.get("displayName"):
            display_name = str(author["displayName"])
        posts.append(
            BlueskyPost(
                uri=uri,
                rkey=rkey,
                handle=handle_val,
                text=str(record.get("text") or "").strip(),
                created_at=str(record.get("createdAt") or ""),
                is_repost=item.get("reason") is not None,
                is_reply=record.get("reply") is not None,
            )
        )
    return display_name, posts
