"""Frontier pre-enqueue relevance gate: off-topic external links (no crypto/
Algorand signal in URL or anchor) are dropped before any preview/crawl."""

from app.modules.scraper.core.link_extractor import _link_plausibly_relevant


def test_off_topic_links_rejected():
    assert _link_plausibly_relevant("https://www.realtor.com/", "Homes for sale") is False
    assert _link_plausibly_relevant("https://cdn.jwplayer.com/previews/abc", "video") is False
    assert _link_plausibly_relevant("https://viewreward.app", "Click here") is False


def test_relevant_links_accepted():
    # Signal in the URL.
    assert _link_plausibly_relevant("https://tinyman.org/pools", "Pools") is True
    assert _link_plausibly_relevant("https://example.com/algorand-defi", "Read") is True
    # Signal only in the anchor text.
    assert _link_plausibly_relevant("https://medium.com/p/xyz", "Algorand governance") is True
    assert _link_plausibly_relevant("https://example.org/", "New DeFi wallet launch") is True
