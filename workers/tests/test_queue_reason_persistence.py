"""Skip/resolve reasons must persist on the queue row (last_reason) instead of
vanishing with the Celery task return: _resolve threads the outcome reason to
mark_queue_done (terminal) or record_queue_reason (row stays pending), and the
standard drain's gate block does the same for fired gates."""

from types import SimpleNamespace

from app.modules.newspaper.tasks import queue_drain_tasks as qdt


def _row():
    return SimpleNamespace(
        queue_id="11111111-1111-1111-1111-111111111111",
        payload={"page_title": "t", "page_text": "x"},
    )


def test_resolve_terminal_passes_reason_to_mark_done(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        qdt, "mark_queue_done", lambda qid, *, reason="": calls.update(qid=qid, reason=reason)
    )
    monkeypatch.setattr(
        qdt,
        "record_queue_reason",
        lambda *_a: (_ for _ in ()).throw(AssertionError("terminal must not use this")),
    )
    status = qdt._resolve(_row(), {"status": "duplicate", "reason": "too_similar_to_recent"})
    assert status == "duplicate"
    assert calls == {
        "qid": "11111111-1111-1111-1111-111111111111",
        "reason": "too_similar_to_recent",
    }


def test_resolve_terminal_defaults_reason_to_status(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        qdt, "mark_queue_done", lambda qid, *, reason="": calls.update(reason=reason)
    )
    qdt._resolve(_row(), {"status": "published"})
    assert calls["reason"] == "published"


def test_resolve_non_terminal_records_reason_and_stays_pending(monkeypatch):
    calls = {}
    monkeypatch.setattr(
        qdt,
        "mark_queue_done",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("must stay pending")),
    )
    monkeypatch.setattr(
        qdt, "record_queue_reason", lambda qid, reason: calls.update(qid=qid, reason=reason)
    )
    status = qdt._resolve(_row(), {"status": "mistral_failed", "reason": ""})
    assert status == "mistral_failed"
    assert calls["reason"] == "mistral_failed"


def test_resolve_queue_status_retires_row_without_terminal(monkeypatch):
    """An outcome carrying queue_status (content-quality veto -> "expired")
    must retire the row under that status — NOT leave it pending. A pending
    sub-floor row recycles through every drain AND blocks the service's next
    signal via the one-pending-per-service dedupe (prod 2026-07-08: 10 such
    rows made 'Pull Top topic' compose nothing)."""
    calls = {}
    monkeypatch.setattr(
        qdt,
        "mark_queue_done",
        lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("not a terminal outcome")),
    )
    monkeypatch.setattr(
        qdt,
        "record_queue_reason",
        lambda *_a: (_ for _ in ()).throw(AssertionError("must mark status, not just reason")),
    )
    monkeypatch.setattr(
        qdt,
        "mark_queue_status",
        lambda qid, status, *, reason="": calls.update(qid=qid, status=status, reason=reason),
    )
    status = qdt._resolve(
        _row(),
        {"status": "skipped", "reason": "poor_quality_content", "queue_status": "expired"},
    )
    assert status == "skipped"
    assert calls == {
        "qid": "11111111-1111-1111-1111-111111111111",
        "status": "expired",
        "reason": "poor_quality_content",
    }
