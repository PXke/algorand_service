"""Compose-article routing to the writer LLM and its no-fallback failure behavior."""

from __future__ import annotations

from typing import Any, Never

import pytest

from app.modules.ai.llm_provider import LLMError
from app.modules.ai.llm_purpose_router import PeakHoursBlockedError
from app.modules.newspaper.article_composer import compose_scrape_article, compose_weekly_digest
from app.modules.newspaper.publish_policy import PublishKind, PublishTopic


def test_compose_scrape_raises_when_mistral_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """No template fallback exists (owner decision 2026-07-14) — every compose requires Mistral now, whether or not the caller ever set the now-vestigial mistral_only flag."""
    import app.core.config as config

    monkeypatch.setattr(config, "MISTRAL_ENABLED", False)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "")

    with pytest.raises(LLMError, match="MISTRAL"):
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
        )


def test_compose_scrape_uses_mistral_when_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Routes composition through Mistral and returns its title/summary/body when configured."""
    import app.core.config as config
    import app.modules.newspaper.article_composer as composer_module

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "key")

    class FakeFields:
        title = "AI Title"
        summary = "AI Summary"
        body = "# AI Body"

    def fake_mistral(**_kwargs: object) -> Any:  # noqa: ANN401 -- test double / fake response
        return FakeFields()

    monkeypatch.setattr(composer_module, "_llm_compose_scrape_article", fake_mistral)

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


def test_compose_scrape_raises_on_mistral_error_no_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No template fallback exists — a Mistral failure must propagate as LLMError so callers can cleanly skip (see publish_from_queued_row/recompose_review/recompose_published, which already catch LLMError and return a {"status": ...} dict before any DB write happens)."""
    import app.core.config as config
    import app.modules.newspaper.article_composer as composer_module

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "key")

    def fail_mistral(**_kwargs: object) -> Never:
        raise LLMError("api down")

    monkeypatch.setattr(composer_module, "_llm_compose_scrape_article", fail_mistral)

    with pytest.raises(LLMError, match="api down"):
        compose_scrape_article(
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


def test_compose_scrape_folds_transcript_into_page_text_for_non_recap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """transcript_text was previously accepted but silently dropped for every topic except COMMUNITY_RECAP — the local YouTube pipeline needs it to reach the general writer path too."""
    import app.core.config as config
    import app.modules.newspaper.article_composer as composer_module

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "key")
    monkeypatch.setattr(config, "YOUTUBE_TRANSCRIPT_MAX_CHARS", 20_000)

    captured = {}

    class FakeFields:
        title = "Title"
        summary = "Summary"
        body = "Body"

    def fake_mistral(**kwargs: object) -> Any:  # noqa: ANN401 -- test double / fake response
        captured.update(kwargs)
        return FakeFields()

    monkeypatch.setattr(composer_module, "_llm_compose_scrape_article", fake_mistral)

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


def test_compose_scrape_recap_topic_does_not_double_fold_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """COMMUNITY_RECAP routes to compose_recap_from_transcript (which takes transcript_text as its own dedicated param, not page_text) — it must never fall through to the generic compose_scrape_article path, which is the only place the page_text transcript-fold applies."""
    import app.core.config as config
    import app.modules.newspaper.article_composer as composer_module

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "key")

    captured = {}

    class FakeFields:
        title = "Title"
        summary = "Summary"
        body = "Body"

    def fake_recap_mistral(**kwargs: object) -> Any:  # noqa: ANN401 -- test double / fake response
        captured.update(kwargs)
        return FakeFields()

    def fail_generic_mistral(**_kwargs: object) -> Never:
        raise AssertionError("must not fall through to the generic scrape compose")

    monkeypatch.setattr(
        composer_module, "compose_recap_from_transcript", fake_recap_mistral
    )
    monkeypatch.setattr(composer_module, "_llm_compose_scrape_article", fail_generic_mistral)

    result = compose_scrape_article(
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
    assert result.composer == "mistral_transcript"
    assert captured["transcript_text"] == "the video said something important"


def test_peak_hours_blocked_error_is_a_mistral_error() -> None:
    """Every existing `except LLMError` call site (publish_tasks.py) must catch this with zero changes there."""
    assert issubclass(PeakHoursBlockedError, LLMError)


def test_compose_scrape_raises_peak_hours_blocked_during_peak_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Owner decision 2026-08-15: confine ALL compose (no exceptions) to off-peak hours -- checked before the real Mistral call is ever made, so no tokens are spent during peak."""
    import app.core.config as config
    import app.modules.newspaper.article_composer as composer_module

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "key")
    monkeypatch.setattr(composer_module, "is_off_peak_now", lambda: False)
    monkeypatch.setattr(composer_module, "next_off_peak_at", lambda: None)

    def fail_if_called(**_kwargs: object) -> Never:
        raise AssertionError("must not call Mistral while peak-hours-blocked")

    monkeypatch.setattr(composer_module, "_llm_compose_scrape_article", fail_if_called)

    with pytest.raises(PeakHoursBlockedError):
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
        )


def test_compose_scrape_allowed_off_peak(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off-peak (the common case) composes exactly as before -- the guard must not interfere when it's not blocking."""
    import app.core.config as config
    import app.modules.newspaper.article_composer as composer_module

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "key")
    monkeypatch.setattr(composer_module, "is_off_peak_now", lambda: True)

    class FakeFields:
        title = "Title"
        summary = "Summary"
        body = "Body"

    monkeypatch.setattr(
        composer_module, "_llm_compose_scrape_article", lambda **_kw: FakeFields()
    )

    result = compose_scrape_article(
        service_name="Svc",
        source_url="https://example.com",
        page_title="Page",
        page_text="body",
        txid="TX",
        round_num=1,
        diff=None,
        is_first_snapshot=True,
        publish_kind=PublishKind.SERVICE_DISCOVERY,
    )
    assert result.title == "Title"


def test_compose_weekly_digest_raises_peak_hours_blocked_during_peak_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The weekly-digest path is one of the 9 real compose-triggering task paths -- must be gated identically to compose_scrape_article, no exceptions."""
    import app.core.config as config
    import app.modules.newspaper.article_composer as composer_module

    monkeypatch.setattr(config, "MISTRAL_ENABLED", True)
    monkeypatch.setattr(config, "MISTRAL_API_KEY", "key")
    monkeypatch.setattr(composer_module, "is_off_peak_now", lambda: False)
    monkeypatch.setattr(composer_module, "next_off_peak_at", lambda: None)

    def fail_if_called(_ctx: object) -> Never:
        raise AssertionError("must not call Mistral while peak-hours-blocked")

    monkeypatch.setattr(composer_module, "compose_weekly_digest_article", fail_if_called)

    with pytest.raises(PeakHoursBlockedError):
        compose_weekly_digest(object())  # guard raises before context is ever touched
