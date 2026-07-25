"""Redis-backed per-source cooldown and failure backoff."""

from __future__ import annotations

import time

import redis

from app.core.config import (
    DEAD_HOST_COOLDOWN_SECONDS,
    REDIS_URL,
    SCRAPE_BACKOFF_BASE_SECONDS,
    SCRAPE_BACKOFF_MAX_SECONDS,
    SCRAPE_BACKOFF_MULTIPLIER,
)
from app.core.net_guard import UnsafeUrlError


def _client() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def _key(service_id: str) -> str:
    return f"scrape:cooldown:{service_id}"


def _fail_key(service_id: str) -> str:
    return f"scrape:cooldown:fails:{service_id}"


def backoff_duration(failures: int) -> int:
    """Seconds to wait after `failures` consecutive failures (1-based).

    First failure waits the base; each subsequent one multiplies, capped.
    """
    n = max(1, failures)
    raw = SCRAPE_BACKOFF_BASE_SECONDS * (SCRAPE_BACKOFF_MULTIPLIER ** (n - 1))
    return int(min(SCRAPE_BACKOFF_MAX_SECONDS, max(SCRAPE_BACKOFF_BASE_SECONDS, raw)))


def is_permanent_failure(exc: BaseException) -> bool:
    """A non-resolving or non-public host won't recover on the retry cadence.

    The SSRF guard raises `UnsafeUrlError` (dead DNS, non-public IP); scrapers
    wrap it, so walk the cause/context chain, then fall back to a message sniff.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, UnsafeUrlError):
            return True
        cur = cur.__cause__ or cur.__context__
    return "dns resolution failed" in str(exc).lower()


def cooldown_for_exception(exc: BaseException) -> int | None:
    """Fixed long cooldown for permanent failures, else None (use exponential)."""
    return DEAD_HOST_COOLDOWN_SECONDS if is_permanent_failure(exc) else None


def is_on_cooldown(service_id: str) -> tuple[bool, str]:
    """Return whether the source is still cooling down, with a reason tag."""
    raw = _client().get(_key(service_id))
    if not raw:
        return False, ""
    try:
        until = int(raw)
    except ValueError:
        return False, ""
    now = int(time.time())
    if now >= until:
        return False, ""
    return True, f"cooldown_until_{until}"


def record_scrape_failure(service_id: str, *, seconds: int | None = None) -> int:
    """Pause polls for this registry row after 401/403/429 storms.

    Each consecutive failure backs off exponentially (Reddit rate-limit
    friendly) until `clear_scrape_cooldown` resets the streak on success.
    An explicit `seconds` overrides the exponential schedule (fixed pause).
    Returns the cooldown duration applied.
    """
    client = _client()
    if seconds is not None:
        duration = max(60, seconds)
    else:
        failures = client.incr(_fail_key(service_id))
        # Keep the failure streak alive across the cooldown window plus slack.
        client.expire(_fail_key(service_id), SCRAPE_BACKOFF_MAX_SECONDS * 2)
        duration = backoff_duration(int(failures))
    until = int(time.time()) + duration
    client.set(_key(service_id), str(until), ex=duration + 60)
    return duration


def clear_scrape_cooldown(service_id: str) -> None:
    """Reset the cooldown and the failure streak after a successful scrape."""
    client = _client()
    client.delete(_key(service_id))
    client.delete(_fail_key(service_id))


# --- Success-path poll throttle --------------------------------------------
# Distinct from the failure backoff above: sets the watch CADENCE for a healthy
# monitored service. A successful poll stamps the full SERVICE_RESCRAPE_DAYS
# window (weekly by default — the service-evolution diff is the story; there is
# nothing to gain from re-fetching sooner). A failed poll stamps only
# SCRAPE_COOLDOWN_SECONDS so one transient error doesn't cost a whole window.
# Fails OPEN so a Redis outage never silently halts polling.
def _throttle_key(service_id: str) -> str:
    return f"scrape:throttle:{service_id}"


def rescrape_window_seconds(*, ok: bool = True) -> int:
    """Seconds until the next allowed poll: the weekly window on success, the short cooldown otherwise."""
    from app.core.config import SCRAPE_COOLDOWN_SECONDS, SERVICE_RESCRAPE_DAYS

    if not ok or SERVICE_RESCRAPE_DAYS <= 0:
        return SCRAPE_COOLDOWN_SECONDS
    return int(SERVICE_RESCRAPE_DAYS * 86400)


def scrape_throttled(service_id: str) -> bool:
    """True when this source was polled within its re-scrape window."""
    if rescrape_window_seconds() <= 0 or not service_id:
        return False
    try:
        return bool(_client().exists(_throttle_key(service_id)))
    except Exception:
        return False


def mark_scraped(service_id: str, *, ok: bool = True) -> None:
    """Stamp a poll so the source isn't re-fetched until the window elapses."""
    window = rescrape_window_seconds(ok=ok)
    if window <= 0 or not service_id:
        return
    try:
        _client().set(_throttle_key(service_id), "1", ex=window)
    except Exception:
        return
