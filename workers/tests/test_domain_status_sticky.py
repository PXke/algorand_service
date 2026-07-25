"""Regression: a per-crawl signal must never overwrite the admin's frontier decision. Previously a thin/off-topic page on an approved domain flipped the whole domain to dead_end (update_domain_status derived status from is_relevant) and wiped metadata (erasing the admin's frontier_set_by_admin marker), making reactivation un-sticky."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.crawler import domain_tracker
from tests.conftest import FakeCassandraResult


class _Row:
    def __init__(
        self,
        *,
        frontier_status: str | None = None,
        is_relevant: bool | None = None,
        metadata: dict | None = None,
        category: str | None = None,
        last_online_at: Any = None,  # noqa: ANN401 -- duck-typed Cassandra row/result
    ) -> None:
        self.frontier_status = frontier_status
        self.is_relevant = is_relevant
        self.metadata = metadata
        self.category = category
        self.last_online_at = last_online_at


class _FakeSession:
    def __init__(self, existing_row: Any) -> None:  # noqa: ANN401 -- duck-typed Cassandra row/result
        self._existing = existing_row
        self.inserted = None

    # The statement registry resolves DomainTrackingStmts.* by calling
    # get_cassandra_session().prepare(cql); return the CQL text so execute() can
    # still branch on it (SELECT vs INSERT).
    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple | None = None) -> FakeCassandraResult:
        q = " ".join(str(query).split())
        if q.startswith("SELECT"):
            return FakeCassandraResult(self._existing)
        if q.startswith("INSERT INTO") and "domain_tracking" in q:
            self.inserted = params
        return FakeCassandraResult(None)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:  # noqa: ANN401 -- test double / fake response
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    # The registry caches prepared statements by CQL via prepare_cached's
    # lru_cache; clear it so each test's fake session is the one that "prepares".
    c.prepare_cached.cache_clear()


# Positions in the INSERT params tuple.
_IS_RELEVANT = 5
_METADATA = 6
_FRONTIER_STATUS = 7


def test_low_relevance_crawl_keeps_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps an approved domain approved even when a crawled page scores irrelevant."""
    fake = _FakeSession(_Row(frontier_status="approved", is_relevant=True))
    _patch(monkeypatch, fake)
    # A thin page on an approved domain (is_relevant False) must NOT dead-end it.
    domain_tracker.update_domain_status("perawallet.app", relevance_score=0.0, is_relevant=False)
    assert fake.inserted[_FRONTIER_STATUS] == "approved"


def test_per_page_crawl_preserves_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserves the existing frontier decision and relevance flag when a per-page crawl omits is_relevant."""
    # The common path now (discovery_store) passes NO is_relevant: the existing
    # decision must be preserved, never reset to the brand-new default.
    fake = _FakeSession(_Row(frontier_status="approved", is_relevant=True))
    _patch(monkeypatch, fake)
    domain_tracker.update_domain_status("perawallet.app", relevance_score=0.0)
    assert fake.inserted[_IS_RELEVANT] is True
    assert fake.inserted[_FRONTIER_STATUS] == "approved"


def test_metadata_is_merged_not_wiped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Merges new metadata into the existing row instead of wiping the admin's frontier_set_by_admin marker."""
    # An incidental recrawl must keep the admin's permanence marker.
    fake = _FakeSession(
        _Row(
            frontier_status="approved", is_relevant=True, metadata={"frontier_set_by_admin": "true"}
        )
    )
    _patch(monkeypatch, fake)
    domain_tracker.update_domain_status("perawallet.app", relevance_score=1.0)
    assert fake.inserted[_METADATA].get("frontier_set_by_admin") == "true"


def test_pending_is_not_auto_decided_by_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leaves a pending domain pending even when a crawl finds it highly relevant."""
    fake = _FakeSession(_Row(frontier_status="pending"))
    _patch(monkeypatch, fake)
    domain_tracker.update_domain_status("maybe.io", relevance_score=0.95, is_relevant=True)
    assert fake.inserted[_FRONTIER_STATUS] == "pending"  # stays held for review


def test_new_domain_defaults_to_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defaults a never-before-seen domain to pending regardless of relevance."""
    fake = _FakeSession(None)
    _patch(monkeypatch, fake)
    domain_tracker.update_domain_status("brandnew.io", relevance_score=0.9, is_relevant=True)
    assert fake.inserted[_FRONTIER_STATUS] == "pending"


def test_explicit_status_still_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Honors an explicit frontier_status_override even though pending status is normally sticky."""
    fake = _FakeSession(_Row(frontier_status="pending"))
    _patch(monkeypatch, fake)
    domain_tracker.update_domain_status(
        "spam.co", relevance_score=0.0, is_relevant=False, frontier_status_override="dead_end"
    )
    assert fake.inserted[_FRONTIER_STATUS] == "dead_end"  # auto-reject decision honoured
    assert fake.inserted[_IS_RELEVANT] is False
