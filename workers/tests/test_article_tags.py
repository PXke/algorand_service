"""Deriving article tags from source kind and market-digest content."""

from app.modules.newspaper.article_tags import derive_article_tags, order_reader_tags


def test_derive_tags_from_source_kind() -> None:
    """Includes the source kind (e.g. "web") among the derived tags."""
    tags = derive_article_tags(service_id="algorand-foundation", source_kind="web")
    assert "web" in tags


def test_web_is_not_leading_when_topical_tags_exist() -> None:
    """Topical tags lead; ubiquitous provenance like web trails."""
    tags = derive_article_tags(
        service_id="algorand-foundation",
        source_kind="web",
        publish_kind="content_update",
        publish_topic="sdk-release",
    )
    assert tags[0] == "sdk"
    assert tags.index("sdk") < tags.index("web")
    assert tags.index("sdk") < tags.index("update")


def test_order_reader_tags_demotes_meta() -> None:
    assert order_reader_tags(["web", "defi", "update", "governance"]) == [
        "defi",
        "governance",
        "web",
        "update",
    ]


def test_derive_weekly_market_tags() -> None:
    """Tags a weekly-price-service digest article with both "weekly" and "market"."""
    tags = derive_article_tags(
        service_id="weekly-price-algorand",
        title="Weekly market snapshot",
    )
    assert "weekly" in tags
    assert "market" in tags
    assert tags[0] == "market"
