"""The breaking drain's per-row vetoes are a uniform ordered list (_BREAKING_VETOES) like the standard drain's _PRE_COMPOSE_GATES and the compose-side _PRE_COMPOSE_VETOES. Pins order, outcome dicts, and the credibility assessment being stashed on the ctx for the success path."""

from types import SimpleNamespace

import pytest

from app.modules.newspaper.breaking_credibility import BreakingAssessment
from app.modules.newspaper.tasks import queue_drain_tasks as qdt


def _ctx(review_full: bool = False, payload: dict | None = None) -> qdt._BreakingVetoCtx:
    row = SimpleNamespace(
        queue_id="q1",
        publish_kind="content_update",
        topic="scam_alert",
        service_id="svc",
        scrape_url="https://example.com",
        payload=payload
        or {
            "diff": "+++ a\n+ x\n+ y\n+ z\n",
            "source_kind": "web",
            "page_text": "scam alert: phishing at https://evil.example — do not interact",
        },
    )
    return qdt._BreakingVetoCtx(row=row, review_full=review_full)


def test_veto_order() -> None:
    """Pins the breaking veto list's exact order (policy, review-slot, credibility)."""
    # Exactly these three — the absence of domain/service cooldown vetoes is
    # deliberate (owner decision, re-confirmed 2026-07-17): breaking must
    # never wait behind a cooldown stamped by routine coverage of the source.
    assert (
        qdt._breaking_policy_veto,
        qdt._breaking_review_slot_veto,
        qdt._breaking_credibility_veto,
    ) == qdt._BREAKING_VETOES


def test_policy_veto_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reports a skipped outcome with the policy engine's reason when breaking publish isn't allowed."""
    monkeypatch.setattr(
        qdt,
        "evaluate_breaking_publish",
        lambda *_a, **_kw: SimpleNamespace(allowed=False, reason="breaking_daily_cap_reached"),
    )
    assert qdt._breaking_policy_veto(_ctx()) == {
        "status": "skipped",
        "reason": "breaking_daily_cap_reached",
    }


def test_review_slot_veto_only_when_full_and_review_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """Vetoes only when the review queue is full AND the row needs review; otherwise passes."""
    monkeypatch.setattr(qdt, "_row_needs_review", lambda _r: True)
    assert qdt._breaking_review_slot_veto(_ctx(review_full=True)) == {
        "status": "skipped",
        "reason": "review_queue_full",
    }
    assert qdt._breaking_review_slot_veto(_ctx(review_full=False)) is None
    monkeypatch.setattr(qdt, "_row_needs_review", lambda _r: False)
    assert qdt._breaking_review_slot_veto(_ctx(review_full=True)) is None


def test_credibility_veto_outcome_and_stashes_assessment(monkeypatch: pytest.MonkeyPatch) -> None:
    """A not-credible assessment vetoes with queue_status=expired and stashes the assessment on ctx."""
    monkeypatch.setattr(
        qdt,
        "assess_breaking_credibility",
        lambda **_kw: BreakingAssessment(credible=False, reason="no_evidence", method="heuristic"),
    )
    ctx = _ctx()
    outcome = qdt._breaking_credibility_veto(ctx)
    # queue_status "expired" retires the row: the heuristic runs on the row's
    # STATIC page_text, so a not-credible verdict can never change on a later
    # beat — before this, the row stayed pending, was re-assessed every
    # ~2-minute breaking beat forever, and starved the service's one
    # pending-row slot (observed: hay-app row stuck 7 days, audit 2026-07-17).
    assert outcome == {
        "status": "skipped",
        "reason": "not_credible:no_evidence",
        "method": "heuristic",
        "queue_status": "expired",
    }
    assert ctx.assessment is not None
    assert ctx.assessment.method == "heuristic"


def test_drain_retires_not_credible_row_but_leaves_transient_veto_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The drain honors a veto's queue_status: credibility retires the row (mark_queue_status), while transient vetoes (cap/review-slot) only record a reason and leave the row pending for the next beat."""
    row = _ctx().row
    monkeypatch.setattr(qdt, "remaining_breaking_publish_slots", lambda: 3)
    monkeypatch.setattr(qdt, "_pending_for_tier", lambda _tier, limit: [row])  # noqa: ARG005 -- name must match the real callee's keyword arg
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.review_queue_full",
        lambda: False,
    )
    monkeypatch.setattr(
        qdt,
        "evaluate_breaking_publish",
        lambda *_a, **_kw: SimpleNamespace(allowed=True, reason="ok"),
    )
    monkeypatch.setattr(qdt, "_row_needs_review", lambda _r: False)
    monkeypatch.setattr(
        qdt,
        "assess_breaking_credibility",
        lambda **_kw: BreakingAssessment(credible=False, reason="no_evidence", method="heuristic"),
    )
    marked: list = []
    monkeypatch.setattr(
        qdt,
        "mark_queue_status",
        lambda qid, status, reason="": marked.append((qid, status, reason)),
    )
    monkeypatch.setattr(
        qdt,
        "publish_from_queued_row",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("must not compose")),
    )

    qdt.drain_breaking_publish_queue()
    assert marked == [("q1", "expired", "not_credible:no_evidence")]

    # Transient veto (daily cap): row must stay pending — reason only.
    marked.clear()
    recorded: list = []
    monkeypatch.setattr(
        qdt, "record_queue_reason", lambda qid, reason: recorded.append((qid, reason))
    )
    monkeypatch.setattr(
        qdt,
        "evaluate_breaking_publish",
        lambda *_a, **_kw: SimpleNamespace(allowed=False, reason="breaking_daily_cap_reached"),
    )
    qdt.drain_breaking_publish_queue()
    assert marked == []
    assert recorded == [("q1", "breaking_daily_cap_reached")]


def test_credible_row_passes_with_assessment_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """A credible row passes all vetoes (returns None) and leaves the assessment on ctx."""
    monkeypatch.setattr(
        qdt,
        "evaluate_breaking_publish",
        lambda *_a, **_kw: SimpleNamespace(allowed=True, reason="ok"),
    )
    monkeypatch.setattr(qdt, "_row_needs_review", lambda _r: False)
    monkeypatch.setattr(
        qdt,
        "assess_breaking_credibility",
        lambda **_kw: BreakingAssessment(credible=True, reason="ok", method="heuristic"),
    )
    ctx = _ctx(review_full=True)
    assert qdt._run_breaking_vetoes(ctx) is None
    # The success path tags its outcome with ctx.assessment.method.
    assert ctx.assessment is not None
    assert ctx.assessment.credible


def test_first_veto_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stops at the first firing veto (policy) and never runs the credibility check after it."""
    monkeypatch.setattr(
        qdt,
        "evaluate_breaking_publish",
        lambda *_a, **_kw: SimpleNamespace(allowed=False, reason="diff_too_small"),
    )
    monkeypatch.setattr(
        qdt,
        "assess_breaking_credibility",
        lambda **_kw: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    outcome = qdt._run_breaking_vetoes(_ctx())
    assert outcome == {"status": "skipped", "reason": "diff_too_small"}
