"""Redis queue the backend pushes external ingest signals onto for the workers to consume."""

from __future__ import annotations

import json
from typing import Any

import redis

from app.core.config import REDIS_URL

QUEUE_KEY = "algorand:ingest:external_signals"


def _client() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def push_external_signal(payload: dict[str, Any]) -> int:
    """Enqueue a push ingest payload (API or bridge). Returns queue depth after push."""
    return int(_client().lpush(QUEUE_KEY, json.dumps(payload, separators=(",", ":"))))


def pop_external_signal() -> dict[str, Any] | None:
    """Dequeue and return the oldest pending external ingest signal, or None if empty/invalid."""
    raw = _client().rpop(QUEUE_KEY)
    if not raw:
        return None
    data = json.loads(raw)
    if not isinstance(data, dict):
        return None
    return data


def queue_depth() -> int:
    """Return the number of pending external ingest signals."""
    return int(_client().llen(QUEUE_KEY))
