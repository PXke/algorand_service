from __future__ import annotations

import re
from dataclasses import dataclass

_SUBREDDIT = re.compile(r"^[A-Za-z0-9_]{2,21}$")
_VALID_SORTS = frozenset({"hot", "new", "top", "rising", "controversial"})


@dataclass(frozen=True)
class RedditTarget:
    subreddit: str
    sort: str


def parse_reddit_target(scrape_url: str) -> RedditTarget | None:
    raw = scrape_url.strip()
    path = ""
    if raw.startswith("reddit://"):
        path = raw[len("reddit://") :].strip("/")
    elif raw.startswith("reddit:"):
        path = raw[len("reddit:") :].strip("/")
    else:
        return None

    if path.startswith("r/"):
        path = path[2:]
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None

    subreddit = parts[0]
    if not _SUBREDDIT.match(subreddit):
        return None

    sort = "hot"
    if len(parts) > 1 and parts[1].lower() in _VALID_SORTS:
        sort = parts[1].lower()
    return RedditTarget(subreddit=subreddit, sort=sort)


def is_reddit_scrape_url(scrape_url: str) -> bool:
    return parse_reddit_target(scrape_url) is not None
