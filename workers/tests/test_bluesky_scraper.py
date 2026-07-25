"""Bluesky handle normalization and scrape-URL/post-URL detection."""

from app.modules.scraper.core.bluesky_scraper import (
    BlueskyPost,
    is_bluesky_scrape_url,
    normalize_handle,
)


def test_normalize_handle_forms() -> None:
    """Normalizes bare handles, @-prefixed handles and bsky.app profile/post URLs to a lowercase handle."""
    assert normalize_handle("algorandfoundation.bsky.social") == "algorandfoundation.bsky.social"
    assert normalize_handle("@Algo.bsky.social") == "algo.bsky.social"
    assert normalize_handle("https://bsky.app/profile/algorand.bsky.social") == (
        "algorand.bsky.social"
    )
    assert normalize_handle("https://bsky.app/profile/x.io/post/abc") == "x.io"
    assert normalize_handle("") == ""


def test_is_bluesky_scrape_url() -> None:
    """Recognizes bsky.app and bluesky: URLs as Bluesky scrape targets, rejecting others."""
    assert is_bluesky_scrape_url("https://bsky.app/profile/x.bsky.social")
    assert is_bluesky_scrape_url("bluesky:x.bsky.social")
    assert not is_bluesky_scrape_url("https://algorand.co")


def test_post_web_url() -> None:
    """Builds the public bsky.app web URL from a post's handle and rkey."""
    p = BlueskyPost(
        uri="at://did:plc:abc/app.bsky.feed.post/3kxyz",
        rkey="3kxyz",
        handle="algo.bsky.social",
        text="hi",
        created_at="",
        is_repost=False,
        is_reply=False,
    )
    assert p.web_url == "https://bsky.app/profile/algo.bsky.social/post/3kxyz"
