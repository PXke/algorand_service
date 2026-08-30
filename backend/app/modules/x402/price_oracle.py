"""USD rates for the non-USDC assets the payment gate accepts.

One CoinGecko call fetches every non-USDC rate at once and caches each in Redis
for an hour. There is no scheduler here — this backend has none, and adding a
dependency on workers/'s Celery beat (a different service) to price a payment
would couple the payment path to a service that can be down. So the refresh is
lazy: the first read after the hour expires pays for the refetch.

Failure behaviour, in order of preference:

1. Cached and fresh -> serve it.
2. Expired, refetch succeeds -> serve the new rate.
3. Expired, refetch fails -> serve the last-known-good rate and log a warning.
   A stale rate is better than a broken payment flow; rates move slowly enough
   that an hour-or-two-old quote is still an honest price.
4. No last-known-good has ever been written (cold start, and the first fetch
   failed) -> return None. The caller omits that asset from the 402 offer
   rather than quoting a made-up or zero price. USDC needs no oracle and is
   always offered, so the endpoint never becomes unpayable because of this.

Redis is treated as best-effort the same way replay.py treats it: an outage
logs a warning and degrades the offer to USDC-only. It never raises into the
payment path, and it never causes a wrong amount to be charged.
"""

from __future__ import annotations

import logging
from decimal import Decimal, InvalidOperation

import httpx

from app.core.redis_client import get_redis
from app.modules.x402.assets import ACCEPTED_ASSETS

logger = logging.getLogger(__name__)

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

# The free, keyless CoinGecko endpoint. A key would be a new secret to hold for
# data that is public and non-authoritative to us.
_HTTP_TIMEOUT_SECONDS = 5.0

_KEY_PREFIX = "algorand:x402:rate:"
# Fresh key: presence means "cached within the hour". Its TTL *is* the cache
# policy, so an expiry needs no timestamp bookkeeping of its own.
_FRESH_TTL_SECONDS = 3600
# Last-known-good: written on every success and never expired, so a long
# CoinGecko outage degrades to a stale quote rather than to no quote.
_LKG_KEY = _KEY_PREFIX + "lkg:"
_FRESH_KEY = _KEY_PREFIX + "fresh:"
# After a failed fetch, don't re-hit CoinGecko on every single request — that
# would put an outbound HTTP call with a 5s timeout in front of every paid
# route while the provider is down.
_COOLDOWN_KEY = _KEY_PREFIX + "cooldown"
_COOLDOWN_SECONDS = 60


def _oracle_priced_ids() -> list[str]:
    """CoinGecko ids for every accepted asset that needs an oracle (i.e. not USDC)."""
    return [a.coingecko_id for a in ACCEPTED_ASSETS if a.coingecko_id is not None]


def _parse_rate(raw: str | None) -> Decimal | None:
    """Decode a cached rate, rejecting anything not a usable positive number."""
    if not raw:
        return None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        logger.warning("x402 price oracle found an undecodable cached rate: %r", raw)
        return None
    if value <= 0:
        logger.warning("x402 price oracle found a non-positive cached rate: %r", raw)
        return None
    return value


def _fetch_rates(*, transport: httpx.BaseTransport | None = None) -> dict[str, Decimal]:
    """Fetch every oracle-priced rate in one call. Returns {} on any failure.

    One call for all of them: the CoinGecko endpoint batches ids, and three
    separate calls would triple both the latency in front of a paid route and
    our footprint against a free, rate-limited API.

    `transport` is the test seam (same shape as media/api/routes.py's fetch) —
    production passes nothing.
    """
    ids = _oracle_priced_ids()
    if not ids:
        return {}
    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT_SECONDS, transport=transport) as client:
            response = client.get(
                COINGECKO_URL,
                params={"ids": ",".join(ids), "vs_currencies": "usd"},
            )
            response.raise_for_status()
            body = response.json()
    except Exception as exc:
        logger.warning("x402 price oracle fetch failed: %s", exc)
        return {}

    if not isinstance(body, dict):
        logger.warning("x402 price oracle got a non-object response: %r", type(body))
        return {}

    rates: dict[str, Decimal] = {}
    for coingecko_id in ids:
        entry = body.get(coingecko_id)
        if not isinstance(entry, dict):
            logger.warning("x402 price oracle response is missing %s", coingecko_id)
            continue
        usd = entry.get("usd")
        if not isinstance(usd, int | float) or isinstance(usd, bool):
            logger.warning(
                "x402 price oracle got a non-numeric usd value for %s: %r", coingecko_id, usd
            )
            continue
        # str() first: Decimal(float) would carry the float's binary error into
        # an amount someone is charged.
        rate = Decimal(str(usd))
        if rate <= 0:
            logger.warning(
                "x402 price oracle got a non-positive rate for %s: %r", coingecko_id, usd
            )
            continue
        rates[coingecko_id] = rate
    return rates


def _refresh(redis: object) -> None:
    """Refetch every rate and update both the fresh and last-known-good caches.

    Fails soft in both directions: a failed fetch arms a short cooldown so the
    next request does not pay for the same timeout, and a Redis write failure
    only costs us the caching, never the request.
    """
    rates = _fetch_rates()
    try:
        if not rates:
            redis.set(_COOLDOWN_KEY, "1", ex=_COOLDOWN_SECONDS)  # type: ignore[attr-defined]
            return
        for coingecko_id, rate in rates.items():
            # Store before mark: the durable last-known-good is written first,
            # so a crash between the two can only cost freshness, never the
            # fallback value itself.
            redis.set(_LKG_KEY + coingecko_id, str(rate))  # type: ignore[attr-defined]
            redis.set(_FRESH_KEY + coingecko_id, str(rate), ex=_FRESH_TTL_SECONDS)  # type: ignore[attr-defined]
    except Exception:
        logger.warning("x402 price oracle could not write rates to Redis", exc_info=True)


def get_usd_rate(asset: str) -> Decimal | None:
    """USD price of one unit of `asset`, where `asset` is its CoinGecko id.

    Returns None when no rate is available at all — never raises for that
    routine case, because a missing rate must degrade the 402 offer rather than
    fail the request. The caller drops that asset from `accepts`.
    """
    try:
        redis = get_redis()
        fresh = _parse_rate(redis.get(_FRESH_KEY + asset))
        if fresh is not None:
            return fresh

        if not redis.exists(_COOLDOWN_KEY):
            _refresh(redis)
            refreshed = _parse_rate(redis.get(_FRESH_KEY + asset))
            if refreshed is not None:
                return refreshed

        stale = _parse_rate(redis.get(_LKG_KEY + asset))
    except Exception:
        logger.warning(
            "x402 price oracle could not reach Redis for %s; this asset will be omitted "
            "from the payment offer (USDC is unaffected)",
            asset,
            exc_info=True,
        )
        return None

    if stale is not None:
        logger.warning(
            "x402 price oracle serving a stale rate for %s (refresh failed); "
            "the quoted price may lag the market",
            asset,
        )
        return stale

    logger.warning(
        "x402 price oracle has no rate for %s and no last-known-good value; "
        "omitting it from the payment offer",
        asset,
    )
    return None
