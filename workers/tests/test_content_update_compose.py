from app.modules.newspaper.content_update_compose import compose_content_update_article
from app.modules.newspaper.publish_policy import PublishTopic


def test_content_update_mentions_pricing() -> None:
    title, summary, body = compose_content_update_article(
        service_name="Acme DEX",
        source_url="https://acme.example",
        page_title="Fees",
        page_text="Trading fees updated for retail users.",
        diff="+++ page\n+ subscription now $9 per month\n+ removed free tier",
        topic=PublishTopic.PRICING_CHANGE,
    )
    assert "pricing" in title.lower() or "fee" in title.lower()
    assert "Acme DEX" in summary
    assert "subscription" in body
