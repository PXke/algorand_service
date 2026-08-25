"""The eval harness (scripts/eval_compose_prompts.py) is a manual, costs-money tool never run in CI — this only pins that its report formatting and fixture lookup work, without making a real Mistral call."""

from __future__ import annotations

from typing import Never

import pytest

from scripts.eval_compose_fixtures import FIXTURES, get
from scripts.eval_compose_prompts import _run_one


def test_fixtures_are_unique_and_nonempty() -> None:
    """Eval fixtures have unique names and there are between 5 and 10 of them."""
    names = [f.name for f in FIXTURES]
    assert len(names) == len(set(names))
    assert 5 <= len(FIXTURES) <= 10


def test_get_unknown_fixture_raises() -> None:
    """Looking up a fixture by an unknown name raises KeyError."""
    try:
        get("does-not-exist")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_run_one_formats_report(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful fake compose+grade run produces a report with the fixture name, title, and body."""
    fixture = FIXTURES[0]

    class _FakeFields:
        title = "A Title"
        summary = "A summary."
        body = "## Heading\n\nSome body text here."
        tags = ("defi", "algorand")
        prompt_version = "test-version"

    monkeypatch.setattr(
        "app.modules.ai.llm_compose.compose_scrape_article",
        lambda **_kw: _FakeFields(),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **_kw: {"grade": 8.5, "issues": []},
    )

    report = _run_one(fixture)
    assert fixture.name in report
    assert "test-version" in report
    assert "A Title" in report
    assert "## Body" in report


def test_run_one_reports_compose_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A compose failure surfaces as a "COMPOSE FAILED" report line with the error message."""
    from app.modules.ai.llm_provider import LLMError

    fixture = FIXTURES[0]

    def _raise(**_kw: object) -> Never:
        raise LLMError("boom")

    monkeypatch.setattr("app.modules.ai.llm_compose.compose_scrape_article", _raise)

    report = _run_one(fixture)
    assert "COMPOSE FAILED" in report
    assert "boom" in report
