from datetime import UTC, datetime

from app.modules.newspaper.publish_daily_guard import (
    reserve_publish_slot,
    release_publish_slot,
)
from app.modules.newspaper.publish_policy import PublishTier


class FakeRedis:
    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    def set(self, key: str, value: str | int, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self._data:
            return False
        self._data[key] = str(value)
        return True

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def incr(self, key: str) -> int:
        current = int(self._data.get(key, "0"))
        current += 1
        self._data[key] = str(current)
        return current

    def decr(self, key: str) -> int:
        current = int(self._data.get(key, "0"))
        current = max(0, current - 1)
        self._data[key] = str(current)
        return current

    def expire(self, key: str, seconds: int) -> bool:
        return True


def test_reserve_blocks_above_cap(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake)
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


def test_release_rolls_back(monkeypatch) -> None:
    fake = FakeRedis()
    monkeypatch.setattr("app.modules.newspaper.publish_daily_guard._client", lambda: fake)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard._ensure_counter_initialized",
        lambda **kwargs: None,
    )
    monkeypatch.setattr("app.core.config.NEWS_MAX_ARTICLES_PER_DAY", 7)

    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
    release_publish_slot(tier=PublishTier.STANDARD)
    assert reserve_publish_slot(tier=PublishTier.STANDARD)[0]
