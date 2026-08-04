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


def test_dead_project_suppressed_until_cooldown_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    """A writer-confirmed dead_project abort blocks recrawl until dead_project_until, even with a very recent last_crawled_at (which the generic auto-irrelevant window would otherwise use)."""
    future = (datetime.now(tz=UTC) + timedelta(days=30)).isoformat()
    _patch_status(
        monkeypatch,
        {
            "is_relevant": False,
            "metadata": {"dead_project_until": future},
            "last_crawled_at": datetime.now(tz=UTC),
        },
    )
    assert dt.should_recrawl_domain("kryptonurd.com") is False


def test_dead_project_cooldown_expired_allows_recrawl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Once dead_project_until has passed, the domain is eligible again -- a cooldown, not a permanent reject."""
    past = (datetime.now(tz=UTC) - timedelta(days=1)).isoformat()
    _patch_status(
        monkeypatch,
        {"is_relevant": False, "metadata": {"dead_project_until": past}, "last_crawled_at": None},
    )
    assert dt.should_recrawl_domain("kryptonurd.com") is True


def test_admin_reject_still_wins_over_a_live_dead_project_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If both markers are somehow present, the permanent admin reject takes precedence over the temporary cooldown."""
    future = (datetime.now(tz=UTC) + timedelta(days=30)).isoformat()
    _patch_status(
        monkeypatch,
        {
            "is_relevant": False,
            "metadata": {
                "frontier_set_by_admin": "true",
                "dead_project_until": future,
            },
            "last_crawled_at": None,
        },
    )
    assert dt.should_recrawl_domain("kryptonurd.com") is False


def test_malformed_dead_project_until_falls_through_to_generic_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrupt/unparseable timestamp must not crash the check -- fall through to the ordinary auto-irrelevant window instead."""
    old = datetime.now(tz=UTC) - timedelta(days=999)
    _patch_status(
        monkeypatch,
        {
            "is_relevant": False,
            "metadata": {"dead_project_until": "not-a-real-timestamp"},
            "last_crawled_at": old,
        },
    )
    assert dt.should_recrawl_domain("kryptonurd.com") is True


def test_suppress_dead_project_domain_sets_cooldown_and_preserves_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Suppressing a domain sets is_relevant=False and a future dead_project_until, without resetting its existing relevance_score."""
    monkeypatch.setattr(
        dt, "get_domain_status", lambda _d: {"relevance_score": 11.0, "metadata": {}}
    )
    captured = {}

    def _fake_update(domain: str, **kwargs: object) -> None:
        captured["domain"] = domain
        captured.update(kwargs)

    monkeypatch.setattr(dt, "update_domain_status", _fake_update)

    dt.suppress_dead_project_domain("kryptonurd.com", days=90, reason="dormant since 2022")

    assert captured["domain"] == "kryptonurd.com"
    assert captured["is_relevant"] is False
    assert captured["relevance_score"] == 11.0
    meta = captured["metadata"]
    assert meta["dead_project_reason"] == "dormant since 2022"
    parsed_until = datetime.fromisoformat(meta["dead_project_until"])
    days_out = (parsed_until - datetime.now(tz=UTC)).days
    assert 88 <= days_out <= 90  # ~90 days, allowing for test execution time


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
