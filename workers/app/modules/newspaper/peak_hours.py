"""Confine LLM compose calls to off-peak hours (DeepSeek peak/off-peak billing, effective 2026-08-16 16:00 UTC).

Owner decision 2026-08-15: ALL compose (including breaking news -- no
exception) is confined to off-peak hours, with a start-margin so a new
compose is never begun close enough to a peak window that it could still be
running once peak starts. An already-running compose that crosses into peak
is fine (each token is billed at whatever rate applies when the request is
actually made) -- the margin only protects the START decision.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core import config


def _parse_peak_windows(spec: str) -> list[tuple[int, int]]:
    """"1-4,6-10" -> [(1, 4), (6, 10)] -- each (start_hour, end_hour) is a UTC hour-of-day range, end exclusive, both 0-23. Malformed entries are skipped, never raise -- a bad env value must degrade to "no peak windows" (fail toward NOT blocking compose), not crash every task that calls into the composer."""
    windows: list[tuple[int, int]] = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part or "-" not in part:
            continue
        start_s, _, end_s = part.partition("-")
        try:
            start, end = int(start_s), int(end_s)
        except ValueError:
            continue
        if 0 <= start <= 24 and 0 <= end <= 24 and start < end:
            windows.append((start, end))
    return windows


def _peak_window_starts_within(now: datetime, windows: list[tuple[int, int]], horizon_minutes: float) -> bool:
    """True if `now` falls inside a peak window, OR any peak window's start time is within the next `horizon_minutes` (today's remaining windows and tomorrow's early ones, so a check near midnight correctly sees a window that starts just after it)."""
    for day_offset in (0, 1):
        base = (now + timedelta(days=day_offset)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        for start_hour, end_hour in windows:
            window_start = base + timedelta(hours=start_hour)
            window_end = base + timedelta(hours=end_hour)
            if window_start <= now < window_end:
                return True  # currently inside a peak window
            if now <= window_start <= now + timedelta(minutes=horizon_minutes):
                return True  # a peak window starts within the margin
    return False


def is_off_peak_now(*, margin_minutes: float | None = None) -> bool:
    """True only if starting a compose RIGHT NOW would not currently be, nor soon become, inside a configured peak window within the margin. No configured peak windows (LLM_PEAK_HOURS_UTC empty/unparseable) -- always True, fail open."""
    windows = _parse_peak_windows(config.LLM_PEAK_HOURS_UTC)
    if not windows:
        return True
    margin = config.LLM_PEAK_MARGIN_MINUTES if margin_minutes is None else margin_minutes
    now = datetime.now(tz=UTC)
    return not _peak_window_starts_within(now, windows, margin)


def next_off_peak_at(*, margin_minutes: float | None = None) -> datetime | None:
    """When compose would next be allowed to start, for status/logging -- None if it's already off-peak (or no windows configured) right now."""
    if is_off_peak_now(margin_minutes=margin_minutes):
        return None
    windows = _parse_peak_windows(config.LLM_PEAK_HOURS_UTC)
    margin = config.LLM_PEAK_MARGIN_MINUTES if margin_minutes is None else margin_minutes
    now = datetime.now(tz=UTC)
    # Walk forward in small steps until the margin check clears -- simple and
    # correct is worth more here than clever interval math for a value that's
    # only ever read for a human-facing status message, not a hot path.
    candidate = now
    step = timedelta(minutes=5)
    for _ in range(24 * 60 // 5 + 1):  # bounded: at most one full day of stepping
        candidate += step
        if not _peak_window_starts_within(candidate, windows, margin):
            return candidate
    return None
