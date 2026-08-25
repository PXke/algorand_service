"""Root-caused 2026-08-25: _fresh_auto_approve_passes (FRESH_AUTO_APPROVE_GRADE_FLOOR) and _grade_and_gate (RECOMPOSE_AUTO_APPLY_GRADE_FLOOR, via recompose_published) both used to grade on article_grader.grade_article_draft's raw output -- schema/structure+length ONLY. The LLM quality rubric (narrative_synthesis/technical_depth/critical_distance/repetition), weighted 75% everywhere else via article_grader.fuse_quality_into_grade, had zero say in whether a fresh article auto-published without human review, or a recompose auto-applied over a LIVE article.

The fix reuses ArticleComposeResult.heuristic_grade -- the FUSED review dict
the writer's own grade/revise loop already produced once during compose
(llm_compose._grade_current_draft calls fuse_quality_into_grade) -- instead
of re-deriving a schema-only number at publish time. These tests build that
fused dict directly via fuse_quality_into_grade (the same function the
compose-time loop calls) so a regression back to schema-only grading is
caught: a structurally-perfect draft with an abysmal LLM rubric must NOT
clear the floor, and a structurally-rough draft with an excellent LLM rubric
must clear it -- the opposite of what schema-only grading would decide in
each case.
"""

from __future__ import annotations

import pytest

from app.modules.gatekeeper.live import DeterministicGate
from app.modules.newspaper.article_composer import ArticleComposeResult
from app.modules.newspaper.article_grader import fuse_quality_into_grade
from app.modules.newspaper.tasks.publish_tasks import _fresh_auto_approve_passes, _grade_and_gate

_GOOD_TITLE = "Nodely Expands Infrastructure with Voi Support and Enterprise Tiers"
_PASS_GATE = DeterministicGate(factuality_score=0.95, completeness_passed=True, passed=True)


def _fused(*, structure: float, length: float, quality_1_to_5: tuple[int, int, int, int]) -> dict:
    """Build a fused review dict the same way llm_compose._grade_current_draft does: grade_article_draft's schema output, run through fuse_quality_into_grade with a rubric result."""
    schema_grade = round(10.0 * (structure * 0.55 + length * 0.45), 1)
    review = {
        "grade": schema_grade,
        "subscores": {"structure": structure, "length": length},
        "issues": [],
    }
    quality = {
        "narrative_synthesis": quality_1_to_5[0],
        "technical_depth": quality_1_to_5[1],
        "critical_distance": quality_1_to_5[2],
        "repetition": quality_1_to_5[3],
    }
    return fuse_quality_into_grade(review, quality)


# A structurally perfect draft (schema-only grade = 10.0, comfortably clears
# any historical 8.0 floor) but the LLM rubric scored it 1/5 across every
# dimension -- exactly the shape of the 2026-08-06 incident this whole fusion
# design exists to catch (schema flat/high while the actual journalism is
# poor). Fused grade must be low.
_STRUCTURALLY_PERFECT_BUT_BAD_QUALITY = _fused(
    structure=1.0, length=1.0, quality_1_to_5=(1, 1, 1, 1)
)

# A structurally rough draft (schema-only grade = 5.0, would have failed the
# old schema-only gate outright) but the LLM rubric scored it 5/5 across
# every dimension -- excellent journalism despite a formatting nit. Fused
# grade must be high enough to clear the floor.
_STRUCTURALLY_ROUGH_BUT_GREAT_QUALITY = _fused(
    structure=0.5, length=0.5, quality_1_to_5=(5, 5, 5, 5)
)


def test_fixtures_sanity_check_schema_only_would_pick_the_opposite_draft() -> None:
    """Confirms the two fixtures actually invert the schema-only ranking, so the tests below are meaningful."""
    assert _STRUCTURALLY_PERFECT_BUT_BAD_QUALITY["grade"] < 8.0  # fused: correctly low
    assert _STRUCTURALLY_ROUGH_BUT_GREAT_QUALITY["grade"] >= 8.0  # fused: correctly high
    # What the OLD schema-only code would have used instead (structure/length
    # only, no rubric) -- the exact opposite ranking.
    schema_only_perfect = round(10.0 * (1.0 * 0.55 + 1.0 * 0.45), 1)
    schema_only_rough = round(10.0 * (0.5 * 0.55 + 0.5 * 0.45), 1)
    assert schema_only_perfect >= 8.0
    assert schema_only_rough < 8.0


