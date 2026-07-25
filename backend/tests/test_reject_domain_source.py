"""Rejecting/approving a domain writes the same flags the Domains tab writes."""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore


class _Result:
    def __init__(self, row: Any = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._row = row

    def one(self) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row
        return self._row


class _FakeSession:
    """Mirrors the pattern in workers/tests/test_domain_status_sticky.py: prepare() returns the raw CQL so execute() can branch on query text."""

    def __init__(self, *, existing_row: Any = None) -> None:  # noqa: ANN401 -- duck-typed Cassandra row
        self._existing = existing_row
        self.domain_inserts: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, query: str, params: tuple = ()) -> _Result:
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "domain_tracking" in q:
            return _Result(self._existing)
        if q.startswith("INSERT INTO algorand_platform.domain_tracking"):
            self.domain_inserts.append(tuple(params))
        return _Result(None)


def _patch(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:  # noqa: ANN401 -- duck-typed fake Cassandra session
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def test_reject_domain_source_marks_permanently_irrelevant(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deleting a fabricated article previously never touched domain_tracking at all — the exact source could (and did: GEO World Energy / world.geographia.com.br, 2026-07-14) get re-crawled and re-composed after being deleted once. reject_domain_source must write the same is_relevant=False / frontier_status=dead_end flag the Domains tab's own "Mark Dead End" action writes, since run_publish_pipeline's is_dead_end_domain check reads exactly that flag before any future scrape/compose spend."""
    fake = _FakeSession(existing_row=None)
    _patch(monkeypatch, fake)
    monkeypatch.setattr(
        AdminCassandraStore, "record_classifier_feedback", lambda _self, **_kw: None
    )

    store = AdminCassandraStore()
    store.reject_domain_source(
        domain="world.geographia.com.br",
        wallet="0xADMIN",
        source_url_hint="https://world.geographia.com.br/",
    )

    assert len(fake.domain_inserts) == 1
    params = fake.domain_inserts[0]
    # domain, last_crawled_at, last_online_at, relevance_score, category,
    # is_relevant, metadata, frontier_status
    assert params[0] == "world.geographia.com.br"
    assert params[5] is False
    assert params[6]["frontier_set_by_admin"] == "true"
    assert params[6]["frontier_status"] == "dead_end"
    assert params[7] == "dead_end"


def test_reject_domain_source_preserves_existing_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rejecting a domain keeps its existing category/metadata and pops pending_url into classifier feedback."""
    from types import SimpleNamespace

    existing = SimpleNamespace(
        domain="example.com",
        last_crawled_at=None,
        last_online_at=None,
        relevance_score=4.0,
        category="news",
        is_relevant=True,
        metadata={"preview_title": "Example", "pending_url": "https://example.com/x"},
    )
    fake = _FakeSession(existing_row=existing)
    _patch(monkeypatch, fake)
    fed_back = {}
    monkeypatch.setattr(
        AdminCassandraStore,
        "record_classifier_feedback",
        lambda _self, **kw: fed_back.update(kw),
    )

    store = AdminCassandraStore()
    store.reject_domain_source(domain="example.com", wallet="0xADMIN")

    params = fake.domain_inserts[0]
    assert params[4] == "news"  # category preserved
    # pending_url is popped out of metadata before the write, and used as the
    # classifier-feedback url when no explicit source_url_hint is given.
    assert "pending_url" not in params[6]
    assert fed_back["url"] == "https://example.com/x"
    assert fed_back["quality"] == "spam"
    assert fed_back["approved"] is False


def test_write_domain_relevance_normalizes_www_on_approve(monkeypatch: pytest.MonkeyPatch) -> None:
    """Approving "www.example.com" writes under the crawler's normalized "example.com" key."""
    # Root-caused 2026-07-24 (urvote.ca): an admin approved "www.urvote.ca"
    # (the site's real canonical/redirect host, not a typo) and it was
    # written to domain_tracking verbatim. The crawler's own
    # is_admin_approved_domain check always looks up
    # domain_from_url(page_url), which collapses "www.urvote.ca" ->
    # "urvote.ca" — so the approval never matched and the admin-approved
    # bypass silently never fired. The approve path must write under the
    # SAME normalized key the crawler will look up.
    fake = _FakeSession(existing_row=None)
    _patch(monkeypatch, fake)
    monkeypatch.setattr(
        AdminCassandraStore, "record_classifier_feedback", lambda _self, **_kw: None
    )

    store = AdminCassandraStore()
    store._write_domain_relevance("www.urvote.ca", is_relevant=True)

    assert len(fake.domain_inserts) == 1
    assert fake.domain_inserts[0][0] == "urvote.ca"


def test_write_domain_relevance_does_not_normalize_on_reject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rejecting a subdomain writes it verbatim, without collapsing to its root domain."""
    # The opposite side of the same fix must NOT collapse subdomains when
    # rejecting — see test_reject_domain_source_marks_permanently_irrelevant:
    # dead-ending one bad subdomain must not dead-end its siblings.
    fake = _FakeSession(existing_row=None)
    _patch(monkeypatch, fake)
    monkeypatch.setattr(
        AdminCassandraStore, "record_classifier_feedback", lambda _self, **_kw: None
    )

    store = AdminCassandraStore()
    store._write_domain_relevance("www.urvote.ca", is_relevant=False)

    assert fake.domain_inserts[0][0] == "www.urvote.ca"
