"""Assigning and refreshing editorial briefs."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.modules.newspaper import editorial_assignment as ea
from app.modules.newspaper.publish_policy import PublishKind, PublishTopic


def _brief(**overrides: object) -> ea.EditorialBrief:
    defaults = {
        "brief_id": "00000000-0000-0000-0000-000000000001",
        "title": "Algorand wallets compared",
        "body_markdown": "Cover download links, platform support, custody.",
        "keywords": "wallet, algorand",
        "status": "active",
        "refresh_every_days": 0,
        "last_run_at": None,
        "linked_article_id": "",
    }
    defaults.update(overrides)
    return ea.EditorialBrief(**defaults)


def test_get_brief_parses_is_special_edition_true(fake_cassandra_session: MagicMock) -> None:
    """get_brief surfaces is_special_edition=True from the row."""
    row = MagicMock()
    row.brief_id = "00000000-0000-0000-0000-000000000001"
    row.title = "State of Algorand DeFi"
    row.body_markdown = "Angle: quarterly deep dive."
    row.keywords = "defi, algorand"
    row.status = "active"
    row.refresh_every_days = 30
    row.last_run_at = None
    row.linked_article_id = None
    row.is_special_edition = True
    fake_cassandra_session.execute.return_value.one.return_value = row

    brief = ea.get_brief("00000000-0000-0000-0000-000000000001")

    assert brief is not None
    assert brief.is_special_edition is True


def test_get_brief_defaults_is_special_edition_false(fake_cassandra_session: MagicMock) -> None:
    """A null is_special_edition column (pre-migration rows) reads as False, not None."""
    row = MagicMock()
    row.brief_id = "00000000-0000-0000-0000-000000000001"
    row.title = "Algorand wallets compared"
    row.body_markdown = "Cover download links."
    row.keywords = "wallet"
    row.status = "active"
    row.refresh_every_days = 0
    row.last_run_at = None
    row.linked_article_id = None
    row.is_special_edition = None
    fake_cassandra_session.execute.return_value.one.return_value = row

    brief = ea.get_brief("00000000-0000-0000-0000-000000000001")

    assert brief is not None
    assert brief.is_special_edition is False


def test_build_assignment_payload_carries_is_special_edition() -> None:
    """The compose payload threads is_special_edition through for the writer prompt."""
    payload = ea._build_assignment_payload(_brief(is_special_edition=True))
    assert payload["is_special_edition"] is True

    payload = ea._build_assignment_payload(_brief(is_special_edition=False))
    assert payload["is_special_edition"] is False


def test_assign_editorial_brief_forces_relevance_and_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Assigning a brief forces relevance=1.0, enqueues a create, dual-writes an artifact, and triggers an immediate compose of THAT artifact (compose_artifact_now, 2026-08-25 successor to drain_standard_publish_queue.delay())."""
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", True)
    monkeypatch.setattr(ea, "get_brief", lambda brief_id: _brief(brief_id=brief_id))

    captured_priority_kwargs = {}

    def fake_compute_priority(**kwargs: object) -> Any:  # noqa: ANN401 -- test double / fake response
        captured_priority_kwargs.update(kwargs)

        class _Breakdown:
            total = 199

        return _Breakdown()

    captured_enqueue_kwargs = {}

    def fake_enqueue_publish(**kwargs: object) -> tuple[str, bool]:
        captured_enqueue_kwargs.update(kwargs)
        return ("queue-id-1", True)

    monkeypatch.setattr(
        "app.modules.newspaper.publish_score.compute_priority", fake_compute_priority
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.enqueue_publish", fake_enqueue_publish
    )
    captured_artifact_kwargs = {}
    monkeypatch.setattr(
        "app.modules.newspaper.artifact_store.insert_artifact",
        lambda **kw: (captured_artifact_kwargs.update(kw), ("artifact-id-1", True))[1],
    )
    compose_now_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.queue_drain_tasks.compose_artifact_now.delay",
        lambda artifact_id: compose_now_calls.append(artifact_id),
    )

    result = ea.assign_editorial_brief("00000000-0000-0000-0000-000000000001")

    assert result["status"] == "enqueued"
    assert result["artifact_id"] == "artifact-id-1"
    assert captured_priority_kwargs["relevance"] == 1.0
    assert captured_priority_kwargs["topic"] == PublishTopic.EDITORIAL_ASSIGNMENT
    assert captured_enqueue_kwargs["publish_kind"] == PublishKind.EDITORIAL_ASSIGNMENT
    assert captured_enqueue_kwargs["topic"] == PublishTopic.EDITORIAL_ASSIGNMENT
    assert captured_enqueue_kwargs["priority"] == 199
    assert captured_enqueue_kwargs["payload"]["publish_mode"] == "create"
    assert captured_enqueue_kwargs["payload"]["source_kind"] == "editorial_assignment"
    assert captured_artifact_kwargs["channel"] == "brief"
    assert captured_artifact_kwargs["metadata"]["dual_write_queue_id"] == "queue-id-1"
    assert compose_now_calls == ["artifact-id-1"]


