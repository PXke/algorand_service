"""Incident 2026-07-15: one drain run chain-published three articles minutes apart (Subtopia 11:27, Aramid 11:33, Silo 11:37) instead of 8h apart.

The review branch of drain_to_compose (formerly drain_standard_publish_queue)
composes slots the classifier wasn't confident about; fresh-auto-approve
inside publish_from_queued_row can turn such a compose into a direct feed
publish (status "published"). Before the 2026-07-15 fix, that outcome
advanced neither the pacing clock (record_standard_publish) nor the run's
feed budget (`published`), and the review batch limit only counted literal
"review" outcomes — so the loop kept composing and publishing until the
daily cap.

Contract pinned here (unchanged by the 2026-08-25 artifacts/to_compose
cutover -- see queue_drain_tasks.py's module docstring):
- a "published" outcome from the review branch advances the pacing clock and
  spends the one-feed-publish-per-run budget;
- an "approved_backlog" outcome counts toward the compose batch limit;
- "approved_backlog" is a terminal queue outcome (row dequeued, article kept).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from algorand_shared.artifact_store import SELECTED, Artifact, ArtifactContent

from app.modules.newspaper.publish_queue_store import TERMINAL_OUTCOMES
from app.modules.newspaper.tasks import queue_drain_tasks as qdt


def _artifact(artifact_id: str, *, status: str = SELECTED) -> Artifact:
    return Artifact(
        artifact_id=artifact_id,
        service_id=f"svc-{artifact_id}",
        url=f"https://example.com/{artifact_id}",
        channel="crawler",
        created_at=datetime(2026, 8, 25, tzinfo=UTC),
        event_date=None,
        priority=1.0,
        priority_computed_at=None,
        status=status,
    )


def _content(artifact_id: str) -> ArtifactContent:
    return ArtifactContent(
        artifact_id=artifact_id,
        title="t",
        content="text",
        metadata={
            "payload": {
                "page_text": "text",
                "signals": None,
                "publish_kind": "content_update",
                "topic": "generic",
            }
        },
    )


def _slate(*artifact_ids: str) -> list[dict[str, object]]:
    return [
        {
            "slot": i,
            "artifact_id": aid,
            "lane": "platform",
            "service_id": f"svc-{aid}",
            "picked_at": None,
        }
        for i, aid in enumerate(artifact_ids)
    ]


@pytest.fixture
def drain_env(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Neutralize everything around the review-branch accounting under test."""
    monkeypatch.setattr(qdt, "_pending_feed_backlog_full", lambda: False)
    monkeypatch.setattr(qdt, "remaining_standard_publish_slots", lambda: 3)
    monkeypatch.setattr(qdt, "_release_due_backlog", lambda _slots: None)
    monkeypatch.setattr(qdt, "_ensure_today_selected", lambda _day: None)
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.review_queue_full",
        lambda: False,
    )
    monkeypatch.setattr(qdt, "_run_pre_compose_gates", lambda _row: None)
    monkeypatch.setattr(qdt, "_row_needs_review", lambda _row: True)
    monkeypatch.setattr(qdt, "get_artifact", lambda aid: _artifact(aid))
    monkeypatch.setattr(qdt, "get_artifact_content", lambda aid: _content(aid))
    monkeypatch.setattr(qdt, "mark_artifact_status", lambda *_a, **_k: None)

    recorded: list[str] = []
    monkeypatch.setattr(qdt, "record_standard_publish", lambda **_kw: recorded.append("tick"))
    return recorded


def test_auto_published_review_outcome_advances_clock_and_spends_budget(
    monkeypatch: pytest.MonkeyPatch, drain_env: list[str]
) -> None:
    """A review-branch auto-publish advances the pacing clock and blocks a second direct publish in the same run."""
    monkeypatch.setattr(qdt, "list_to_compose_for_day", lambda _day: _slate("r1", "r2"))
    # r2 is a direct-publish slot; it must never be composed once r1's
    # auto-publish spent the run's feed budget.
    monkeypatch.setattr(qdt, "_row_needs_review", lambda row: row.queue_id == "r1")
    composed: list[str] = []

    def compose(row: Any, **_kw: object) -> dict:  # noqa: ANN401 -- duck-typed Cassandra row/result
        composed.append(row.queue_id)
        return {"status": "published", "article_id": "a-1"}

    monkeypatch.setattr(qdt, "publish_from_queued_row", compose)

    result = qdt.drain_to_compose()

    assert composed == ["r1"]
    assert drain_env == ["tick"]  # pacing clock advanced exactly once
    assert result["published"] == 1


