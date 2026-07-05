
from app.modules.newspaper.publish_daily_guard import (
    release_publish_slot,
    reserve_publish_slot,
)
from app.modules.newspaper.publish_policy import PublishTier


def test_reserve_blocks_above_cap(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard._ensure_counter_initialized",
        lambda **kwargs: None,
    )
    monkeypatch.setattr("app.core.config.NEWS_MAX_ARTICLES_PER_DAY", 2)

    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
    ok, reason = reserve_publish_slot(tier=PublishTier.STANDARD)
    assert not ok
    assert "cap" in reason


def test_release_rolls_back(monkeypatch, fake_redis) -> None:
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake_redis)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard._ensure_counter_initialized",
        lambda **kwargs: None,
    )
    monkeypatch.setattr("app.core.config.NEWS_MAX_ARTICLES_PER_DAY", 7)

    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
    release_publish_slot(tier=PublishTier.STANDARD)
    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
