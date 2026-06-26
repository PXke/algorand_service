"""Regression: a per-crawl signal must never overwrite the admin's frontier
decision. Previously a thin/off-topic page on an approved domain flipped the
whole domain to dead_end (update_domain_status derived status from is_relevant)
and wiped metadata (erasing the admin's frontier_set_by_admin marker), making
reactivation un-sticky."""

from __future__ import annotations

from app.modules.crawler import domain_tracker


class _Row:
    def __init__(self, *, frontier_status=None, is_relevant=None, metadata=None,
                 category=None, last_online_at=None):
        self.frontier_status = frontier_status
        self.is_relevant = is_relevant
        self.metadata = metadata
        self.category = category
        self.last_online_at = last_online_at


class _Result:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class _FakeSession:
    def __init__(self, existing_row):
        self._existing = existing_row
        self.inserted = None

    def execute(self, query, params=None):
        q = " ".join(query.split())
        if q.startswith("SELECT"):
            return _Result(self._existing)
        if q.startswith("INSERT INTO domain_tracking"):
            self.inserted = params
        return _Result(None)


def _patch(monkeypatch, fake):
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)


# Positions in the INSERT params tuple.
_IS_RELEVANT = 5
_METADATA = 6
_FRONTIER_STATUS = 7


def test_low_relevance_crawl_keeps_approved(monkeypatch):
    fake = _FakeSession(_Row(frontier_status="approved", is_relevant=True))
    _patch(monkeypatch, fake)
    # A thin page on an approved domain (is_relevant False) must NOT dead-end it.
    domain_tracker.update_domain_status("perawallet.app", relevance_score=0.0, is_relevant=False)
    assert fake.inserted[_FRONTIER_STATUS] == "approved"


def test_per_page_crawl_preserves_decision(monkeypatch):
    # The common path now (discovery_store) passes NO is_relevant: the existing
    # decision must be preserved, never reset to the brand-new default.
    fake = _FakeSession(_Row(frontier_status="approved", is_relevant=True))
    _patch(monkeypatch, fake)
    domain_tracker.update_domain_status("perawallet.app", relevance_score=0.0)
    assert fake.inserted[_IS_RELEVANT] is True
    assert fake.inserted[_FRONTIER_STATUS] == "approved"


def test_metadata_is_merged_not_wiped(monkeypatch):
    # An incidental recrawl must keep the admin's permanence marker.
    fake = _FakeSession(_Row(frontier_status="approved", is_relevant=True,
                             metadata={"frontier_set_by_admin": "true"}))
    _patch(monkeypatch, fake)
    domain_tracker.update_domain_status("perawallet.app", relevance_score=1.0)
    assert fake.inserted[_METADATA].get("frontier_set_by_admin") == "true"


def test_pending_is_not_auto_decided_by_crawl(monkeypatch):
    fake = _FakeSession(_Row(frontier_status="pending"))
    _patch(monkeypatch, fake)
    domain_tracker.update_domain_status("maybe.io", relevance_score=0.95, is_relevant=True)
    assert fake.inserted[_FRONTIER_STATUS] == "pending"  # stays held for review


def test_new_domain_defaults_to_pending(monkeypatch):
    fake = _FakeSession(None)
    _patch(monkeypatch, fake)
    domain_tracker.update_domain_status("brandnew.io", relevance_score=0.9, is_relevant=True)
    assert fake.inserted[_FRONTIER_STATUS] == "pending"


def test_explicit_status_still_applies(monkeypatch):
    fake = _FakeSession(_Row(frontier_status="pending"))
    _patch(monkeypatch, fake)
    domain_tracker.update_domain_status(
        "spam.co", relevance_score=0.0, is_relevant=False, frontier_status_override="dead_end"
    )
    assert fake.inserted[_FRONTIER_STATUS] == "dead_end"  # auto-reject decision honoured
    assert fake.inserted[_IS_RELEVANT] is False
