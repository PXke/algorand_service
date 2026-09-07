"""In-call latency percentiles: N samples of the SAME target within one paid check, no new storage.

Satisfies the actual agent ask (Moltbook feedback, verified 2026-09-06:
"P99 latency matters for trading agents -- give me percentiles, not just
averages") at zero new infra cost and zero new abuse surface beyond the
tradeoff documented below. This is a deliberately DIFFERENT feature from
the persistent `.../uptime/history` read (see `services/history_service.py`
and `stores/`): that one aggregates MANY independent real checks over days;
this one answers "what does this target look like right now," from a
handful of back-to-back samples taken during a single paid call.

DDoS-amplification tradeoff (read before changing sample counts): the
per-target real-fetch rate limiter (`rate_limit.target_over_budget`) is
called exactly ONCE per call by `api/routes.py._uptime_product_write`, for
the WHOLE multi-sample call -- a multi-sample call counts as one unit
against that budget, same as a single-sample call always did, because it is
one payment. That is accepted, not an oversight: on a genuine cache MISS,
this module now makes up to `settings.x402_uptime_percentile_samples` real
HTTP requests against the caller's target instead of one. It does NOT
multiply with caller/payer volume, because a fresh cache hit (the
overwhelmingly common case once a target has been checked once within its
TTL window -- see cache.py's asymmetric TTLs) never calls this at all. The
worst case is bounded further by `x402_uptime_percentile_budget_s`: a slow
or hanging target does not get to hold one paid HTTP request open for
`samples * per_attempt_timeout` (up to 100s at the default settings) --
sampling stops once the running wall-clock total crosses the budget, and
percentiles are computed over however many samples were actually collected
(always at least one, the same single result callers already got before
this feature existed).
"""

from __future__ import annotations

import time
from math import ceil

from app.core.config import settings
from app.modules.x402_uptime.services.checker import UptimeResult, check_target


def sample_target(
    normalized_url: str, *, samples: int | None = None
) -> tuple[UptimeResult, list[int]]:
    """Run 1..`samples` (default settings.x402_uptime_percentile_samples) real checks of `normalized_url`.

    Returns the FIRST sample's full UptimeResult unchanged (the single-check
    contract this product already had -- reachable/http_status/final_url/
    redirect_chain/resolved_ip/error all come from this one, "primary"
    sample) plus every collected sample's response_time_ms, for
    `compute_percentiles()`. Always takes at least one sample; stops taking
    more once the running wall-clock total exceeds
    `x402_uptime_percentile_budget_s`, or once the wanted sample count is
    reached, whichever comes first.

    `samples` is an explicit override for callers that must not amplify to
    the full configured sample count regardless of its value -- see
    `api/routes.py`'s `_handle_preview_check`, which passes `samples=1` so
    an unpaid, unauthenticated `?preview=true` call can never trigger the
    same multi-sample real-fetch amplification against a caller-supplied
    third-party URL that a paid call does. Omit it (or pass `None`) for the
    real, paid path, which still uses the configured setting.
    """
    started = time.monotonic()
    primary = check_target(
        normalized_url,
        timeout_s=settings.x402_uptime_check_timeout_s,
        max_redirects=settings.x402_uptime_max_redirects,
    )
    latencies_ms = [primary.response_time_ms]
    wanted = max(1, samples if samples is not None else settings.x402_uptime_percentile_samples)
    for _ in range(wanted - 1):
        if time.monotonic() - started >= settings.x402_uptime_percentile_budget_s:
            break
        extra = check_target(
            normalized_url,
            timeout_s=settings.x402_uptime_check_timeout_s,
            max_redirects=settings.x402_uptime_max_redirects,
        )
        latencies_ms.append(extra.response_time_ms)
    return primary, latencies_ms


def compute_percentiles(samples_ms: list[int]) -> dict[str, int]:
    """Nearest-rank percentiles over `samples_ms`; "count" is how many samples went in.

    With the small sample counts this product actually takes (default 5),
    p90 and p99 both collapse to the single highest observed sample -- an
    honest property of nearest-rank percentiles at low N, not a bug: a
    statistically meaningful p99 needs hundreds of independent observations,
    which is exactly what the separate, persistent `.../uptime/history` read
    is for (aggregating many real checks over up to
    `x402_uptime_history_max_days` days). This function answers "what did we
    see just now," not "what is the long-run tail."
    """
    if not samples_ms:
        return {"count": 0, "min": 0, "max": 0, "p50": 0, "p90": 0, "p99": 0}
    ordered = sorted(samples_ms)
    n = len(ordered)

    def _rank(pct: int) -> int:
        idx = min(n - 1, max(0, ceil(pct / 100 * n) - 1))
        return ordered[idx]

    return {
        "count": n,
        "min": ordered[0],
        "max": ordered[-1],
        "p50": _rank(50),
        "p90": _rank(90),
        "p99": _rank(99),
    }


__all__ = ["compute_percentiles", "sample_target"]
