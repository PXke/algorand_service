from pathlib import Path
from unittest.mock import patch

from app.modules.newspaper.article_matching import build_match_keys, resolve_publish_mode

D13 = (Path(__file__).parent / "fixtures" / "algoblow_d13_alert.txt").read_text(encoding="utf-8")


def test_build_match_keys_algoblow():
    keys = build_match_keys(
        service_id="algorand-scam-alerts",
        page_text=D13,
        source_url="https://x.com/d13_co/status/1",
    )
    types = {t for t, _ in keys}
    assert "domain" in types
    assert "algo_address" in types
    assert "keyword" in types
    assert ("domain", "algoblow.com") in [(t, v) for t, v in keys]
    assert ("source_url", "https://x.com/d13_co/status/1") in [(t, v) for t, v in keys]


def test_build_match_keys_domain_source_uses_registry_domain_not_page_url():
    keys = build_match_keys(
        service_id="algorand-foundation-blog",
        page_text="New post about governance.",
        source_url="https://algorand.co/blog/governance-update",
        match_kind="domain",
        match_value="algorand.co",
    )
    pairs = set(keys)
    assert ("domain", "algorand.co") in pairs
    assert ("service_id", "algorand-foundation-blog") in pairs
    assert not any(key_type == "source_url" for key_type, _ in keys)


def test_build_match_keys_domain_derives_from_url_when_no_match_value():
    keys = build_match_keys(
        service_id="algorand-foundation-blog",
        page_text="Blog listing page.",
        source_url="https://www.algorand.co/blog",
        match_kind="domain",
    )
    assert ("domain", "algorand.co") in keys


def test_resolve_publish_mode_edit_when_domain_matches():
    with patch(
        "app.modules.newspaper.article_matching.find_article_for_followup",
        return_value="article-123",
    ) as mock_find:
        info = resolve_publish_mode(
            service_id="algorand-foundation-blog",
            page_text="Follow-up crawl from another blog page.",
            source_url="https://algorand.co/blog/new-post",
            match_kind="domain",
            match_value="algorand.co",
        )
    mock_find.assert_called_once()
    keys = mock_find.call_args[0][0]
    assert ("domain", "algorand.co") in keys
    assert not any(key_type == "source_url" for key_type, _ in keys)
    assert info["publish_mode"] == "edit"
    assert info["linked_article_id"] == "article-123"


def test_resolve_publish_mode_create_when_no_match():
    with patch(
        "app.modules.newspaper.article_matching.find_article_for_followup",
        return_value=None,
    ):
        info = resolve_publish_mode(
            service_id="algorand-scam-alerts",
            page_text=D13,
            topic="scam_alert",
        )
    assert info["publish_mode"] == "create"
    assert info["linked_article_id"] is None
