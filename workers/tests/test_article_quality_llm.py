from app.modules.newspaper.article_quality_llm import (
    _parse_quality_response,
    grade_article_quality_llm,
    quality_needs_revision,
)


def test_quality_needs_revision_below_threshold() -> None:
    assert quality_needs_revision(
        {"narrative_synthesis": 2, "technical_depth": 4}, min_score=3
    )
    assert quality_needs_revision(
        {"narrative_synthesis": 4, "technical_depth": 2}, min_score=3
    )
    assert not quality_needs_revision(
        {"narrative_synthesis": 3, "technical_depth": 3}, min_score=3
    )


def test_grade_article_schema_ignores_relevance_in_issues() -> None:
    from app.modules.newspaper.article_grader import grade_article_draft

    body = (
        "## Intro\n\n"
        "Algorand partnership news with enough words to pass the minimum length band "
        "for schema grading in this unit test case only here.\n\n"
        "| Concept | Real-World Implication |\n| -- | -- |\n| Fast finality | "
        "Settlement in seconds |\n\n"
        + ("More grounded prose about tooling. " * 40)
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
    raw = 'Here is the grade:\n```json\n{"narrative_synthesis": 4, "technical_depth": 3, "issues": []}\n```'
    parsed = _parse_quality_response(raw)
    assert parsed is not None
    assert parsed["technical_depth"] == 3


def test_grade_failure_falls_back_to_revision_trigger(monkeypatch) -> None:
    class _BadClient:
        def chat_json_object(self, *_a, **_kw):
            from app.modules.ai.mistral_client import MistralError

            raise MistralError("Mistral returned non-JSON content")

    monkeypatch.setattr(
        "app.modules.ai.mistral_client.get_mistral_digest_client",
        lambda: _BadClient(),
    )
    monkeypatch.setattr(
        "app.core.config.WRITER_QUALITY_LLM_ENABLED", True, raising=False
    )
    result = grade_article_quality_llm(title="T", body="Some article body text.")
    assert result["model"] == "llm_rubric_error"
    assert result["narrative_synthesis"] == 2
    assert result["technical_depth"] == 2
    assert result["issues"]
    assert quality_needs_revision(result, min_score=3)