def test_assign_editorial_brief_duplicate_does_not_recompose(monkeypatch: pytest.MonkeyPatch) -> None:
    """A duplicate enqueue does not trigger an immediate compose."""
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", True)
    monkeypatch.setattr(ea, "get_brief", lambda brief_id: _brief(brief_id=brief_id))
    monkeypatch.setattr(
        "app.modules.newspaper.publish_score.compute_priority",
        lambda **_kwargs: type("B", (), {"total": 100})(),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.enqueue_publish",
        lambda **_kw: ("existing-queue-id", False),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.artifact_store.insert_artifact",
        lambda **_kw: ("artifact-id-1", True),
    )
    compose_now_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.queue_drain_tasks.compose_artifact_now.delay",
        lambda artifact_id: compose_now_calls.append(artifact_id),
    )

    result = ea.assign_editorial_brief("00000000-0000-0000-0000-000000000001")

    assert result["status"] == "duplicate"
    assert not compose_now_calls


def test_assign_editorial_brief_disabled_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips assignment and never enqueues when editorial briefs are disabled."""
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", False)
    called = []
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.enqueue_publish",
        lambda **kw: called.append(kw),
    )

    result = ea.assign_editorial_brief("00000000-0000-0000-0000-000000000001")

    assert result["status"] == "disabled"
    assert not called


def test_assign_editorial_brief_missing_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips assignment with reason brief_not_found when the brief id doesn't resolve."""
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", True)
    monkeypatch.setattr(ea, "get_brief", lambda _brief_id: None)

    result = ea.assign_editorial_brief("does-not-exist")

    assert result["status"] == "skipped"
    assert result["reason"] == "brief_not_found"


def test_refresh_falls_back_to_assign_without_linked_article(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refreshing a brief with no linked article delegates to assign_editorial_brief."""
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", True)
    monkeypatch.setattr(ea, "get_brief", lambda _brief_id: _brief(linked_article_id=""))
    called_assign = []

    def fake_assign(brief_id: str) -> dict:
        called_assign.append(brief_id)
        return {"status": "enqueued"}

    monkeypatch.setattr(ea, "assign_editorial_brief", fake_assign)

    result = ea.refresh_editorial_brief("00000000-0000-0000-0000-000000000001")

    assert called_assign == ["00000000-0000-0000-0000-000000000001"]
    assert result["status"] == "enqueued"


def test_refresh_edits_existing_article_and_bumps_last_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refreshing a brief with a linked article enqueues an edit, dual-writes an artifact, marks the brief's last run, and triggers an immediate compose of that artifact."""
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", True)
    linked_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(ea, "get_brief", lambda _brief_id: _brief(linked_article_id=linked_id))

    captured_enqueue_kwargs = {}
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.enqueue_publish",
        lambda **kw: (captured_enqueue_kwargs.update(kw), ("queue-id-2", True))[1],
    )

    def fake_compute_priority(**_kwargs: object) -> Any:  # noqa: ANN401 -- test double / fake response
        class _Breakdown:
            total = 150

        return _Breakdown()

    monkeypatch.setattr(
        "app.modules.newspaper.publish_score.compute_priority", fake_compute_priority
    )
    monkeypatch.setattr(
        "app.modules.newspaper.artifact_store.insert_artifact",
        lambda **_kw: ("artifact-id-2", True),
    )

    mark_run_calls = []
    monkeypatch.setattr(ea, "mark_brief_run", lambda **kw: mark_run_calls.append(kw))
    compose_now_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.queue_drain_tasks.compose_artifact_now.delay",
        lambda artifact_id: compose_now_calls.append(artifact_id),
    )

    result = ea.refresh_editorial_brief("00000000-0000-0000-0000-000000000001")

    assert result["status"] == "enqueued"
    assert result["artifact_id"] == "artifact-id-2"
    assert captured_enqueue_kwargs["payload"]["publish_mode"] == "edit"
    assert captured_enqueue_kwargs["payload"]["linked_article_id"] == linked_id
    assert mark_run_calls == [{"brief_id": "00000000-0000-0000-0000-000000000001"}]
    assert compose_now_calls == ["artifact-id-2"]


def test_scan_schedule_assigns_unlinked_and_refreshes_due(monkeypatch: pytest.MonkeyPatch) -> None:
    """The schedule scan assigns unlinked briefs and refreshes only the ones due, skipping the rest."""
    now = datetime.now(tz=UTC)
    unlinked = _brief(brief_id="brief-unlinked", linked_article_id="")
    due = _brief(
        brief_id="brief-due",
        linked_article_id="art-1",
        refresh_every_days=7,
        last_run_at=now - timedelta(days=10),
    )
    not_due = _brief(
        brief_id="brief-not-due",
        linked_article_id="art-2",
        refresh_every_days=7,
        last_run_at=now - timedelta(days=1),
    )
    one_off_with_article = _brief(
        brief_id="brief-one-off",
        linked_article_id="art-3",
        refresh_every_days=0,
    )
    monkeypatch.setattr(
        ea, "list_active_briefs", lambda: [unlinked, due, not_due, one_off_with_article]
    )

    assigned = []
    refreshed = []
    monkeypatch.setattr(ea, "assign_editorial_brief", lambda brief_id: assigned.append(brief_id))
    monkeypatch.setattr(ea, "refresh_editorial_brief", lambda brief_id: refreshed.append(brief_id))

    result = ea.scan_editorial_brief_schedule()

    assert assigned == ["brief-unlinked"]
    assert refreshed == ["brief-due"]
    assert result == {"status": "ok", "assigned": 1, "refreshed": 1}
