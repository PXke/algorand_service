"""The breaking drain's per-row vetoes are a uniform ordered list
(_BREAKING_VETOES) like the standard drain's _PRE_COMPOSE_GATES and the
compose-side _PRE_COMPOSE_VETOES. Pins order, outcome dicts, and the
credibility assessment being stashed on the ctx for the success path."""

from types import SimpleNamespace

from app.modules.newspaper.breaking_credibility import BreakingAssessment
from app.modules.newspaper.tasks import queue_drain_tasks as qdt


def _ctx(review_full=False, payload=None):
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


def test_veto_order():
    assert qdt._BREAKING_VETOES == (
        qdt._breaking_policy_veto,
        qdt._breaking_review_slot_veto,
        qdt._breaking_credibility_veto,
    )


def test_policy_veto_outcome(monkeypatch):
    monkeypatch.setattr(
        qdt,
        "evaluate_breaking_publish",
        lambda *_a, **_kw: SimpleNamespace(allowed=False, reason="breaking_daily_cap_reached"),
    )
    assert qdt._breaking_policy_veto(_ctx()) == {
        "status": "skipped",
        "reason": "breaking_daily_cap_reached",
    }


def test_review_slot_veto_only_when_full_and_review_bound(monkeypatch):
    monkeypatch.setattr(qdt, "_row_needs_review", lambda _r: True)
    assert qdt._breaking_review_slot_veto(_ctx(review_full=True)) == {
        "status": "skipped",
        "reason": "review_queue_full",
    }
    assert qdt._breaking_review_slot_veto(_ctx(review_full=False)) is None
    monkeypatch.setattr(qdt, "_row_needs_review", lambda _r: False)
    assert qdt._breaking_review_slot_veto(_ctx(review_full=True)) is None


def test_credibility_veto_outcome_and_stashes_assessment(monkeypatch):
    monkeypatch.setattr(
        qdt,
        "assess_breaking_credibility",
        lambda **_kw: BreakingAssessment(
            credible=False, reason="no_evidence", method="heuristic"
        ),
    )
    ctx = _ctx()
    outcome = qdt._breaking_credibility_veto(ctx)
    assert outcome == {
        "status": "skipped",
        "reason": "not_credible:no_evidence",
        "method": "heuristic",
    }
    assert ctx.assessment is not None and ctx.assessment.method == "heuristic"


def test_credible_row_passes_with_assessment_available(monkeypatch):
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
    assert ctx.assessment is not None and ctx.assessment.credible


def test_first_veto_wins(monkeypatch):
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
