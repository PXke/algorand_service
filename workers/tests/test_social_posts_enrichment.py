from unittest.mock import patch

from app.modules.newspaper.writer_enrichment.collectors.social_posts import (
    enrich_linked_posts,
    extract_post_urls,
)


def test_extract_tweet_url():
    text = "See https://x.com/d13_co/status/2060386210732761317 for details"
    assert extract_post_urls(text) == ["https://x.com/d13_co/status/2060386210732761317"]


def test_enrich_linked_posts_mocked():
    fake = {
        "author_name": "D13",
        "author_url": "https://x.com/d13_co",
        "html": "<p>Scam warning algoblow.com</p>",
    }
    with patch(
        "app.modules.newspaper.writer_enrichment.collectors.social_posts.httpx.Client"
    ) as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value.raise_for_status = lambda: None
        client.get.return_value.json.return_value = fake
        out = enrich_linked_posts(
            "https://x.com/d13_co/status/2060386210732761317",
            enabled=True,
        )
    assert out["count"] == 1
    assert "Scam warning" in out["linked_posts"][0]["text"]
