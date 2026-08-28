"""Release-time re-gating: pending_feed_queue articles are corrected by the current body-only self-healing gates at the moment of release, closing the time capsule where gates added after an article's compose never saw it (UNDP/Stellar and quantum-rebrand incidents, week of 2026-07-14)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

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


def test_dirty_body_is_corrected_and_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strips an unsupported-authority sentence, persists before/after versions, updates Cassandra and reindexes."""
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
    article_row = SimpleNamespace(
        status="published",
        year=2026,
        published_at=None,
        title="T",
        summary="S",
        image_url="https://example.com/hero.png",
    )

    class _FakeSession:
        def execute(self, stmt: str, params: tuple | None = None) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
            # First-ever import of index_tasks pulls in celery_app, whose
            # beat-schedule build reads crawler config at import time.
            text = str(stmt)
            if (
                "SELECT" in text.upper()
                and "FROM algorand_platform.articles WHERE article_id = ?" in text
            ):
                return SimpleNamespace(one=lambda: article_row)
            # The `articles` content UPDATE is the write under test.
            if text.startswith("UPDATE algorand_platform.articles SET title"):
                executed.append(params)
            return SimpleNamespace(one=lambda: None)

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    indexed: list = []
    monkeypatch.setattr(
        "app.modules.search.tasks.index_tasks.index_article",
        SimpleNamespace(delay=lambda **kw: indexed.append(kw)),
    )

    result = release_gates.apply_release_gates("00000000-0000-0000-0000-000000000001")

    assert result["changed"] is True
    assert result["notes"]["_authority_removed"] == [INCIDENT_SENTENCE]
    # before/after versions saved, `articles` content UPDATE executed, search reindexed.
    assert versions == ["before_edit", "release_gate:_authority_removed"]
    assert len(executed) == 1
    new_body = executed[0][2]
    assert "industry-wide research" not in new_body
    assert "Clean sentence." in new_body
    assert "Another clean one." in new_body
    assert indexed
    assert "industry-wide research" not in indexed[0]["body"]


def test_clean_body_untouched_no_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leaves a clean body untouched and never opens a Cassandra session."""
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


def test_fail_open_on_gate_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    """An already-approved article is never blocked by a gate crash."""
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.get_article",
        lambda _aid: (_ for _ in ()).throw(RuntimeError("cassandra down")),
    )
    result = release_gates.apply_release_gates("00000000-0000-0000-0000-000000000001")
    assert result == {"changed": False, "notes": {}}


def test_release_drain_invokes_gates_before_feed_insert(monkeypatch: pytest.MonkeyPatch) -> None:
    """Wiring pin: _release_pending_feed_backlog must call apply_release_gates for each released article before inserting its feed row."""
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
        status="backlog",
        year=2026,
        article_id=pending_row.article_id,
        service_id="svc",
        title="T",
        summary="S",
        body="",
        tags=["algorand"],
        image_url=None,
        source_url="https://example.com/",
        trigger_txid="",
        trigger_round=0,
        slug=None,
        translations=None,
        translated_titles=None,
        first_published_at=None,
        updated_at=None,
        prompt_version=None,
        composed_by_model=None,
        deleted_at=None,
        status_updated_at=None,
        interest_score=pending_row.interest_score,
        approved_at=pending_row.approved_at,
        published_at=None,
        views=None,
    )

    backlog_row = SimpleNamespace(
        article_id=pending_row.article_id,
        service_id="svc",
        title="T",
        interest_score=pending_row.interest_score,
        approved_at=pending_row.approved_at,
    )

    class _FakeSession:
        def execute(self, stmt: str, _params: tuple | None = None) -> Any:  # noqa: ANN401 -- duck-typed Cassandra row/result
            text = str(stmt)
            if "status = 'backlog'" in text:
                order.append("peek")
                return [backlog_row]
            if "pending_feed_queue" in text and "SELECT" in text.upper():
                return [pending_row]
            if (
                "SELECT" in text.upper()
                and "FROM algorand_platform.articles WHERE article_id = ?" in text
            ):
                order.append("get_article_row")
                return SimpleNamespace(one=lambda: feed_art)
            if text.startswith("INSERT INTO algorand_platform.articles ("):
                order.append("articles_insert")
                # gate must have run before the article became visible
                assert calls, "apply_release_gates must run before the articles insert"
                return SimpleNamespace(one=lambda: None)
            order.append("other")
            return SimpleNamespace(one=lambda: None)

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr(qdt, "record_standard_publish", lambda **_kw: None)
    monkeypatch.setattr(
        "app.modules.newspaper.publish_daily_guard.reserve_publish_slot",
        lambda **_kw: (True, "ok"),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.publish_tasks.enqueue_article_translations",
        lambda _aid: None,
    )

    result = qdt._release_pending_feed_backlog(slots=1)
    assert result["published"] == 1
    assert calls == [("gate", pending_row.article_id)]
    assert "articles_insert" in order
