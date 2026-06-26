from app.modules.newspaper.article_tags import derive_article_tags


def test_derive_tags_from_source_kind() -> None:
    tags = derive_article_tags(service_id="algorand-foundation", source_kind="web")
    assert "web" in tags


def test_derive_weekly_market_tags() -> None:
    tags = derive_article_tags(
        service_id="weekly-price-algorand",
        title="Weekly market snapshot",
    )
    assert "weekly" in tags
    assert "market" in tags
