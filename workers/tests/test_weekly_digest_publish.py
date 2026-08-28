"""Publishing the weekly digest, including its no-fallback Mistral failure path."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Never

import pytest

from app.modules.ai.llm_provider import LLMError
from app.modules.newspaper import weekly_digest_publish
from app.modules.newspaper.price_analysis import WeeklyPriceSnapshot
from app.modules.newspaper.weekly_digest import WeeklyDigestContext


def _fake_compose(_ctx: WeeklyDigestContext) -> Any:  # noqa: ANN401 -- test double / fake response
    return type(
        "R",
        (),
        {"title": "T", "summary": "S", "body": "B", "composer": "template"},
    )()


def test_run_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the weekly digest publish entirely when PRICE_ANALYSIS_ENABLED is off."""
    import app.core.config as config

    monkeypatch.setattr(config, "PRICE_ANALYSIS_ENABLED", False)
    result = weekly_digest_publish.run_weekly_digest_publish()
    assert result["status"] == "skipped"


def test_run_skips_when_already_published(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips the run with an "already_published" reason when this week's digest article already exists."""
    import app.core.config as config

    monkeypatch.setattr(config, "PRICE_ANALYSIS_ENABLED", True)
    ctx = WeeklyDigestContext(
        week_key="2026-W23",
        week_label="2026-06-02",
        price=WeeklyPriceSnapshot(
            asset_id="algorand",
            asset_name="Algorand",
            currency="USD",
            price_usd=1.0,
            week_open_usd=1.0,
            week_high_usd=1.0,
            week_low_usd=1.0,
            week_change_pct=0.0,
            as_of=datetime(2026, 6, 2, tzinfo=UTC),
        ),
        articles=(),
    )

    monkeypatch.setattr(weekly_digest_publish, "build_weekly_digest", lambda **_kw: ctx)
    monkeypatch.setattr(weekly_digest_publish, "compose_weekly_digest", _fake_compose)

    def fake_insert(**kwargs: object) -> tuple[str, bool]:
        return str(kwargs["article_id"]), False

    monkeypatch.setattr(weekly_digest_publish, "insert_article_if_absent", fake_insert)
    result = weekly_digest_publish.run_weekly_digest_publish()
    assert result["status"] == "skipped"
    assert result["reason"] == "already_published"


def test_run_publishes_new_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Publishes and indexes a new weekly digest article when none exists yet for the week."""
    import app.core.config as config

    monkeypatch.setattr(config, "PRICE_ANALYSIS_ENABLED", True)
    ctx = WeeklyDigestContext(
        week_key="2026-W23",
        week_label="2026-06-02",
        price=WeeklyPriceSnapshot(
            asset_id="algorand",
            asset_name="Algorand",
            currency="USD",
            price_usd=1.0,
            week_open_usd=1.0,
            week_high_usd=1.0,
            week_low_usd=1.0,
            week_change_pct=0.0,
            as_of=datetime(2026, 6, 2, tzinfo=UTC),
        ),
        articles=(),
    )

    monkeypatch.setattr(weekly_digest_publish, "build_weekly_digest", lambda **_kw: ctx)
    monkeypatch.setattr(weekly_digest_publish, "compose_weekly_digest", _fake_compose)
    indexed: list[str] = []
    monkeypatch.setattr(
        weekly_digest_publish.index_article,
        "delay",
        lambda **kw: indexed.append(kw["article_id"]),
    )
    translated: list[str] = []
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.publish_tasks.enqueue_article_translations",
        lambda article_id: translated.append(article_id),
    )

    captured_insert_kwargs: dict = {}

    def fake_insert(**kwargs: object) -> tuple[str, bool]:
        captured_insert_kwargs.update(kwargs)
        return str(kwargs["article_id"]), True

    monkeypatch.setattr(weekly_digest_publish, "insert_article_if_absent", fake_insert)
    result = weekly_digest_publish.run_weekly_digest_publish()
    assert result["status"] == "published"
    assert result["feed_articles"] == "0"
    assert indexed
    # Unlike the normal publish path, the digest never called this at all
    # (found 2026-08-03) -- weekly digests silently never got translated.
    assert translated == [result["article_id"]]
    # No source page to pull a hero image from -- falls back to the site's
    # own icon instead of publishing with no image at all.
    assert captured_insert_kwargs["image_url"] == f"{config.PUBLIC_SITE_URL}/icons/icon-512.png"


def test_run_skipped_already_published_does_not_retranslate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A week that already has its digest article must not re-enqueue translations on every subsequent beat tick."""
    import app.core.config as config

    monkeypatch.setattr(config, "PRICE_ANALYSIS_ENABLED", True)
    ctx = WeeklyDigestContext(
        week_key="2026-W23",
        week_label="2026-06-02",
        price=WeeklyPriceSnapshot(
            asset_id="algorand",
            asset_name="Algorand",
            currency="USD",
            price_usd=1.0,
            week_open_usd=1.0,
            week_high_usd=1.0,
            week_low_usd=1.0,
            week_change_pct=0.0,
            as_of=datetime(2026, 6, 2, tzinfo=UTC),
        ),
        articles=(),
    )

    monkeypatch.setattr(weekly_digest_publish, "build_weekly_digest", lambda **_kw: ctx)
    monkeypatch.setattr(weekly_digest_publish, "compose_weekly_digest", _fake_compose)
    translated: list[str] = []
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.publish_tasks.enqueue_article_translations",
        lambda article_id: translated.append(article_id),
    )

    def fake_insert(**kwargs: object) -> tuple[str, bool]:
        return str(kwargs["article_id"]), False

    monkeypatch.setattr(weekly_digest_publish, "insert_article_if_absent", fake_insert)
    result = weekly_digest_publish.run_weekly_digest_publish()
    assert result["status"] == "skipped"
    assert not translated


