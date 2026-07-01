from datetime import UTC, datetime, timedelta

from app.modules.newspaper import editorial_assignment as ea
from app.modules.newspaper.publish_policy import PublishKind, PublishTopic


def _brief(**overrides) -> ea.EditorialBrief:
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


def test_assign_editorial_brief_forces_relevance_and_enqueues(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", True)
    monkeypatch.setattr(ea, "get_brief", lambda brief_id: _brief(brief_id=brief_id))

    captured_priority_kwargs = {}

    def fake_compute_priority(**kwargs):
        captured_priority_kwargs.update(kwargs)

        class _Breakdown:
            total = 199

        return _Breakdown()

    captured_enqueue_kwargs = {}

    def fake_enqueue_publish(**kwargs):
        captured_enqueue_kwargs.update(kwargs)
        return ("queue-id-1", True)

    monkeypatch.setattr(
        "app.modules.newspaper.publish_score.compute_priority", fake_compute_priority
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.enqueue_publish", fake_enqueue_publish
    )
    drain_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.queue_drain_tasks.drain_standard_publish_queue.delay",
        lambda: drain_calls.append(1),
    )

    result = ea.assign_editorial_brief("00000000-0000-0000-0000-000000000001")

    assert result["status"] == "enqueued"
    assert captured_priority_kwargs["relevance"] == 1.0
    assert captured_priority_kwargs["topic"] == PublishTopic.EDITORIAL_ASSIGNMENT
    assert captured_enqueue_kwargs["publish_kind"] == PublishKind.EDITORIAL_ASSIGNMENT
    assert captured_enqueue_kwargs["topic"] == PublishTopic.EDITORIAL_ASSIGNMENT
    assert captured_enqueue_kwargs["priority"] == 199
    assert captured_enqueue_kwargs["payload"]["publish_mode"] == "create"
    assert captured_enqueue_kwargs["payload"]["source_kind"] == "editorial_assignment"
    assert drain_calls == [1]


def test_assign_editorial_brief_duplicate_does_not_redrain(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", True)
    monkeypatch.setattr(ea, "get_brief", lambda brief_id: _brief(brief_id=brief_id))
    monkeypatch.setattr(
        "app.modules.newspaper.publish_score.compute_priority",
        lambda **kwargs: type("B", (), {"total": 100})(),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.enqueue_publish",
        lambda **kw: ("existing-queue-id", False),
    )
    drain_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.queue_drain_tasks.drain_standard_publish_queue.delay",
        lambda: drain_calls.append(1),
    )

    result = ea.assign_editorial_brief("00000000-0000-0000-0000-000000000001")

    assert result["status"] == "duplicate"
    assert not drain_calls


def test_assign_editorial_brief_disabled_flag(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", False)
    called = []
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.enqueue_publish",
        lambda **kw: called.append(kw),
    )

    result = ea.assign_editorial_brief("00000000-0000-0000-0000-000000000001")

    assert result["status"] == "disabled"
    assert not called


def test_assign_editorial_brief_missing_brief(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", True)
    monkeypatch.setattr(ea, "get_brief", lambda brief_id: None)

    result = ea.assign_editorial_brief("does-not-exist")

    assert result["status"] == "skipped"
    assert result["reason"] == "brief_not_found"


def test_refresh_falls_back_to_assign_without_linked_article(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", True)
    monkeypatch.setattr(ea, "get_brief", lambda brief_id: _brief(linked_article_id=""))
    called_assign = []

    def fake_assign(brief_id):
        called_assign.append(brief_id)
        return {"status": "enqueued"}

    monkeypatch.setattr(ea, "assign_editorial_brief", fake_assign)

    result = ea.refresh_editorial_brief("00000000-0000-0000-0000-000000000001")

    assert called_assign == ["00000000-0000-0000-0000-000000000001"]
    assert result["status"] == "enqueued"


def test_refresh_edits_existing_article_and_bumps_last_run(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.WRITER_EDITORIAL_BRIEFS_ENABLED", True)
    linked_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(ea, "get_brief", lambda brief_id: _brief(linked_article_id=linked_id))

    captured_enqueue_kwargs = {}
    monkeypatch.setattr(
        "app.modules.newspaper.publish_queue_store.enqueue_publish",
        lambda **kw: (captured_enqueue_kwargs.update(kw), ("queue-id-2", True))[1],
    )

    def fake_compute_priority(**kwargs):
        class _Breakdown:
            total = 150

        return _Breakdown()

    monkeypatch.setattr(
        "app.modules.newspaper.publish_score.compute_priority", fake_compute_priority
    )

    mark_run_calls = []
    monkeypatch.setattr(
        ea, "mark_brief_run", lambda **kw: mark_run_calls.append(kw)
    )
    drain_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.tasks.queue_drain_tasks.drain_standard_publish_queue.delay",
        lambda: drain_calls.append(1),
    )

    result = ea.refresh_editorial_brief("00000000-0000-0000-0000-000000000001")

    assert result["status"] == "enqueued"
    assert captured_enqueue_kwargs["payload"]["publish_mode"] == "edit"
    assert captured_enqueue_kwargs["payload"]["linked_article_id"] == linked_id
    assert mark_run_calls == [{"brief_id": "00000000-0000-0000-0000-000000000001"}]
    assert drain_calls == [1]


def test_scan_schedule_assigns_unlinked_and_refreshes_due(monkeypatch) -> None:
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
