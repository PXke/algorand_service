"""Step 3: the deterministic gatekeeper diverts an auto-publishable draft into human review only when GATEKEEPER_ENFORCE is on (default off = shadow)."""

import pytest

from app.modules.gatekeeper.live import DeterministicGate
from app.modules.newspaper.publish_policy import PublishKind
from app.modules.newspaper.tasks.publish_tasks import (
    _content_quality_fails,
    _gate_enforces_review,
    _quality_floor_fails,
)

_FAIL = DeterministicGate(factuality_score=0.1, completeness_passed=False, passed=False)
_PASS = DeterministicGate(factuality_score=0.9, completeness_passed=True, passed=True)


def _args() -> dict:
    return {
        "title": "T",
        "body": "B",
        "page_text": "src",
        "source_url": "https://example.com",
    }


def test_no_divert_when_enforce_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """When GATEKEEPER_ENFORCE is off, the deterministic gate is never consulted and review is never forced."""
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENFORCE", False, raising=False)
    # gate_draft must not even be consulted when enforcement is off.
    monkeypatch.setattr(
        "app.modules.gatekeeper.live.gate_draft",
        lambda **_: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    assert _gate_enforces_review(clf_decision=True, **_args()) is False


def test_divert_when_enforce_on_and_gate_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """With enforcement on, a failing deterministic gate diverts the draft to review."""
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENFORCE", True, raising=False)
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_: _FAIL)
    assert _gate_enforces_review(clf_decision=True, **_args()) is True


def test_no_divert_when_gate_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """With enforcement on, a passing deterministic gate does not divert the draft to review."""
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENFORCE", True, raising=False)
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_: _PASS)
    assert _gate_enforces_review(clf_decision=True, **_args()) is False


def test_no_divert_when_gate_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """A gate_draft result of None (gate skipped) does not divert the draft to review."""
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENFORCE", True, raising=False)
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_: None)
    assert _gate_enforces_review(clf_decision=True, **_args()) is False


def test_low_confidence_already_reviews_not_double_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-True classifier decision is already review-bound, so the deterministic gate never runs again."""
    # clf_decision is not True -> already review-bound; helper returns False so
    # the gate is never run a second time for it.
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENFORCE", True, raising=False)
    monkeypatch.setattr(
        "app.modules.gatekeeper.live.gate_draft",
        lambda **_: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    assert _gate_enforces_review(clf_decision=None, **_args()) is False


def test_quality_floor_no_divert_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the quality gate disabled, even a below-floor grade does not fail the quality floor check."""
    monkeypatch.setattr("app.core.config.WRITER_QUALITY_GATE_ENABLED", False, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_QUALITY_FLOOR", 6.0, raising=False)
    assert _quality_floor_fails({"grade": 2.0}) is False


def test_quality_floor_diverts_below_floor_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the quality gate enabled, only grades strictly below the floor fail."""
    monkeypatch.setattr("app.core.config.WRITER_QUALITY_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_QUALITY_FLOOR", 6.0, raising=False)
    assert _quality_floor_fails({"grade": 5.9}) is True
    assert _quality_floor_fails({"grade": 6.0}) is False
    assert _quality_floor_fails({"grade": 8.1}) is False


def test_quality_floor_fails_open_on_missing_or_bad_grade(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing, None, or non-numeric grade fails open (does not trigger the quality floor)."""
    monkeypatch.setattr("app.core.config.WRITER_QUALITY_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_QUALITY_FLOOR", 6.0, raising=False)
    assert _quality_floor_fails(None) is False
    assert _quality_floor_fails({}) is False
    assert _quality_floor_fails({"grade": None}) is False
    assert _quality_floor_fails({"grade": "not-a-number"}) is False


def test_content_quality_fails_below_reject_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Content relevance strictly below the frontier reject score fails; at or above it passes."""
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_REJECT_SCORE", 0.2, raising=False)
    assert _content_quality_fails(0.1) is True
    assert _content_quality_fails(0.2) is False
    assert _content_quality_fails(0.9) is False


def test_content_quality_fails_uses_stricter_floor_for_content_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CONTENT_UPDATE publish kind is held to the stricter content-update floor, not the lenient discovery one."""
    monkeypatch.setattr("app.core.config.FRONTIER_CONTENT_REJECT_SCORE", 0.2, raising=False)
    monkeypatch.setattr("app.core.config.CONTENT_UPDATE_QUALITY_FLOOR", 0.35, raising=False)
    # A relevance of 0.31 clears the lenient discovery floor (0.2) but must
    # fail the stricter CONTENT_UPDATE-specific floor (0.35) — this is the
    # zk-colorsort case: a low-relevance service diff that used to slip through.
    assert _content_quality_fails(0.31) is False
    assert _content_quality_fails(0.31, PublishKind.CONTENT_UPDATE) is True
    assert _content_quality_fails(0.36, PublishKind.CONTENT_UPDATE) is False
    assert _content_quality_fails(0.1, PublishKind.SERVICE_DISCOVERY) is True
