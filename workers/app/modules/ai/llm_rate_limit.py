"""Client-side rate limiting for LLM provider calls (Mistral and DeepSeek), coordinated across all Celery worker processes via Redis.

Why this exists: provider accounts cap requests/second AND a tokens/minute
budget (e.g. Mistral's mistral-medium-2505 was 0.42 rps / 375k TPM). The
article writer runs an agentic tool loop and several drains can compose at
once, so without a shared gate the workers burst past the per-second ceiling
and the gateway rejects with instant 429s.

The gate is a leaky bucket on a single Redis key holding the next free slot
timestamp. Each caller atomically reserves the next slot (>= now, spaced by the
configured interval) and sleeps until then, so requests leave at most one per
interval no matter how many workers ask at once. If Redis is unreachable we fall
back to a process-local interval (best effort — better than no spacing).

One shared gate covers both providers rather than a gate each (2026-08-13):
they've never actually collided with either provider's real limit in
practice (zero observed 429s from either), so the extra bookkeeping of a
second Redis key/interval isn't earning its keep yet — split it if that
ever changes.
"""

from __future__ import annotations

import threading
import time

from app.core.config import LLM_MIN_REQUEST_INTERVAL_SECONDS, REDIS_URL

_KEY = "llm:ratelimit:next_slot"

# Atomically reserve the next departure slot: max(now, last + interval), then
# store it back. Returns the reserved slot (epoch seconds, as a string to keep
# float precision across the Redis number boundary).
_RESERVE_LUA = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local interval = tonumber(ARGV[2])
local last = tonumber(redis.call('get', key) or '0')
local slot = now
if last + interval > now then
  slot = last + interval
end
redis.call('set', key, tostring(slot), 'PX', math.floor(interval * 1000) + 60000)
return tostring(slot)
"""

# Process-local fallback when Redis is down (does not serialize across processes,
# but still paces a single worker).
_local_lock = threading.Lock()
_local_next = 0.0


def _reserve_slot_redis(interval: float) -> float:
    import redis

    client = redis.from_url(REDIS_URL, decode_responses=True)
    now = time.time()
    slot = client.eval(_RESERVE_LUA, 1, _KEY, repr(now), repr(interval))
    return float(slot)


def _reserve_slot_local(interval: float) -> float:
    global _local_next
    with _local_lock:
        now = time.time()
        slot = max(now, _local_next)
        _local_next = slot + interval
        return slot


def throttle_llm_call() -> None:
    """Block until this process is allowed to send one LLM request (Mistral or DeepSeek).

    Reserves the next slot in the shared leaky bucket and sleeps until it is due.
    Never raises: any Redis failure degrades to process-local pacing.
    """
    interval = float(LLM_MIN_REQUEST_INTERVAL_SECONDS)
    if interval <= 0:
        return
    try:
        slot = _reserve_slot_redis(interval)
    except Exception:
        slot = _reserve_slot_local(interval)
    wait = slot - time.time()
    if wait > 0:
        time.sleep(wait)
