"""Atomic daily publish-slot reservation and rollback."""

import pytest
from conftest import FakeRedis

from app.modules.newspaper.publish_daily_guard import (
    release_publish_slot,
    reserve_publish_slot,
)
from app.modules.newspaper.publish_policy import PublishTier


def test_reserve_blocks_above_cap(monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
    """Reservations succeed up to the daily cap, then the next one is rejected with a "cap" reason."""
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard._ensure_counter_initialized",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("app.core.config.NEWS_MAX_ARTICLES_PER_DAY", 2)

    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
    ok, reason = reserve_publish_slot(tier=PublishTier.STANDARD)
    assert not ok
    assert "cap" in reason


def test_release_rolls_back(monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis) -> None:
    """Releasing a reserved slot frees it up so a subsequent reservation can succeed."""
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard._ensure_counter_initialized",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr("app.core.config.NEWS_MAX_ARTICLES_PER_DAY", 7)

    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
    release_publish_slot(tier=PublishTier.STANDARD)
    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