def test_run_skips_cleanly_when_mistral_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """No template fallback exists (owner decision 2026-07-14) — compose_weekly_digest now raises MistralError instead of silently falling back, and this was the one caller in the whole compose layer with no existing exception handling for that. Must skip cleanly with a status dict, not let the Celery task fail with an uncaught exception."""
    import app.core.config as config

    monkeypatch.setattr(config, "PRICE_ANALYSIS_ENABLED", True)
    ctx = WeeklyDigestContext(
        week_key="2026-W23",
        week_label="2026-06-02",
        price=WeeklyPriceSnapshot(
            asset_id="algorand",
            asset_name="Algorand",
            currency="USD",
            price_usd=1.0,
            week_open_usd=1.0,
            week_high_usd=1.0,
            week_low_usd=1.0,
            week_change_pct=0.0,
            as_of=datetime(2026, 6, 2, tzinfo=UTC),
        ),
        articles=(),
    )

    monkeypatch.setattr(weekly_digest_publish, "build_weekly_digest", lambda **_kw: ctx)

    def fail_compose(_ctx: WeeklyDigestContext) -> Never:
        raise LLMError("MISTRAL_ENABLED and MISTRAL_API_KEY required — no template fallback")

    monkeypatch.setattr(weekly_digest_publish, "compose_weekly_digest", fail_compose)

    result = weekly_digest_publish.run_weekly_digest_publish()
    assert result["status"] == "mistral_failed"
    assert result["week"] == "2026-W23"


