"""_domain_sort_key: pending-review ordering by relevance.

Content-classified domains (content_relevance set by classify_pending_domains)
sort by that 0-1 score, highest first. Domains not yet content-classified fall
back to relevance_score -- the cheap keyword score computed from the
discovering link's preview text at discovery time (register_pending_domain),
so a fresh, promising domain doesn't get buried under older low-signal ones
purely by recency (owner feedback: "need to go a few pages before to find
something relevant").
"""

from __future__ import annotations

from app.modules.admin.api.routes import _domain_sort_key


def _item(
    domain: str,
    *,
    frontier_status: str = "pending",
    relevance_score: float = 0.0,
    content_relevance: float | None = None,
    last_crawled_at: str | None = None,
) -> dict:
    return {
        "domain": domain,
        "frontier_status": frontier_status,
        "relevance_score": relevance_score,
        "content_relevance": content_relevance,
        "last_crawled_at": last_crawled_at,
    }


def test_unscored_domains_sort_by_relevance_score_descending() -> None:
    """Two domains with no content_relevance yet.

    The higher discovery-time relevance_score ranks first, even if it arrived
    earlier.
    """
    low_but_newer = _item("low.example", relevance_score=1.0, last_crawled_at="2026-08-20T00:00:00")
    high_but_older = _item("high.example", relevance_score=7.0, last_crawled_at="2026-08-01T00:00:00")
    items = [low_but_newer, high_but_older]
    items.sort(key=_domain_sort_key)
    assert [it["domain"] for it in items] == ["high.example", "low.example"]


def test_unscored_domains_tiebreak_by_recency() -> None:
    """Equal relevance_score: newest last_crawled_at wins, same as the old behavior."""
    older = _item("older.example", relevance_score=3.0, last_crawled_at="2026-08-01T00:00:00")
    newer = _item("newer.example", relevance_score=3.0, last_crawled_at="2026-08-20T00:00:00")
    items = [older, newer]
    items.sort(key=_domain_sort_key)
    assert [it["domain"] for it in items] == ["newer.example", "older.example"]


def test_content_classified_domains_sort_by_content_relevance_descending() -> None:
    """Once classify_pending_domains has scored a domain.

    That 0-1 score drives order -- unaffected by this change.
    """
    low = _item("low.example", content_relevance=0.2)
    high = _item("high.example", content_relevance=0.9)
    items = [low, high]
    items.sort(key=_domain_sort_key)
    assert [it["domain"] for it in items] == ["high.example", "low.example"]


def test_unscored_tier_still_sorts_before_content_classified_tier() -> None:
    """A brand-new, not-yet-classified domain still surfaces first.

    It ranks ahead of every already-classified domain (even a highly-relevant
    one) -- preserves the existing "don't bury fresh discoveries" behavior;
    this change only fixes ordering WITHIN the unscored tier.
    """
    unscored = _item("unscored.example", relevance_score=0.5, last_crawled_at="2026-08-01T00:00:00")
    classified_high = _item("classified.example", content_relevance=0.95)
    items = [classified_high, unscored]
    items.sort(key=_domain_sort_key)
    assert [it["domain"] for it in items] == ["unscored.example", "classified.example"]


def test_frontier_status_ordering_preserved() -> None:
    """Pending < dead_end < approved regardless of score, unchanged by this fix."""
    approved = _item("approved.example", frontier_status="approved", relevance_score=9.0)
    dead_end = _item("dead.example", frontier_status="dead_end", relevance_score=9.0)
    pending = _item("pending.example", frontier_status="pending", relevance_score=0.1)
    items = [approved, dead_end, pending]
    items.sort(key=_domain_sort_key)
    assert [it["domain"] for it in items] == ["pending.example", "dead.example", "approved.example"]


def test_missing_relevance_score_treated_as_zero() -> None:
    """A row with no relevance_score at all (None/absent).

    Doesn't crash the sort and ranks below any scored peer.
    """
    missing = {
        "domain": "missing.example",
        "frontier_status": "pending",
        "content_relevance": None,
        "last_crawled_at": None,
    }
    scored = _item("scored.example", relevance_score=1.0)
    items = [missing, scored]
    items.sort(key=_domain_sort_key)
    assert [it["domain"] for it in items] == ["scored.example", "missing.example"]
