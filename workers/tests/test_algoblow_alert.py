from pathlib import Path

from app.modules.newspaper.publish_policy import (
    PublishKind,
    PublishTier,
    PublishTopic,
    classify_publish_topic,
    classify_publish_tier,
)
from app.modules.newspaper.scam_enrichment import (
    extract_algorand_addresses,
    extract_domains_and_urls,
    gather_scam_enrichment,
)

FIXTURE = Path(__file__).parent / "fixtures" / "algoblow_d13_alert.txt"
D13_TEXT = FIXTURE.read_text(encoding="utf-8")

VICTIM_ADDRS = [
    "A43BSFDDZGPEVB2XUUX652OOHNHRA3OZVP4FNM7MF5TDOCUFZWGLS7MR6A",
    "TJ7SMOGG52KCSEDWGP4NNCJMJFFLBFI5IKQS7GDUJN5UZJWCP7NTPPFMJ4",
    "XTC4LUY4T5U2KBGRMJD5STGLS2UQJM6QW2N7PCYFB76BYGSH7PV3WOQ22U",
    "YKTO4C2WAC2BSMJMYKM43YCGUYHU3XHAHAYG6UUSF3BLOF6VMGRXKYB7ZU",
]


def test_algoblow_classified_scam_breaking():
    topic = classify_publish_topic(
        page_text=D13_TEXT,
        diff=None,
        publish_kind=PublishKind.CONTENT_UPDATE,
        source_kind="push",
    )
    assert topic == PublishTopic.SCAM_ALERT
    assert classify_publish_tier(topic=topic, page_text=D13_TEXT) == PublishTier.BREAKING


def test_algoblow_extracts_domain_and_addresses():
    _urls, domains = extract_domains_and_urls(D13_TEXT)
    assert "algoblow.com" in domains
    addrs = extract_algorand_addresses(D13_TEXT)
    assert addrs == VICTIM_ADDRS


def test_algoblow_scam_enrichment_bundle():
    ctx = gather_scam_enrichment(D13_TEXT, source_url="push://community/d13-algoblow")
    assert "algoblow.com" in ctx.mentioned_domains
    assert len(ctx.mentioned_algo_addresses) == 4
    assert any("rekeyed" in n for n in ctx.fetch_notes)
