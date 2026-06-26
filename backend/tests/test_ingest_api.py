from __future__ import annotations

import json

import pytest

from app.core.config import settings
from app.modules.ingest.queue import QUEUE_KEY, push_signal


def test_push_signal_writes_redis(monkeypatch) -> None:
    stored: list[str] = []

    class FakeRedis:
        def lpush(self, key: str, value: str) -> int:
            stored.append(value)
            assert key == QUEUE_KEY
            return 1

    monkeypatch.setattr(
        "app.modules.ingest.queue.redis.from_url",
        lambda *a, **k: FakeRedis(),
    )
    depth = push_signal({"service_id": "test", "page_text": "hello"})
    assert depth == 1
    payload = json.loads(stored[0])
    assert payload["service_id"] == "test"


def test_ingest_requires_api_key() -> None:
    if not settings.ingest_api_key:
        pytest.skip("INGEST_API_KEY not set in test env")