def test_fresh_auto_approve_bad_rubric_blocks_despite_perfect_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structurally-perfect draft with an abysmal LLM rubric must NOT auto-approve -- the schema-only gate this replaces would have let it through."""
    monkeypatch.setattr("app.core.config.FRESH_AUTO_APPROVE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.FRESH_AUTO_APPROVE_GRADE_FLOOR", 8.0, raising=False)
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_kw: _PASS_GATE)
    passed, meta = _fresh_auto_approve_passes(
        title=_GOOD_TITLE,
        body="body",
        page_text="source",
        source_url="https://example.com",
        heuristic_grade=_STRUCTURALLY_PERFECT_BUT_BAD_QUALITY,
    )
    assert passed is False
    assert meta["auto_applied"] == "0"
    assert float(meta["grade"]) < 8.0


def test_fresh_auto_approve_good_rubric_allows_despite_rough_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structurally-rough draft with an excellent LLM rubric DOES clear the gate -- the schema-only gate this replaces would have held it for review over a formatting nit."""
    monkeypatch.setattr("app.core.config.FRESH_AUTO_APPROVE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.FRESH_AUTO_APPROVE_GRADE_FLOOR", 8.0, raising=False)
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_kw: _PASS_GATE)
    passed, meta = _fresh_auto_approve_passes(
        title=_GOOD_TITLE,
        body="body",
        page_text="source",
        source_url="https://example.com",
        heuristic_grade=_STRUCTURALLY_ROUGH_BUT_GREAT_QUALITY,
    )
    assert passed is True
    assert meta["auto_applied"] == "1"
    assert float(meta["grade"]) >= 8.0


def test_fresh_auto_approve_missing_heuristic_grade_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No compose-time grade available (e.g. a compose path that never graded) must fail closed, not silently re-grade or pass open."""
    monkeypatch.setattr("app.core.config.FRESH_AUTO_APPROVE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.FRESH_AUTO_APPROVE_GRADE_FLOOR", 8.0, raising=False)
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_kw: _PASS_GATE)
    passed, meta = _fresh_auto_approve_passes(
        title=_GOOD_TITLE,
        body="body",
        page_text="source",
        source_url="https://example.com",
        heuristic_grade=None,
    )
    assert passed is False
    assert meta["auto_applied"] == "0"
    assert "grade" not in meta


def test_grade_and_gate_bad_rubric_grade_value_is_low_despite_perfect_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_grade_and_gate backs recompose_published's RECOMPOSE_AUTO_APPLY_GRADE_FLOOR gate: a structurally-perfect draft with an abysmal LLM rubric must report a LOW grade_value, not the schema-only 10.0 that would have auto-applied it onto a live article."""
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_kw: _PASS_GATE)
    composed = ArticleComposeResult(
        title=_GOOD_TITLE,
        summary="s",
        body="body",
        composer="mistral",
        heuristic_grade=_STRUCTURALLY_PERFECT_BUT_BAD_QUALITY,
    )
    grade_meta, grade_value, gate_ok = _grade_and_gate(
        composed,
        title=_GOOD_TITLE,
        source_url="https://example.com",
        page_text="source",
        service_id="https://example.com",
    )
    assert gate_ok is True
    assert grade_value is not None
    assert grade_value < 8.0
    assert float(grade_meta["grade"]) == grade_value


def test_grade_and_gate_good_rubric_grade_value_is_high_despite_rough_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A structurally-rough draft with an excellent LLM rubric reports a grade_value that clears the auto-apply floor -- the schema-only 5.0 this replaces would have wrongly diverted it to human review."""
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_kw: _PASS_GATE)
    composed = ArticleComposeResult(
        title=_GOOD_TITLE,
        summary="s",
        body="body",
        composer="mistral",
        heuristic_grade=_STRUCTURALLY_ROUGH_BUT_GREAT_QUALITY,
    )
    grade_meta, grade_value, gate_ok = _grade_and_gate(
        composed,
        title=_GOOD_TITLE,
        source_url="https://example.com",
        page_text="source",
        service_id="https://example.com",
    )
    assert gate_ok is True
    assert grade_value is not None
    assert grade_value >= 8.0
    assert float(grade_meta["grade"]) == grade_value


def test_grade_and_gate_missing_heuristic_grade_fails_soft_to_no_grade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matches _grade_and_gate's existing fail-soft design: a missing/errored grade must never stop the draft being stored -- it just means grade_value is None (which the auto-apply AND-gate then treats as fail-closed)."""
    monkeypatch.setattr("app.modules.gatekeeper.live.gate_draft", lambda **_kw: _PASS_GATE)
    composed = ArticleComposeResult(
        title=_GOOD_TITLE, summary="s", body="body", composer="mistral", heuristic_grade=None
    )
    grade_meta, grade_value, gate_ok = _grade_and_gate(
        composed,
        title=_GOOD_TITLE,
        source_url="https://example.com",
        page_text="source",
        service_id="https://example.com",
    )
    assert grade_value is None
    assert "grade" not in grade_meta
    assert gate_ok is True
