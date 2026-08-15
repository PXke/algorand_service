"""Publish-queue priority ordering and per-source diversity caps."""

from __future__ import annotations

import random
from datetime import UTC, datetime

from app.modules.newspaper.publish_queue_store import QueuedPublishRow, order_for_drain


def _row(qid: str, priority: int, url: str, *, created_at_epoch: float = 0) -> QueuedPublishRow:
    return QueuedPublishRow(
        queue_id=qid,
        priority=priority,
        topic="",
        publish_kind="",
        service_id=url,
        display_name="",
        scrape_url=url,
        payload={},
        created_at_epoch=created_at_epoch,
    )


def test_priority_dominates_on_average() -> None:
    """Higher priority wins the head slot far more often than not, even though the weighted-random
    draw (see _weighted_shuffle_key) means it is not a guaranteed, every-single-time outcome --
    that give-lower-priority-a-real-shot property is intentional (owner-approved 'quite some
    randomness' for updates), not a bug. A single draw with widely-separated priorities used to
    assert strict determinism here; that assumption no longer holds now that order_for_drain
    draws across the whole priority axis instead of exact-tie tiers."""
    # created_at_epoch pinned to "now" so age_bonus is ~0 for all three rows --
    # _row's default of epoch 0 (1970) would otherwise cap every row's age bonus
    # at DRAIN_AGE_BONUS_MAX, swamping this small priority gap in a shared
    # +120 baseline and flattening the draw toward uniform.
    now = datetime.now(tz=UTC).timestamp()
    rows = [
        _row("low", 1, "https://a.com/1", created_at_epoch=now),
        _row("high", 9, "https://b.com/1", created_at_epoch=now),
        _row("mid", 5, "https://c.com/1", created_at_epoch=now),
    ]
    random.seed(7)
    wins = {"low": 0, "high": 0, "mid": 0}
    for _ in range(500):
        wins[order_for_drain(rows)[0].queue_id] += 1
    assert wins["high"] > wins["mid"] > wins["low"]
    assert wins["high"] > 250  # ~9/15 expected share, comfortably the plurality


def test_flood_source_does_not_monopolize_head() -> None:
    """A single source flooding a priority tier does not push a lone other-source item past the first round."""
    # One domain floods a priority tier; a lone item from another source must
    # surface in the first round, not after all 20 flood items.
    random.seed(0)
    flood = [_row(f"f{i}", 5, f"https://flood.com/{i}") for i in range(20)]
    other = _row("other", 5, "https://other.com/1")
    ordered = order_for_drain([*flood, other])
    pos = next(i for i, r in enumerate(ordered) if r.queue_id == "other")
    # Two distinct sources => round-robin reaches "other" within the first round.
    assert pos < 2


def test_keeps_every_row() -> None:
    """Reordering preserves every input row exactly once, with none dropped or duplicated."""
    random.seed(1)
    rows = [_row(f"x{i}", i % 3, f"https://d{i % 4}.com/{i}") for i in range(30)]
    ordered = order_for_drain(rows)
    assert {r.queue_id for r in ordered} == {r.queue_id for r in rows}
    assert len(ordered) == len(rows)


def test_subdomains_of_same_domain_count_as_one_source() -> None:
    """Treats subdomains of the same root domain as a single source for round-robin diversity."""
    # explore.perawallet.app and perawallet.app are the same project; the
    # interleave must treat them as ONE source so a third source still surfaces
    # in the first round instead of being buried behind both Pera subdomains.
    random.seed(3)
    pera = [
        _row("pera_root", 5, "https://perawallet.app/x"),
        _row("pera_sub", 5, "https://explore.perawallet.app/y"),
    ]
    other = _row("other", 5, "https://tinyman.org/z")
    ordered = order_for_drain([*pera, other])
    pos = next(i for i, r in enumerate(ordered) if r.queue_id == "other")
    # Two real sources (perawallet.app, tinyman.org) => "other" within first round.
    assert pos < 2


def test_lower_priority_never_jumps_ahead_despite_diversity() -> None:
    """A rare high-priority source still leads even when many low-priority items compete for diversity slots."""
    random.seed(2)
    # Rare high-priority source vs common low-priority source.
    rows = [_row("hp", 9, "https://rare.com/1")]
    rows += [_row(f"lp{i}", 1, f"https://common.com/{i}") for i in range(10)]
    ordered = order_for_drain(rows)
    assert ordered[0].queue_id == "hp"
