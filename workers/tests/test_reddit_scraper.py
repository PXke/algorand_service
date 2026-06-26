from __future__ import annotations

from app.modules.scraper.core.reddit_scraper import format_reddit_posts
from app.modules.scraper.core.reddit_urls import is_reddit_scrape_url, parse_reddit_target


def test_parse_reddit_target() -> None:
    assert parse_reddit_target("reddit://r/algorand") == parse_reddit_target("reddit:r/algorand")
    t = parse_reddit_target("reddit://r/algorand/new")
    assert t is not None
    assert t.subreddit == "algorand"
    assert t.sort == "new"
    assert parse_reddit_target("https://reddit.com/r/x") is None


def test_is_reddit_scrape_url() -> None:
    assert is_reddit_scrape_url("reddit://r/algorand")
    assert not is_reddit_scrape_url("discord://channel/123456789012345678")


def test_format_reddit_posts() -> None:
    posts = [
        {
            "data": {
                "title": "Hello",
                "selftext": "World",
                "author": "alice",
                "created_utc": 1000.0,
            }
        },
        {
            "data": {
                "title": "Earlier",
                "selftext": "",
                "author": "bob",
                "created_utc": 500.0,
            }
        },
    ]
    lines = format_reddit_posts(posts)
    assert len(lines) == 2
    assert "Earlier" in lines[0]
    assert "Hello" in lines[1]
