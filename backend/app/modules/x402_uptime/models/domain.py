"""Domain types for x402 uptime-check history (the paid `.../uptime/history` read).

Distinct from `services/checker.UptimeResult`, which is the shape of ONE
stateless call's outcome and is never itself persisted -- `StoredUptimeCheck`
is the durable row this codebase writes to `x402_uptime_checks_by_url`
(migration 117) so a later `.../uptime/history` read can aggregate across
many of them.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StoredUptimeCheck:
    """One real (cache-miss) uptime check, recorded for a later `.../uptime/history` read.

    Written only when a real network check actually ran -- see
    `services/history_service.py`'s `HistoryService.record` and
    `api/routes.py`'s `_uptime_product_write`: a served cache hit, or a
    budget-exhausted stale-cache serve, is a RE-serving of an OLD
    measurement, not a new one. Recording those too would duplicate the same
    reading many times over in the history table -- skewing an "uptime %
    over N days" toward how often callers happened to ask rather than
    toward how often the target was actually measured -- and would make
    table growth track raw paid-call volume instead of the already
    rate-limited real-check cadence
    (`settings.x402_uptime_target_rate_limit_per_hour`).
    """

    url_hash: str
    url: str
    checked_at_epoch: int
    reachable: bool
    http_status: int
    response_time_ms: int
    error: str
