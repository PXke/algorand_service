"""Step 3: the deterministic gatekeeper diverts an auto-publishable draft into
human review only when GATEKEEPER_ENFORCE is on (default off = shadow)."""

from app.modules.gatekeeper.live import DeterministicGate
from app.modules.newspaper.tasks.publish_tasks import _gate_enforces_review

_FAIL = DeterministicGate(factuality_score=0.1, completeness_passed=False, passed=False)
_PASS = DeterministicGate(factuality_score=0.9, completeness_passed=True, passed=True)


def _args():
    return {
        "title": "T",
        "body": "B",
        "page_text": "src",
        "source_url": "https://example.com",
    }


def test_no_divert_when_enforce_off(monkeypatch):
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENFORCE", False, raising=False)
    # gate_draft must not even be consulted when enforcement is off.
    monkeypatch.setattr(
        "app.modules.gatekeeper.live.gate_draft",
        lambda **_: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    assert _gate_enforces_review(clf_decision=True, **_args()) is False


def test_divert_when_enforce_on_and_gate_fails(monkeypatch):
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENFORCE", True, raising=False)
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_: _FAIL)
    assert _gate_enforces_review(clf_decision=True, **_args()) is True


def test_no_divert_when_gate_passes(monkeypatch):
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENFORCE", True, raising=False)
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_: _PASS)
    assert _gate_enforces_review(clf_decision=True, **_args()) is False


def test_no_divert_when_gate_none(monkeypatch):
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENFORCE", True, raising=False)
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_: None)
    assert _gate_enforces_review(clf_decision=True, **_args()) is False


def test_low_confidence_already_reviews_not_double_gated(monkeypatch):
    # clf_decision is not True -> already review-bound; helper returns False so
    # the gate is never run a second time for it.
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENFORCE", True, raising=False)
    monkeypatch.setattr(
        "app.modules.gatekeeper.live.gate_draft",
        lambda **_: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    assert _gate_enforces_review(clf_decision=None, **_args()) is False
