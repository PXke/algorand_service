from app.modules.newspaper.service_discovery_compose import compose_service_discovery_article


def test_discovery_framing() -> None:
    title, _summary, body = compose_service_discovery_article(
        service_name="AlgoSwap",
        source_url="https://example.com",
        page_title="Introducing AlgoSwap",
        page_text="AlgoSwap is a DEX on Algorand. Pricing starts at $1 per swap.",
    )
    assert "AlgoSwap" in title
    assert "first snapshot" not in body.lower()
    assert "Algorand Platform" in body
    assert "pricing" in body.lower() or "Pricing" in body
