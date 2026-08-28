"""W2-A(2/3/4) regressions.

_resolve_artifact's bookkeeping write surviving a soft-time-limit interrupt,
drain_to_compose's single_flight lock, and _publish_standard_row honouring a
full backlog like the review branch already does.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app.modules.newspaper.tasks import queue_drain_tasks as qdt


def test_resolve_artifact_retries_bookkeeping_write_on_soft_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(2) A compose an outcome reflects already ran (paid for).

    If SoftTimeLimitExceeded fires mid-write inside `mark_artifact_status`
    (the same interrupt that will end up aborting the whole drain run), the
    write must still land on retry before the interrupt is allowed to
    propagate. Without this, a compose that already succeeded could be left
    SELECTED/retriable and get composed again by a later run.
    """
    calls: list[tuple[str, str]] = []

    def _flaky_mark(artifact_id: str, status: str) -> None:
        calls.append((artifact_id, status))
        if len(calls) == 1:
            raise SoftTimeLimitExceeded

    monkeypatch.setattr(qdt, "mark_artifact_status", _flaky_mark)
    monkeypatch.setattr(qdt, "is_terminal_outcome", lambda _outcome: True)

    with pytest.raises(SoftTimeLimitExceeded):
        qdt._resolve_artifact("art-1", {"status": "published"})

    # Retried exactly once, and the retry actually wrote the mark -- not a
    # silent no-op.
    assert calls == [("art-1", qdt.COMPOSED), ("art-1", qdt.COMPOSED)]


def test_resolve_artifact_returns_status_on_the_happy_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unchanged normal-path behaviour: no interrupt, one mark call, status returned."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        qdt, "mark_artifact_status", lambda aid, status: calls.append((aid, status))
    )
    monkeypatch.setattr(qdt, "is_terminal_outcome", lambda _outcome: True)

    status = qdt._resolve_artifact("art-2", {"status": "published"})

    assert status == "published"
    assert calls == [("art-2", qdt.COMPOSED)]


def test_drain_to_compose_is_single_flight_locked(monkeypatch: pytest.MonkeyPatch) -> None:
    """(3) A concurrent drain_to_compose invocation must not race the first run.

    It must return `already_running` without touching the drain body at all
    -- a second run would race the same one-fresh-compose-per-run budget /
    concurrently compose the same to_compose slot.
    """
    monkeypatch.setattr("app.core.redis_lock.acquire", lambda _key, _ttl: None)

    def _boom() -> None:
        raise AssertionError("drain body must not run while the lock is held")

    monkeypatch.setattr(qdt, "_drain_to_compose_setup", _boom)

    result = qdt.drain_to_compose()

    assert result == {"status": "already_running", "key": "drain:to_compose"}


def test_drain_to_compose_lock_ttl_covers_the_hard_time_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The lock TTL must be at least the task's soft time limit.

    CLAUDE.md invariant 5 -- pinned here to the hard COMPOSE_TASK_TIME_LIMIT
    so the lock always outlives the run even if the soft interrupt doesn't
    land and celery has to hard-kill the worker.
    """
    seen_ttls: list[int] = []

    def _spy_acquire(_key: str, ttl: int) -> str:
        seen_ttls.append(ttl)
        return "token"

    monkeypatch.setattr("app.core.redis_lock.acquire", _spy_acquire)
    monkeypatch.setattr("app.core.redis_lock.release", lambda _key, _token: None)
    monkeypatch.setattr(
        qdt,
        "_drain_to_compose_setup",
        lambda: (0, {"status": "skipped", "reason": "x", "published": 0}),
    )

    qdt.drain_to_compose()

    assert seen_ttls == [qdt.config.COMPOSE_TASK_TIME_LIMIT]
    assert qdt.config.COMPOSE_TASK_TIME_LIMIT >= qdt.config.COMPOSE_TASK_SOFT_TIME_LIMIT


def test_publish_standard_row_defers_when_backlog_full(monkeypatch: pytest.MonkeyPatch) -> None:
    """(4) A standard-tier slot must defer, not compose, while backlog is full.

    Same reasoning `_process_review_row` already applies to review-bound
    rows: composing a fresh standard row now while a full day of backlog
    releases is already queued is pure cost.
    """

    def _boom(*_a: object, **_kw: object) -> None:
        raise AssertionError("must not compose a fresh standard row while backlog_full")

    monkeypatch.setattr(qdt, "publish_from_queued_row", _boom)

    def _resolve_boom(*_a: object) -> str:
        raise AssertionError("must not resolve a row that was never composed")

    row = SimpleNamespace()  # backlog_full short-circuits before any row access

    result = qdt._publish_standard_row(row, 0, backlog_full=True, resolve=_resolve_boom)

    assert result == (None, 0, None)


def test_publish_standard_row_composes_normally_when_backlog_not_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity counterpart: backlog_full=False must not change existing behaviour."""
    from app.modules.newspaper.publish_policy import PublishKind

    row = SimpleNamespace(publish_kind=PublishKind.CONTENT_UPDATE.value, payload={}, queue_id="q1")
    monkeypatch.setattr(
        qdt,
        "evaluate_standard_publish",
        lambda *_a, **_kw: SimpleNamespace(allowed=False, reason="capped"),
    )

    entry, published_delta, early_stop = qdt._publish_standard_row(
        row, 0, backlog_full=False, resolve=lambda *_a: "published"
    )

    assert entry is None
    assert published_delta == 0
    assert early_stop == {"status": "skipped", "reason": "capped", "published": 0}
