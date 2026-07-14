from __future__ import annotations

import pytest

from app.modules.ai.mistral_client import MistralError
from app.modules.newspaper.article_composer import compose_scrape_article
from app.modules.newspaper.price_analysis import WeeklyPriceSnapshot
from app.modules.newspaper.publish_policy import PublishKind


def test_compose_scrape_uses_template_when_mistral_disabled(monkeypatch) -> None:
    monkeypatch.setenv("MISTRAL_ENABLED", "0")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    import app.core.config as config

    monkeypatch.setattr(config, "MISTRAL_ENABLED", False)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")

    result = compose_scrape_article(
        service_name="Svc",
        source_url="https://example.com",
        page_title="Page",
        page_text="body text",
        txid="TXID",
        round_num=1,
        diff=None,
        is_first_snapshot=True,
        publish_kind=PublishKind.SERVICE_DISCOVERY,
    )
    assert result.composer == "template"
    assert "Svc" in result.title
    assert result.publish_kind == "service_discovery"
    assert "profile" in result.summary.lower() or "tracking" in result.summary.lower()


def test_compose_scrape_uses_mistral_when_configured(monkeypatch) -> None:
    import app.core.config as config
    import app.modules.newspaper.article_composer as composer_module

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "key")

    class FakeFields:
        title = "AI Title"
        summary = "AI Summary"
        body = "# AI Body"

    def fake_mistral(**kwargs):
        return FakeFields()

    monkeypatch.setattr(composer_module, "compose_scrape_article_mistral", fake_mistral)

    result = compose_scrape_article(
        service_name="Svc",
        source_url="https://example.com",
        page_title="Page",
        page_text="body",
        txid="TX",
        round_num=2,
        diff="diff",
        is_first_snapshot=False,
        publish_kind=PublishKind.CONTENT_UPDATE,
    )
    assert result.composer == "mistral"
    assert result.title == "AI Title"


def test_compose_scrape_falls_back_on_mistral_error(monkeypatch) -> None:
    import app.core.config as config
    import app.modules.newspaper.article_composer as composer_module

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "key")
    monkeypatch.setattr(config, "MISTRAL_FALLBACK_TEMPLATE", True)

    def fail_mistral(**kwargs):
        raise MistralError("api down")

    monkeypatch.setattr(composer_module, "compose_scrape_article_mistral", fail_mistral)

    result = compose_scrape_article(
        service_name="Svc",
        source_url="https://example.com",
        page_title="Page",
        page_text="body",
        txid="TX",
        round_num=2,
        diff=None,
        is_first_snapshot=True,
        publish_kind=PublishKind.SERVICE_DISCOVERY,
    )
    assert result.composer == "template"


def test_compose_scrape_mistral_only_raises_when_not_configured(monkeypatch) -> None:
    import app.core.config as config

    monkeypatch.setattr(config, "MISTRAL_ENABLED", False)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")

    with pytest.raises(Exception, match="MISTRAL"):
        compose_scrape_article(
            service_name="Svc",
            source_url="https://example.com",
            page_title="Page",
            page_text="body",
            txid="TX",
            round_num=1,
            diff=None,
            is_first_snapshot=True,
            publish_kind=PublishKind.SERVICE_DISCOVERY,
            mistral_only=True,
        )


def test_compose_scrape_folds_transcript_into_page_text_for_non_recap(monkeypatch) -> None:
    """transcript_text was previously accepted but silently dropped for every
    topic except COMMUNITY_RECAP — the local YouTube pipeline needs it to
    reach the general writer path too."""
    import app.core.config as config
    import app.modules.newspaper.article_composer as composer_module

    monkeypatch.setattr(config, "MISTRAL_ENABLED", False)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")
    monkeypatch.setattr(config, "YOUTUBE_TRANSCRIPT_MAX_CHARS", 20_000)

    captured = {}

    def fake_service_discovery(**kwargs):
        captured.update(kwargs)
        return "Title", "Summary", "Body"

    monkeypatch.setattr(
        composer_module, "compose_service_discovery_article", fake_service_discovery
    )

    compose_scrape_article(
        service_name="Svc",
        source_url="https://example.com",
        page_title="Page",
        page_text="original page text",
        txid="TX",
        round_num=1,
        diff=None,
        is_first_snapshot=True,
        publish_kind=PublishKind.SERVICE_DISCOVERY,
        transcript_text="the video said something important",
    )
    assert "original page text" in captured["page_text"]
    assert "Video transcript:" in captured["page_text"]
    assert "the video said something important" in captured["page_text"]


def test_compose_scrape_recap_topic_does_not_double_fold_transcript(monkeypatch) -> None:
    """COMMUNITY_RECAP already gets the full transcript via
    compose_recap_from_transcript_mistral — the page_text fold-in must not
    also fire for that topic."""
    import app.core.config as config
    import app.modules.newspaper.article_composer as composer_module
    from app.modules.newspaper.publish_policy import PublishTopic

    monkeypatch.setattr(config, "MISTRAL_ENABLED", False)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")

    captured = {}

    def fake_recap_template(**kwargs):
        captured.update(kwargs)
        return "Title", "Summary", "Body"

    monkeypatch.setattr(
        composer_module, "compose_community_recap_article", fake_recap_template
    )

    compose_scrape_article(
        service_name="Svc",
        source_url="https://example.com",
        page_title="Page",
        page_text="original page text",
        txid="TX",
        round_num=1,
        diff=None,
        is_first_snapshot=True,
        publish_kind=PublishKind.CONTENT_UPDATE,
        publish_topic=PublishTopic.COMMUNITY_RECAP,
        transcript_text="the video said something important",
    )
    assert captured["page_text"] == "original page text"


def test_compose_weekly_price_template_when_disabled(monkeypatch) -> None:
    import app.core.config as config

    monkeypatch.setattr(config, "MISTRAL_ENABLED", False)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")

    from app.modules.newspaper.article_composer import compose_weekly_price

    snap = WeeklyPriceSnapshot(
        asset_id="algorand",
        asset_name="Algorand",
        currency="USD",
        price_usd=0.25,
        week_open_usd=0.20,
        week_high_usd=0.26,
        week_low_usd=0.19,
        week_change_pct=25.0,
        as_of=__import__("datetime").datetime(2026, 6, 2, tzinfo=__import__("datetime").UTC),
    )
    result = compose_weekly_price(snap)
    assert result.composer == "template"