def test_approved_backlog_outcome_counts_toward_compose_batch_limit(
    monkeypatch: pytest.MonkeyPatch, drain_env: list[str]
) -> None:
    """An approved_backlog outcome spends the compose batch budget without touching the pacing clock."""
    monkeypatch.setattr(qdt, "list_to_compose_for_day", lambda _day: _slate("r1", "r2"))
    monkeypatch.setattr(qdt.config, "REVIEW_COMPOSE_BATCH_LIMIT", 1, raising=False)
    composed: list[str] = []

    def compose(row: Any, **_kw: object) -> dict:  # noqa: ANN401 -- duck-typed Cassandra row/result
        composed.append(row.queue_id)
        return {"status": "approved_backlog", "article_id": "a-1"}

    monkeypatch.setattr(qdt, "publish_from_queued_row", compose)

    qdt.drain_to_compose()

    # One compose spent the batch budget; the second row waits for a later run.
    assert composed == ["r1"]
    assert drain_env == []  # nothing hit the feed — clock untouched


def test_approved_backlog_is_terminal() -> None:
    """Confirms approved_backlog is registered as a terminal queue outcome."""
    assert "approved_backlog" in TERMINAL_OUTCOMES


def test_edited_is_terminal() -> None:
    """Regression pin (2026-07-17): run_article_edit's success outcome ({"status": "edited", ...}) was missing from TERMINAL_OUTCOMES, so a completed edit never resolved its queue row — the row stayed "pending" and drain_breaking_publish_queue (fires every ~2 min; retired 2026-08-25 along with the rest of the BREAKING tier) redrained and re-edited the same live article every beat, forever. 165 edits / 330 versions on one article in under 4 hours before this was caught by hand."""
    assert "edited" in TERMINAL_OUTCOMES


def test_edit_failure_is_terminal() -> None:
    """run_article_edit's failure outcome ({"reason": "update_failed"}) is only reachable when update_article() returns False, which is ONLY a permanent condition (linked article deleted, malformed id, never published) — a real Cassandra write error raises instead. Same missing-terminal-status shape as "edited"; closed alongside it."""
    assert "failed" in TERMINAL_OUTCOMES


def test_full_backlog_stops_review_composes(
    monkeypatch: pytest.MonkeyPatch, drain_env: list[str]
) -> None:
    """2026-07-16: auto-approve → backlog bypassed the 1-slot review throttle, so hourly drains composed six articles overnight — two days of publish inventory at 3/day. With PENDING_FEED_MAX_DEPTH articles already queued, review-bound slots must stay SELECTED, uncomposed."""
    monkeypatch.setattr(qdt, "_pending_feed_backlog_full", lambda: True)
    monkeypatch.setattr(qdt, "list_to_compose_for_day", lambda _day: _slate("r1", "r2"))
    monkeypatch.setattr(
        qdt,
        "publish_from_queued_row",
        lambda *_a, **_k: pytest.fail("must not compose while the backlog is full"),
    )

    result = qdt.drain_to_compose()

    assert result["published"] == 0
    assert drain_env == []  # clock untouched — nothing happened


def test_capped_compose_is_stashed_to_backlog_not_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-07-15: a finished ok compose (the 'Seven Real-World Apps' YouTube article) hit 'standard daily publish cap reached (3/3)' AFTER composing, got returned as rate_limited, and the content was thrown away (its queue row later aged out). The stash helper must store the article unlisted and queue it for the paced backlog release instead."""
    from app.modules.newspaper.publish_policy import PublishKind, PublishTopic
    from app.modules.newspaper.tasks import publish_tasks as pt

    stored: dict = {}
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.insert_stored_article",
        lambda **kw: (stored.update(kw), ("aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000", True))[1],
    )
    executed: list = []

    class _FakeSession:
        def execute(self, stmt: str, params: tuple | None = None) -> None:
            executed.append((stmt, params))

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)

    from types import SimpleNamespace

    row = SimpleNamespace(
        queue_id="q1",
        service_id="youtube-algorand-foundation",
        scrape_url="https://www.youtube.com/watch?v=3hiqzTfcdF4",
        payload={"txid": "", "round_num": 0},
    )
    composed = SimpleNamespace(
        title="Seven Real-World Apps",
        summary="s",
        body="body text",
        publish_kind=None,
        extra_tags=(),
        prompt_version="2026-07-16a",
    )
    out = pt._stash_capped_compose_to_backlog(
        row=row,
        composed=composed,
        payload=row.payload,
        hero_image="",
        image_field="",
        publish_kind=PublishKind.CONTENT_UPDATE,
        topic=PublishTopic.GENERIC,
        reason="standard daily publish cap reached (3/3)",
    )

    assert out["status"] == "approved_backlog"
    assert stored["publish_to_feed"] is False
    assert stored["title"] == "Seven Real-World Apps"
    assert stored["status"] == "backlog"
    assert stored["interest_score"] == 0.0
    assert stored["approved_at"] is not None
    # No separate pending_feed_queue INSERT anymore (Phase 5 dropped the
    # table) -- status='backlog'/interest_score/approved_at on the
    # insert_stored_article call above (mocked here) IS the queue entry now.
    assert executed == []
