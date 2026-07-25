"""Weekly digest id stability and week-key computation."""

from __future__ import annotations

from datetime import UTC, datetime

from app.modules.newspaper.weekly_digest import (
    current_week_key,
    digest_article_id,
    weekly_digest_trigger_id,
)


def test_digest_ids_are_stable_per_week() -> None:
    assert weekly_digest_trigger_id("2026-W23") == "weekly-digest-2026-W23"
    assert digest_article_id("2026-W23") == digest_article_id("2026-W23")
    assert digest_article_id("2026-W23") != digest_article_id("2026-W24")


def test_current_week_key() -> None:
    key = current_week_key(datetime(2026, 6, 2, tzinfo=UTC))
    assert key.startswith("2026-W")
