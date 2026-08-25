"""compose(): the standalone, Celery-free entry point a benchmark script calls directly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import app.modules.ai.compose_runner as compose_runner
from app.modules.ai.compose_runner import ArticleInput, ComposeRunResult, compose
from app.modules.ai.llm_compose import LLMArticleFields
from app.modules.ai.llm_provider import LLMProvider
from app.modules.ai.session_register import SessionRegisterSQLite


class _FakeProvider(LLMProvider):
    """A minimal LLMProvider stand-in -- compose() never needs to reach a real API for these tests, since compose_scrape_article itself is monkeypatched."""

    def __init__(self, *, model: str | None = None, timeout: float | None = None) -> None:
        del timeout
        self._model = model or "fake-model"
        self._usage = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10, "cached_tokens": 2}

    def chat_completion(self, messages: list, *, max_tokens=None, temperature=0.3) -> str:  # noqa: ANN001, ARG002
        return ""

    def chat_json_object(self, messages: list, *, max_tokens=None, temperature=0.3) -> dict:  # noqa: ANN001, ARG002
        return {}

    def chat_with_tools(self, _messages: list, **_kwargs: object) -> str:
        return ""

    def usage_totals(self) -> dict[str, int]:
        return dict(self._usage)

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "fake"


def _article_input() -> ArticleInput:
    return ArticleInput(
        service_name="lumirogue-com",
        source_url="https://lumirogue.com",
        page_title="Lumi Rogue",
        page_text="A roguelike on Algorand.",
        txid="benchmark-run",
        round_num=0,
        diff=None,
        is_first_snapshot=True,
    )


def test_compose_returns_a_result_with_usage_provider_model_and_duration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """compose() wires a fresh provider into compose_scrape_article and reports back combined usage/timing."""
    captured_kwargs: dict[str, Any] = {}

    def _fake_get_provider(name: str, *, model: str | None = None, timeout: float | None = None) -> LLMProvider:
        del name
        return _FakeProvider(model=model, timeout=timeout)

    def _fake_compose_scrape_article(**kwargs: object) -> LLMArticleFields:
        captured_kwargs.update(kwargs)
        return LLMArticleFields(title="t", summary="s", body="b")

    monkeypatch.setattr(compose_runner, "get_provider", _fake_get_provider)
    monkeypatch.setattr(
        compose_runner, "compose_scrape_article", _fake_compose_scrape_article
    )

    register = SessionRegisterSQLite(tmp_path / "bench.sqlite")
    result = compose(
        article_input=_article_input(),
        provider_name="kimi",
        session_register=register,
    )

    assert isinstance(result, ComposeRunResult)
    assert result.fields.title == "t"
    assert result.provider == "kimi"
    assert result.model == "fake-model"
    # Two _FakeProvider instances (writer + research), each usage_totals()
    # returning the same fixed dict -- compose() sums both.
    assert result.usage == {
        "prompt_tokens": 14,
        "completion_tokens": 6,
        "total_tokens": 20,
        "cached_tokens": 4,
    }
    assert result.duration_ms >= 0

    # The article_input's fields reached compose_scrape_article unchanged.
    assert captured_kwargs["service_name"] == "lumirogue-com"
    assert captured_kwargs["source_url"] == "https://lumirogue.com"
    assert captured_kwargs["session_register"] is register
    assert isinstance(captured_kwargs["client"], _FakeProvider)
    assert isinstance(captured_kwargs["research_client"], _FakeProvider)


def test_compose_uses_the_same_provider_for_both_tiers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A benchmark compares one provider end to end -- writer and research tiers must both be the requested provider, not a purpose-routed mix."""
    requested_names = []

    def _fake_get_provider(name: str, *, model=None, timeout=None) -> LLMProvider:  # noqa: ANN001
        requested_names.append(name)
        return _FakeProvider(model=model, timeout=timeout)

    monkeypatch.setattr(compose_runner, "get_provider", _fake_get_provider)
    monkeypatch.setattr(
        compose_runner,
        "compose_scrape_article",
        lambda **_kw: LLMArticleFields(title="t", summary="s", body="b"),
    )

    compose(
        article_input=_article_input(),
        provider_name="glm",
        session_register=SessionRegisterSQLite(tmp_path / "bench.sqlite"),
    )

    assert requested_names == ["glm", "glm"]


def test_article_input_is_frozen() -> None:
    """ArticleInput is a frozen snapshot -- a benchmark loop reuses the exact same instance across every provider/run without risk of one run's compose mutating another's input."""
    article_input = _article_input()
    with pytest.raises(AttributeError):
        article_input.page_text = "mutated"  # type: ignore[misc]
