"""The eval harness (scripts/eval_compose_prompts.py) is a manual, costs-money
tool never run in CI — this only pins that its report formatting and fixture
lookup work, without making a real Mistral call."""

from __future__ import annotations

from scripts.eval_compose_fixtures import FIXTURES, get
from scripts.eval_compose_prompts import _run_one


def test_fixtures_are_unique_and_nonempty() -> None:
    names = [f.name for f in FIXTURES]
    assert len(names) == len(set(names))
    assert 5 <= len(FIXTURES) <= 10


def test_get_unknown_fixture_raises() -> None:
    try:
        get("does-not-exist")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_run_one_formats_report(monkeypatch) -> None:
    fixture = FIXTURES[0]

    class _FakeFields:
        title = "A Title"
        summary = "A summary."
        body = "## Heading\n\nSome body text here."
        tags = ("defi", "algorand")
        prompt_version = "test-version"

    monkeypatch.setattr(
        "app.modules.ai.mistral_compose.compose_scrape_article_mistral",
        lambda **kw: _FakeFields(),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.grade_article_draft",
        lambda **kw: {"grade": 8.5, "issues": []},
    )

    report = _run_one(fixture)
    assert fixture.name in report
    assert "test-version" in report
    assert "A Title" in report
    assert "## Body" in report


def test_run_one_reports_compose_failure(monkeypatch) -> None:
    from app.modules.ai.mistral_client import MistralError

    fixture = FIXTURES[0]

    def _raise(**kw):
        raise MistralError("boom")

    monkeypatch.setattr(
        "app.modules.ai.mistral_compose.compose_scrape_article_mistral", _raise
    )

    report = _run_one(fixture)
    assert "COMPOSE FAILED" in report
    assert "boom" in report
