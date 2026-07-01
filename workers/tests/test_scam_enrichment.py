from app.modules.newspaper.scam_enrichment import (
    extract_domains_and_urls,
    gather_scam_enrichment,
)


def test_extract_algoblow_domain():
    text = "DO NOT interact with algoblow.com! Malicious rekey requests."
    _urls, domains = extract_domains_and_urls(text)
    assert "algoblow.com" in domains


def test_gather_disabled_notes_domains():
    ctx = gather_scam_enrichment("Warning: evil.example.com wallet drainer")
    assert "evil.example.com" in ctx.mentioned_domains
    assert any("enrichment_disabled" in n for n in ctx.fetch_notes)
