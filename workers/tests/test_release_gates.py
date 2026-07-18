"""Release-time re-gating: pending_feed_queue articles are corrected by the
current body-only self-healing gates at the moment of release, closing the
time capsule where gates added after an article's compose never saw it
(UNDP/Stellar and quantum-rebrand incidents, week of 2026-07-14)."""

from __future__ import annotations

from types import SimpleNamespace

from app.modules.newspaper import release_gates
from app.modules.newspaper.article_store import ArticleDetail

INCIDENT_SENTENCE = (
    "While the Foundation has not disclosed specific benchmarks, "
    "industry-wide research suggests that Falcon signatures can be 10-100x "
    "slower to verify than classical ECC signatures."
)


def _detail(body: str) -> ArticleDetail:
    return ArticleDetail(
        article_id="00000000-0000-0000-0000-000000000001",
        service_id="svc",
        title="T",
        summary="S",
        body=body,
        published_at_epoch=1_700_000_000,
        trigger_txid="",
        trigger_round=0,
        source_url="https://example.com/",
    )


def test_dirty_body_is_corrected_and_persisted(monkeypatch):
    body = "Clean sentence. " + INCIDENT_SENTENCE + " Another clean one."
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article", lambda _aid: _detail(body)
    )
    versions: list[str] = []
    monkeypatch.setattr(
        "app.modules.newspaper.article_version_store.save_article_version",
        lambda **kw: versions.append(kw["edit_reason"]) or 1,
    )
    executed: list = []

    class _FakeSession:
        def execute(self, stmt, params=None):
            # First-ever import of index_tasks pulls in celery_app, whose
            # beat-schedule build reads crawler config at import time — only
            # the article UPDATE is the write under test.
            if "UPDATE algorand_platform.articles_by_id" in str(stmt):
                executed.append(params)
            return SimpleNamespace(one=lambda: None)

    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: _FakeSession()
    )
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    indexed: list = []
    monkeypatch.setattr(
        "app.modules.search.tasks.index_tasks.index_article",
        SimpleNamespace(delay=lambda **kw: indexed.append(kw)),
    )

    result = release_gates.apply_release_gates("00000000-0000-0000-0000-000000000001")

    assert result["changed"] is True
    assert result["notes"]["_authority_removed"] == [INCIDENT_SENTENCE]
    # before/after versions saved, raw UPDATE executed, search reindexed.
    assert versions == ["before_edit", "release_gate:_authority_removed"]
    assert len(executed) == 1
    new_body = executed[0][2]
    assert "industry-wide research" not in new_body
    assert "Clean sentence." in new_body and "Another clean one." in new_body
    assert indexed and "industry-wide research" not in indexed[0]["body"]


def test_clean_body_untouched_no_writes(monkeypatch):
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _aid: _detail("Perfectly grounded prose with real sources."),
    )
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: (_ for _ in ()).throw(AssertionError("must not touch Cassandra")),
    )
    result = release_gates.apply_release_gates("00000000-0000-0000-0000-000000000001")
    assert result == {"changed": False, "notes": {}}


def test_fail_open_on_gate_crash(monkeypatch):
    """An already-approved article is never blocked by a gate crash."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _aid: (_ for _ in ()).throw(RuntimeError("cassandra down")),
    )
    result = release_gates.apply_release_gates("00000000-0000-0000-0000-000000000001")
    assert result == {"changed": False, "notes": {}}


def test_release_drain_invokes_gates_before_feed_insert(monkeypatch):
    """Wiring pin: _release_pending_feed_backlog must call apply_release_gates
    for each released article before inserting its feed row."""
    from app.modules.newspaper.tasks import queue_drain_tasks as qdt

    calls: list[str] = []
    monkeypatch.setattr(
        "app.modules.newspaper.release_gates.apply_release_gates",
        lambda aid: calls.append(("gate", aid)) or {"changed": False, "notes": {}},
    )
    order: list = []

    pending_row = SimpleNamespace(
        article_id="00000000-0000-0000-0000-000000000001",
        bucket="main",
        interest_score=50,
        approved_at=None,
    )
    feed_art = SimpleNamespace(
        article_id=pending_row.article_id,
        service_id="svc",
        title="T",
        summary="S",
        tags=["algorand"],
        image_url=None,
        source_url="https://example.com/",
        published_at=None,
    )

    class _FakeSession:
        def execute(self, stmt, params=None):
            text = str(stmt)
            if "pending_feed_queue" in text and "SELECT" in text.upper():
                order.append("peek")
                return [pending_row]
            if "articles_by_id" in text:
                order.append("get_for_feed")
                return SimpleNamespace(one=lambda: feed_art)
            if "articles_feed" in text and "INSERT" in text.upper():
                order.append("feed_insert")
                # gate must have run before the article became visible
                assert calls, "apply_release_gates must run before feed INSERT"
                return SimpleNamespace(one=lambda: None)
            order.append("other")
            return SimpleNamespace(one=lambda: None)

    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: _FakeSession()
    )
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(qdt, "record_standard_publish", lambda **_kw: None)
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.publish_tasks.enqueue_article_translations",
        lambda _aid: None,
    )

    result = qdt._release_pending_feed_backlog(slots=1)
    assert result["published"] == 1
    assert calls == [("gate", pending_row.article_id)]
    assert "feed_insert" in order
