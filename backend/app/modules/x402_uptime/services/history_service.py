"""Uptime-check history: record a real check, and answer the bounded `.../uptime/history` read.

See `models/domain.StoredUptimeCheck` for why only a genuine real (cache-miss)
check is ever recorded, and `services/percentiles.py` for the SEPARATE
in-call percentile feature this module does not duplicate: this one
aggregates across MANY independent real checks over days, that one computes
percentiles across a handful of samples taken during one call.
"""

from __future__ import annotations

import logging
import time

from app.core.config import settings
from app.modules.x402_directory.services.listing_service import url_hash
from app.modules.x402_uptime.models.domain import StoredUptimeCheck
from app.modules.x402_uptime.services.cache import is_down_fields
from app.modules.x402_uptime.services.checker import UptimeResult
from app.modules.x402_uptime.services.percentiles import compute_percentiles
from app.modules.x402_uptime.stores.base import UptimeHistoryStore
from app.modules.x402_uptime.stores.factory import get_uptime_history_store

logger = logging.getLogger(__name__)


def _check_json(check: StoredUptimeCheck) -> dict[str, object]:
    return {
        "checked_at_epoch": check.checked_at_epoch,
        "reachable": check.reachable,
        "http_status": check.http_status,
        "response_time_ms": check.response_time_ms,
        "error": check.error,
    }


class HistoryService:
    """Records real uptime checks and reads back a bounded, aggregated window."""

    def __init__(self, store: UptimeHistoryStore | None = None) -> None:
        """Take an explicit store for tests; otherwise resolve the configured one lazily."""
        self._store = store

    @property
    def store(self) -> UptimeHistoryStore:
        """The injected store, or the process-wide one built from settings."""
        return self._store or get_uptime_history_store()

    def record(self, normalized_url: str, result: UptimeResult, *, checked_at_epoch: int) -> None:
        """Write one history row for a REAL check only -- see StoredUptimeCheck's own docstring.

        Best-effort: a storage hiccup here must not turn an already-computed,
        already-paid-for check into a 500 for the caller -- the same
        fail-soft convention `cache.py`'s own `set_cached` uses for an
        identical reason (the result was already served; failing to persist
        it to history only costs a future history gap, not this call).
        """
        try:
            self.store.record(
                StoredUptimeCheck(
                    url_hash=url_hash(normalized_url),
                    url=normalized_url,
                    checked_at_epoch=checked_at_epoch,
                    reachable=result.reachable,
                    http_status=result.http_status,
                    response_time_ms=result.response_time_ms,
                    error=result.error,
                )
            )
        except Exception:
            logger.warning(
                "x402 uptime history: write failed for %s -- result was already served, "
                "this only costs a future history gap",
                normalized_url,
                exc_info=True,
            )

    def read(self, normalized_url: str, *, days: int) -> dict[str, object]:
        """Aggregate up to settings.x402_uptime_history_max_results real checks within `days`.

        For a target checked often, `x402_uptime_history_max_results` (the
        row cap on this read, not a time cap) can be exhausted well before
        the requested `days` boundary is reached -- e.g. the per-target real
        check rate limit alone allows up to
        settings.x402_uptime_target_rate_limit_per_hour checks/hour, which
        fills the default 500-row cap in roughly a day. Silently reporting
        `days` back unchanged in that case would make `uptime_pct` and the
        percentiles look like a full `days`-window figure when they are
        actually computed over a much narrower ACTUAL window. So this
        reports both: `window_days_requested` (the caller's clamped input,
        unchanged) and `window_days_actual` (the real span the returned rows
        cover, from the oldest returned row to the newest), plus
        `oldest_checked_at_epoch`/`newest_checked_at_epoch` and a `truncated`
        flag that is true exactly when the row cap -- not the `days`
        boundary or a lack of history -- is what stopped the read short.
        """
        since_epoch = int(time.time()) - days * 86400
        limit = settings.x402_uptime_history_max_results
        checks = self.store.list_since(
            url_hash(normalized_url),
            since_epoch=since_epoch,
            limit=limit,
        )
        total = len(checks)
        down_count = sum(
            1 for c in checks if is_down_fields(reachable=c.reachable, http_status=c.http_status)
        )
        uptime_pct = round((total - down_count) / total * 100, 2) if total else None
        percentiles = compute_percentiles([c.response_time_ms for c in checks if c.reachable])
        # `checks` is newest-first (the store's own contract -- see
        # UptimeHistoryStore.list_since's docstring), so the first/last
        # elements are the newest/oldest without a second sort.
        newest_epoch = checks[0].checked_at_epoch if checks else None
        oldest_epoch = checks[-1].checked_at_epoch if checks else None
        window_days_actual = (
            round((newest_epoch - oldest_epoch) / 86400, 2)
            if newest_epoch is not None and oldest_epoch is not None
            else 0.0
        )
        return {
            "target_url": normalized_url,
            "window_days_requested": days,
            "window_days_actual": window_days_actual,
            "truncated": total >= limit,
            "oldest_checked_at_epoch": oldest_epoch,
            "newest_checked_at_epoch": newest_epoch,
            "checks_recorded": total,
            "uptime_pct": uptime_pct,
            "latency_p50_ms": percentiles["p50"],
            "latency_p90_ms": percentiles["p90"],
            "latency_p99_ms": percentiles["p99"],
            "latency_min_ms": percentiles["min"],
            "latency_max_ms": percentiles["max"],
            "history": [_check_json(c) for c in checks],
        }


__all__ = ["HistoryService"]
