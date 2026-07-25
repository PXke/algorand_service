"""Building match keys and resolving edit-vs-create publish mode."""

from pathlib import Path
from unittest.mock import patch

from app.modules.newspaper.article_matching import build_match_keys, resolve_publish_mode

D13 = (Path(__file__).parent / "fixtures" / "algoblow_d13_alert.txt").read_text(encoding="utf-8")


def test_build_match_keys_algoblow() -> None:
    """Builds domain/address/keyword/source_url match keys for a scam_alert page."""
    # scam_alert topic: body-mentioned domains ARE a legitimate "this belongs
    # to that story" signal — a later report about algoblow.com should
    # attach to the same scam-alert article.
    keys = build_match_keys(
        service_id="algorand-scam-alerts",
        page_text=D13,
        source_url="https://x.com/d13_co/status/1",
        topic="scam_alert",
    )
    types = {t for t, _ in keys}
    assert "domain" in types
    assert "algo_address" in types
    assert "keyword" in types
    assert ("domain", "algoblow.com") in [(t, v) for t, v in keys]
    assert ("source_url", "https://x.com/d13_co/status/1") in [(t, v) for t, v in keys]


def test_build_match_keys_body_domains_suppressed_outside_scam_incident_topics() -> None:
    """Regression pin (2026-07-17): body-mentioned domains registered as match keys for ORDINARY content turned the most-cited article into a magnet for every unrelated future update mentioning any of them — six unrelated sources all got routed to "edit" the same live article, which then re-edited itself every ~2 minutes forever (missing terminal-outcome bug, fixed separately) — 165 edits / 330 versions in under 4 hours. Without topic=scam_alert/network_incident, body domains must NOT become match keys; the source's own domain/service_id still can (unaffected)."""
    keys = build_match_keys(
        service_id="algorand-foundation-blog",
        page_text=D13,  # same body-mentioned domain (algoblow.com), different topic
        source_url="https://algorand.co/blog/some-post",
    )
    domains = {v for t, v in keys if t == "domain"}
    assert "algoblow.com" not in domains
    # Cashtags share the gate (audit 2026-07-17): $ALGO/$USDC appear in
    # ordinary market coverage constantly, so an ungated "keyword" key routes
    # unrelated future updates into editing whatever article mentioned the
    # ticker last — same magnet mechanic as body domains, same fix.
    assert not any(t == "keyword" for t, _ in keys)
    # The source's own registrable domain (from source_url, not the body)
    # is a different code path — a normal, narrow signal, untouched by this.
    assert ("source_url", "https://algorand.co/blog/some-post") in [(t, v) for t, v in keys]
    # Addresses stay ungated in ANY topic: 58-char checksummed strings are
    # high-precision "same story" signals, unlike domains/tickers.
    assert any(t == "algo_address" for t, _ in keys)


def test_build_match_keys_domain_source_uses_registry_domain_not_page_url() -> None:
    """Uses the registry's match_value/service_id as keys, not a source_url key, when domain-matched."""
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


def test_build_match_keys_domain_derives_from_url_when_no_match_value() -> None:
    """Derives the domain key from source_url when match_kind is domain but match_value is absent."""
    keys = build_match_keys(
        service_id="algorand-foundation-blog",
        page_text="Blog listing page.",
        source_url="https://www.algorand.co/blog",
        match_kind="domain",
    )
    assert ("domain", "algorand.co") in keys


def test_resolve_publish_mode_edit_when_domain_matches() -> None:
    """Resolves to edit mode against the matched article when a domain-matched follow-up is found."""
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


def test_resolve_publish_mode_create_when_no_match() -> None:
    """Resolves to create mode with no linked article when no follow-up match is found."""
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
