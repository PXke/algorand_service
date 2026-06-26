from __future__ import annotations

from datetime import UTC, datetime

from app.modules.newspaper.price_analysis import WeeklyPriceSnapshot
from app.modules.newspaper.weekly_digest import (
    DigestArticleItem,
    WeeklyDigestContext,
    compose_weekly_digest_article,
    current_week_key,
    digest_article_id,
    weekly_digest_trigger_id,
)


def _snapshot() -> WeeklyPriceSnapshot:
    return WeeklyPriceSnapshot(
        asset_id="algorand",
        asset_name="Algorand",
        currency="USD",
        price_usd=0.25,
        week_open_usd=0.20,
        week_high_usd=0.26,
        week_low_usd=0.19,
        week_change_pct=25.0,
        as_of=datetime(2026, 6, 2, 12, 0, tzinfo=UTC),
    )


def test_compose_weekly_digest_includes_feed() -> None:
    ctx = WeeklyDigestContext(
        week_key="2026-W23",
        week_label="2026-06-02",
        price=_snapshot(),
        articles=(
            DigestArticleItem(
                article_id="a1",
                service_id="discord-room-1",
                title="Discord update",
                summary="Channel activity",
                published_at_epoch=1748800000,
            ),
        ),
    )
    title, _summary, body = compose_weekly_digest_article(ctx)
    assert "digest" in title.lower()
    assert _summary
    assert "Discord update" in body
    assert "highlights" in body.lower() or "CoinGecko" in body
    assert len(body) < 4000


def test_digest_ids_are_stable_per_week() -> None:
    assert weekly_digest_trigger_id("2026-W23") == "weekly-digest-2026-W23"
    assert digest_article_id("2026-W23") == digest_article_id("2026-W23")
    assert digest_article_id("2026-W23") != digest_article_id("2026-W24")


def test_current_week_key() -> None:
    key = current_week_key(datetime(2026, 6, 2, tzinfo=UTC))
    assert key.startswith("2026-W")
