from __future__ import annotations

from datetime import UTC, datetime

from app.modules.newspaper import weekly_digest_publish
from app.modules.newspaper.price_analysis import WeeklyPriceSnapshot
from app.modules.newspaper.weekly_digest import WeeklyDigestContext


def _fake_compose(_ctx):
    return type(
        "R",
        (),
        {"title": "T", "summary": "S", "body": "B", "composer": "template"},
    )()


def test_run_skips_when_disabled(monkeypatch) -> None:
    import app.core.config as config

    monkeypatch.setattr(config, "PRICE_ANALYSIS_ENABLED", False)
    result = weekly_digest_publish.run_weekly_digest_publish()
    assert result["status"] == "skipped"


def test_run_skips_when_already_published(monkeypatch) -> None:
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

    monkeypatch.setattr(weekly_digest_publish, "build_weekly_digest", lambda **kw: ctx)
    monkeypatch.setattr(weekly_digest_publish, "compose_weekly_digest", _fake_compose)

    def fake_insert(**kwargs):
        return str(kwargs["article_id"]), False

    monkeypatch.setattr(weekly_digest_publish, "insert_article_if_absent", fake_insert)
    result = weekly_digest_publish.run_weekly_digest_publish()
    assert result["status"] == "skipped"
    assert result["reason"] == "already_published"


def test_run_publishes_new_digest(monkeypatch) -> None:
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

    monkeypatch.setattr(weekly_digest_publish, "build_weekly_digest", lambda **kw: ctx)
    monkeypatch.setattr(weekly_digest_publish, "compose_weekly_digest", _fake_compose)
    indexed: list[str] = []
    monkeypatch.setattr(
        weekly_digest_publish.index_article,
        "delay",
        lambda **kw: indexed.append(kw["article_id"]),
    )

    def fake_insert(**kwargs):
        return str(kwargs["article_id"]), True

    monkeypatch.setattr(weekly_digest_publish, "insert_article_if_absent", fake_insert)
    result = weekly_digest_publish.run_weekly_digest_publish()
    assert result["status"] == "published"
    assert result["feed_articles"] == "0"
    assert indexed
