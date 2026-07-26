"""LLM quality-rubric revision triggers and response parsing."""

from typing import Never

import pytest

from app.modules.newspaper.article_quality_llm import (
    _parse_quality_response,
    grade_article_quality_llm,
    quality_needs_revision,
)


def test_quality_needs_revision_below_threshold() -> None:
    """Flags revision when any scored dimension falls below the minimum score threshold."""
    assert quality_needs_revision({"narrative_synthesis": 2, "technical_depth": 4}, min_score=3)
    assert quality_needs_revision({"narrative_synthesis": 4, "technical_depth": 2}, min_score=3)
    assert not quality_needs_revision({"narrative_synthesis": 3, "technical_depth": 3}, min_score=3)


def test_quality_needs_revision_low_critical_distance() -> None:
    """Flags revision for a low critical_distance score even when other dimensions score well, but ignores a missing critical_distance key."""
    # A well-written but uncritical piece (e.g. relaying a CEX's own marketing
    # claims without naming custodial risk) should still trigger a revision.
    assert quality_needs_revision(
        {"narrative_synthesis": 5, "technical_depth": 5, "critical_distance": 2},
        min_score=3,
    )
    assert not quality_needs_revision(
        {"narrative_synthesis": 3, "technical_depth": 3, "critical_distance": 3},
        min_score=3,
    )
    # Missing key (older callers/tests) must not crash or count as failing.
    assert not quality_needs_revision({"narrative_synthesis": 3, "technical_depth": 3}, min_score=3)


def test_quality_needs_revision_low_repetition() -> None:
    """Root-caused 2026-07-15: a real NFT-marketplace draft restated 'fees are undisclosed' five times across sections and still scored 8.6 overall, because nothing scored dimension checked by quality_needs_revision covered cross-section repetition — the rubric's own free-text issues mentioned it, but that alone never forced a revision pass."""
    assert quality_needs_revision(
        {"narrative_synthesis": 5, "technical_depth": 5, "critical_distance": 5, "repetition": 2},
        min_score=3,
    )
    assert not quality_needs_revision(
        {"narrative_synthesis": 3, "technical_depth": 3, "critical_distance": 3, "repetition": 3},
        min_score=3,
    )
    # Missing key (older callers/tests) must not crash or count as failing.
    assert not quality_needs_revision(
        {"narrative_synthesis": 3, "technical_depth": 3, "critical_distance": 3}, min_score=3
    )


def test_grade_article_quality_llm_includes_repetition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Carries the rubric's repetition score and a matching issue message through the LLM grading result."""

    class _StubClient:
        def chat_json_object(self, *_a: object, **_kw: object) -> dict:
            return {
                "narrative_synthesis": 4,
                "technical_depth": 4,
                "critical_distance": 4,
                "repetition": 2,
                "issues": [],
            }

    monkeypatch.setattr("app.core.config.WRITER_QUALITY_LLM_ENABLED", True, raising=False)
    result = grade_article_quality_llm(
        title="T", body="Some article body text.", client=_StubClient()
    )
    assert result["model"] == "llm_rubric"
    assert result["repetition"] == 2
    assert any("repetition scored 2/5" in i for i in result["issues"])


def test_grade_article_schema_ignores_relevance_in_issues() -> None:
    """Surfaces a weak-Algorand-relevance finding in signals but keeps it out of the blocking issues list."""
    from app.modules.newspaper.article_grader import grade_article_draft

    body = (
        "## Intro\n\n"
        "Algorand partnership news with enough words to pass the minimum length band "
        "for schema grading in this unit test case only here.\n\n"
        "| Concept | Real-World Implication |\n| -- | -- |\n| Fast finality | "
        "Settlement in seconds |\n\n" + ("More grounded prose about tooling. " * 40)
    )
    review = grade_article_draft(
        title="Short title",
        summary="Deck",
        body=body,
        source_url="https://unrelated-recipes.example/",
    )
    assert review["model"] == "schema_heuristic"
    assert not any("weak Algorand relevance" in i for i in review.get("issues", []))
    assert any("weak Algorand relevance" in s for s in review.get("signals", []))


def test_parse_quality_response_salvages_fenced_json() -> None:
    """Extracts the JSON object from a markdown-fenced LLM response with surrounding prose."""
    raw = 'Here is the grade:\n```json\n{"narrative_synthesis": 4, "technical_depth": 3, "issues": []}\n```'
    parsed = _parse_quality_response(raw)
    assert parsed is not None
    assert parsed["technical_depth"] == 3


