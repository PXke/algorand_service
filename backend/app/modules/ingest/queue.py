"""Redis queue that hands ingest signals to the workers service."""

from __future__ import annotations

import json
from typing import Any

import redis

from app.core.config import settings

QUEUE_KEY = "algorand:ingest:external_signals"


def push_signal(payload: dict[str, Any]) -> int:
    """Push a JSON-encoded ingest signal onto the workers' Redis queue."""
    client = redis.from_url(settings.redis_url, decode_responses=True)
    return int(client.lpush(QUEUE_KEY, json.dumps(payload, separators=(",", ":"))))
