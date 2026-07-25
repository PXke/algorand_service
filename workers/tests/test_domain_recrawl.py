"""should_recrawl_domain: admin rejects are permanent; rejected-while-pending domains must not be crawled (the bug where they kept getting crawled)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.modules.crawler import domain_tracker as dt


def _patch_status(monkeypatch: pytest.MonkeyPatch, status: dict[str, Any] | None) -> None:
    monkeypatch.setattr(dt, "get_domain_status", lambda _domain: status)


def test_unknown_domain_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allows recrawl for a domain with no stored status at all."""
    _patch_status(monkeypatch, None)
    assert dt.should_recrawl_domain("x.com") is True


def test_relevant_domain_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Allows recrawl for a domain already marked relevant."""
    _patch_status(monkeypatch, {"is_relevant": True})
    assert dt.should_recrawl_domain("x.com") is True


def test_admin_reject_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    """An admin-set dead-end rejection blocks recrawl permanently, regardless of last_crawled_at."""
    _patch_status(
        monkeypatch,
        {
            "is_relevant": False,
            "metadata": {"frontier_set_by_admin": "true", "frontier_status": "dead_end"},
            "last_crawled_at": None,
        },
    )
    assert dt.should_recrawl_domain("realtor.com") is False


def test_rejected_while_pending_not_crawled(monkeypatch: pytest.MonkeyPatch) -> None:
    """A domain rejected as irrelevant before ever being crawled is not crawled (regression pin)."""
    # is_relevant False, never crawled (last is None) — previously returned True.
    _patch_status(monkeypatch, {"is_relevant": False, "metadata": {}, "last_crawled_at": None})
    assert dt.should_recrawl_domain("spam.com") is False


def test_auto_irrelevant_rechecks_after_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """An auto-marked-irrelevant domain is eligible for recrawl only after the recheck window elapses."""
    from app.core.config import FRONTIER_RECRAWL_DAYS_IRRELEVANT

    old = datetime.now(tz=UTC) - timedelta(days=FRONTIER_RECRAWL_DAYS_IRRELEVANT + 1)
    _patch_status(monkeypatch, {"is_relevant": False, "metadata": {}, "last_crawled_at": old})
    assert dt.should_recrawl_domain("maybe.com") is True
    recent = datetime.now(tz=UTC) - timedelta(hours=1)
    _patch_status(monkeypatch, {"is_relevant": False, "metadata": {}, "last_crawled_at": recent})
    assert dt.should_recrawl_domain("maybe.com") is False


# --- evaluate_frontier_link: one status read decides gate + state -----------


def test_blocklisted_is_dead_end_without_db_read(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hard-blocklisted domain resolves dead_end without ever reading domain status from the DB."""
    calls = {"n": 0}

    def _counting_status(_domain: str) -> None:
        calls["n"] += 1
        return

    monkeypatch.setattr(dt, "get_domain_status", _counting_status)
    # x.com is in the hard blocklist → dead end, and we never touch the DB.
    assert dt.evaluate_frontier_link("x.com") == ("dead_end", True)
    assert calls["n"] == 0


def test_evaluate_reads_status_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads domain status exactly once, not the two separate reads the old implementation did."""
    calls = {"n": 0}

    def _counting_status(_domain: str) -> dict:
        calls["n"] += 1
        return {"is_relevant": True, "frontier_status": "approved", "metadata": {}}

    monkeypatch.setattr(dt, "get_domain_status", _counting_status)
    assert dt.evaluate_frontier_link("algorand.co") == ("approved", False)
    assert calls["n"] == 1  # not the two reads the old is_dead_end + frontier_status did


def test_evaluate_states(monkeypatch: pytest.MonkeyPatch) -> None:
    """Maps unknown/irrelevant/pending domain statuses to their (state, is_dead_end) pair."""
    _patch_status(monkeypatch, None)
    assert dt.evaluate_frontier_link("new.io") == ("unknown", False)
    _patch_status(monkeypatch, {"is_relevant": False, "metadata": {}})
    assert dt.evaluate_frontier_link("off.io") == ("dead_end", True)
    _patch_status(monkeypatch, {"is_relevant": True, "frontier_status": "pending", "metadata": {}})
    assert dt.evaluate_frontier_link("hold.io") == ("pending", False)


def test_protected_excludes_english_false_positives() -> None:
    """Recognizes real Algo-prefixed domains as protected while excluding unrelated English words like "algorithm"."""
    assert dt.is_protected_domain("algofi.org") is True
    assert dt.is_protected_domain("myalgorand.news") is True
    assert dt.is_protected_domain("algorithm.io") is False
    assert dt.is_protected_domain("algospeak.com") is False