def test_run_publishes_new_digest_sanitizes_body_with_real_nh3_sanitizer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W1-B: this was the last weekly-digest write path still calling security.sanitize_body -- a regex-only <script>-tag stripper with no on*= handler / javascript:/data: URL stripping. It now uses the real nh3 allowlist sanitizer (article_store._sanitize_body), the same one insert_stored_article itself re-applies -- proving THIS call runs the real sanitizer, not just that the redundant internal one does.

    insert_article_if_absent is mocked here (matching test_run_publishes_new_digest
    above), so this exercises the real _sanitize_body call in
    run_weekly_digest_publish, not insert_stored_article's own internal pass.
    """
    import app.core.config as config

    monkeypatch.setattr(config, "PRICE_ANALYSIS_ENABLED", True)
    ctx = WeeklyDigestContext(
        week_key="2026-W23",
        week_label="2026-06-02",
        price=WeeklyPriceSnapshot(
            asset_id="algorand",
            asset_name="Algorand",
            currency="USD",
            price_usd=1.0,
            week_open_usd=1.0,
            week_high_usd=1.0,
            week_low_usd=1.0,
            week_change_pct=0.0,
            as_of=datetime(2026, 6, 2, tzinfo=UTC),
        ),
        articles=(),
    )

    malicious_body = (
        '<p onclick="alert(1)">Hello</p>'
        "<script>alert(2)</script>"
        '<a href="javascript:alert(3)">click</a>'
        '<img src="x" onerror="alert(4)">'
        " world"
    )

    def _fake_compose_malicious(_ctx: WeeklyDigestContext) -> Any:  # noqa: ANN401
        return type(
            "R",
            (),
            {"title": "T", "summary": "S", "body": malicious_body, "composer": "template"},
        )()

    monkeypatch.setattr(weekly_digest_publish, "build_weekly_digest", lambda **_kw: ctx)
    monkeypatch.setattr(weekly_digest_publish, "compose_weekly_digest", _fake_compose_malicious)
    monkeypatch.setattr(weekly_digest_publish.index_article, "delay", lambda **_kw: None)
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.publish_tasks.enqueue_article_translations",
        lambda _article_id: None,
    )

    captured_insert_kwargs: dict = {}

    def fake_insert(**kwargs: object) -> tuple[str, bool]:
        captured_insert_kwargs.update(kwargs)
        return str(kwargs["article_id"]), True

    monkeypatch.setattr(weekly_digest_publish, "insert_article_if_absent", fake_insert)
    result = weekly_digest_publish.run_weekly_digest_publish()

    assert result["status"] == "published"
    stored_body = captured_insert_kwargs["body"]
    assert "<script" not in stored_body
    assert "alert(2)" not in stored_body
    assert "onclick" not in stored_body
    assert "onerror" not in stored_body
    assert "javascript:" not in stored_body
    assert "Hello" in stored_body
    assert "world" in stored_body


def test_run_skips_cleanly_during_peak_hours(monkeypatch: pytest.MonkeyPatch) -> None:
    """The weekly-digest path is one of the 9 real compose-triggering task paths -- a PeakHoursBlockedError (owner decision 2026-08-15: no exceptions) must be reported as a routine skip, not logged/returned as a Mistral failure."""
    import app.core.config as config
    from app.modules.ai.llm_purpose_router import PeakHoursBlockedError

    monkeypatch.setattr(config, "PRICE_ANALYSIS_ENABLED", True)
    ctx = WeeklyDigestContext(
        week_key="2026-W23",
        week_label="2026-06-02",
        price=WeeklyPriceSnapshot(
            asset_id="algorand",
            asset_name="Algorand",
            currency="USD",
            price_usd=1.0,
            week_open_usd=1.0,
            week_high_usd=1.0,
            week_low_usd=1.0,
            week_change_pct=0.0,
            as_of=datetime(2026, 6, 2, tzinfo=UTC),
        ),
        articles=(),
    )

    monkeypatch.setattr(weekly_digest_publish, "build_weekly_digest", lambda **_kw: ctx)

    def fail_compose(_ctx: WeeklyDigestContext) -> Never:
        raise PeakHoursBlockedError("peak hours (DeepSeek billing) — next off-peak start at ...")

    monkeypatch.setattr(weekly_digest_publish, "compose_weekly_digest", fail_compose)

    result = weekly_digest_publish.run_weekly_digest_publish()
    assert result["status"] == "skipped_peak_hours"
    assert result["week"] == "2026-W23"