def test_grade_article_quality_llm_includes_critical_distance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carries the rubric's critical_distance score and a matching issue message through the LLM grading result."""

    class _StubClient:
        def chat_json_object(self, *_a: object, **_kw: object) -> dict:
            return {
                "narrative_synthesis": 4,
                "technical_depth": 4,
                "critical_distance": 2,
                "repetition": 4,
                "issues": [],
            }

    monkeypatch.setattr("app.core.config.WRITER_QUALITY_LLM_ENABLED", True, raising=False)
    result = grade_article_quality_llm(
        title="T", body="Some article body text.", client=_StubClient()
    )
    assert result["model"] == "llm_rubric"
    assert result["critical_distance"] == 2
    assert any("critical distance scored 2/5" in i for i in result["issues"])


def test_partial_rubric_response_retries_then_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-07-16: a real draft was graded narrative_synthesis=3 with the other three dimensions null — the rubric response was partial, and quality_needs_revision treats None as passing, so the draft was effectively graded on 1 of 4 dimensions. A partial response must retry once, and any dimension still missing FAILS CLOSED at 2."""
    calls = {"n": 0}

    class _PartialClient:
        def chat_json_object(self, *_a: object, **_kw: object) -> dict:
            calls["n"] += 1
            return {"narrative_synthesis": 3, "issues": []}  # always partial

    monkeypatch.setattr("app.core.config.WRITER_QUALITY_LLM_ENABLED", True, raising=False)
    result = grade_article_quality_llm(
        title="T", body="Some article body text.", client=_PartialClient()
    )
    assert calls["n"] == 2  # one retry
    assert result["model"] == "llm_rubric_partial"
    assert result["narrative_synthesis"] == 3  # the score it DID give survives
    assert result["technical_depth"] == 2  # missing -> failing
    assert result["critical_distance"] == 2
    assert result["repetition"] == 2
    assert quality_needs_revision(result, min_score=3)
    assert any("no score" in i for i in result["issues"])


def test_partial_rubric_recovered_by_retry_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Does not fail-close any dimension when the retried response fills in all the fields the first partial response left out."""
    responses = iter(
        [
            {"narrative_synthesis": 4, "issues": []},  # partial first answer
            {
                "narrative_synthesis": 4,
                "technical_depth": 4,
                "critical_distance": 4,
                "repetition": 4,
                "issues": [],
            },
        ]
    )

    class _FlakyClient:
        def chat_json_object(self, *_a: object, **_kw: object) -> dict:
            return next(responses)

    monkeypatch.setattr("app.core.config.WRITER_QUALITY_LLM_ENABLED", True, raising=False)
    result = grade_article_quality_llm(
        title="T", body="Some article body text.", client=_FlakyClient()
    )
    assert result["model"] == "llm_rubric"
    assert result["technical_depth"] == 4
    assert not quality_needs_revision(result, min_score=3)


def test_grade_failure_falls_back_to_revision_trigger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails closed to a low-scoring "llm_rubric_error" result that still forces revision when the LLM call itself raises."""

    class _BadClient:
        def chat_json_object(self, *_a: object, **_kw: object) -> Never:
            from app.modules.ai.mistral_client import MistralError

            raise MistralError("Mistral returned non-JSON content")

    monkeypatch.setattr(
        "app.modules.ai.mistral_client.get_mistral_digest_client",
        lambda: _BadClient(),
    )
    monkeypatch.setattr("app.core.config.WRITER_QUALITY_LLM_ENABLED", True, raising=False)
    result = grade_article_quality_llm(title="T", body="Some article body text.")
    assert result["model"] == "llm_rubric_error"
    assert result["narrative_synthesis"] == 2
    assert result["technical_depth"] == 2
    assert result["critical_distance"] == 2
    assert result["issues"]
    assert quality_needs_revision(result, min_score=3)


def test_rubric_technical_depth_is_relevance_gated() -> None:
    """Root-caused 2026-07-18 on the live D13.co article: the rubric's old technical_depth wording ('bridges the story to Algorand layer-1 mechanics ... state proofs, etc.') made the grader emit 'add more specific Algorand layer-1 mechanics (e.g. PPoS, state proofs)' as a fix — and the writer obeyed, inserting a state-proofs non sequitur into a wallet-phishing post-mortem. Relevance must gate the dimension, and the grader must never prescribe adding mechanics."""
    from app.modules.newspaper.article_quality_llm import (
        _FALLBACK_QUALITY,
        _QUALITY_RUBRIC,
    )

    assert "RELEVANCE GATES THIS SCORE" in _QUALITY_RUBRIC
    assert "Never suggest 'add more layer-1 mechanics'" in _QUALITY_RUBRIC
    assert "wallet-phishing post-mortem" in _QUALITY_RUBRIC
    # Fallback issues must not carry the generic add-mechanics instruction.
    joined = " ".join(_FALLBACK_QUALITY["issues"])
    assert "THIS story actually involves" in joined
