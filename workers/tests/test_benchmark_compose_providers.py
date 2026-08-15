"""The local multi-provider benchmark script: provider-configured detection, snapshot loading, and the run/skip/failure-handling orchestration -- no real API calls (compose() is monkeypatched)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.benchmark_compose_providers as bm
from app.modules.ai.compose_runner import ComposeRunResult
from app.modules.ai.mistral_compose import MistralArticleFields


def _write_snapshot(tmp_path: Path) -> Path:
    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            {
                "service_name": "lumirogue-com",
                "source_url": "https://lumirogue.com",
                "page_title": "Lumi Rogue",
                "page_text": "A roguelike on Algorand.",
                "txid": "recompose-208cdbeb",
                "round_num": 0,
                "diff": None,
                "is_first_snapshot": True,
                # Provenance-only fields a real snapshot carries that ArticleInput doesn't accept --
                # _load_article_input must filter these out, not choke on them.
                "_snapshot_source_article_id": "208cdbeb-5b1b-4b5b-90a8-1bfca14a9f07",
                "_snapshot_source_title": "Lumi Rogue is an Algorand roguelike",
            }
        ),
        encoding="utf-8",
    )
    return path


def test_provider_configured_true_when_api_key_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider with a real API key set locally is reported as configured."""
    from app.core import config

    monkeypatch.setattr(config, "KIMI_API_KEY", "real-key")
    assert bm._provider_configured("kimi") is True


def test_provider_configured_false_when_api_key_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    """A provider with a blank API key is reported as NOT configured -- never silently substituted for another provider."""
    from app.core import config

    monkeypatch.setattr(config, "KIMI_API_KEY", "")
    assert bm._provider_configured("kimi") is False


def test_provider_configured_false_for_unknown_provider() -> None:
    """An unregistered provider name is never "configured", regardless of what env vars happen to be set."""
    assert bm._provider_configured("chatgpt-turbo-9000") is False


def test_load_article_input_filters_out_provenance_only_fields(tmp_path: Path) -> None:
    """A real snapshot.json carries _snapshot_source_* provenance fields ArticleInput doesn't accept -- loading must silently drop them, not raise a TypeError on an unexpected kwarg."""
    article_input = bm._load_article_input(_write_snapshot(tmp_path))
    assert article_input.service_name == "lumirogue-com"
    assert article_input.source_url == "https://lumirogue.com"
    assert article_input.is_first_snapshot is True


def test_run_benchmark_skips_unconfigured_providers_and_runs_configured_ones(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only providers with a real key actually run; the rest are skipped, not silently faked."""
    calls: list[str] = []

    def _fake_compose(*, article_input, provider_name, session_register) -> ComposeRunResult:  # noqa: ANN001, ARG001
        calls.append(provider_name)
        return ComposeRunResult(
            fields=MistralArticleFields(
                title="t", summary="s", body="b", heuristic_grade={"grade": 8.5}
            ),
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            provider=provider_name,
            model=f"{provider_name}-model",
            duration_ms=100,
        )

    monkeypatch.setattr(bm, "compose", _fake_compose)
    monkeypatch.setattr(bm, "_provider_configured", lambda name: name == "mistral")

    results = bm.run_benchmark(
        snapshot_path=_write_snapshot(tmp_path),
        db_path=tmp_path / "bench.sqlite",
        runs_per_provider=2,
        provider_names=["mistral", "kimi"],
    )

    # Only mistral (the "configured" one) actually ran, twice.
    assert calls == ["mistral", "mistral"]
    assert len(results) == 2
    assert all(r["provider"] == "mistral" for r in results)
    assert all(r["usage"]["total_tokens"] == 15 for r in results)
    assert all(r["heuristic_grade"] == {"grade": 8.5} for r in results)


def test_run_benchmark_round_robins_across_providers_before_repeating(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One run of every configured provider must complete before any provider's second run starts, so a cross-provider comparison exists as early as possible rather than only after the first provider's full quota."""
    calls: list[str] = []

    def _fake_compose(*, article_input, provider_name, session_register) -> ComposeRunResult:  # noqa: ANN001, ARG001
        calls.append(provider_name)
        return ComposeRunResult(
            fields=MistralArticleFields(title="t", summary="s", body="b"),
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            provider=provider_name,
            model="m",
            duration_ms=1,
        )

    monkeypatch.setattr(bm, "compose", _fake_compose)
    monkeypatch.setattr(bm, "_provider_configured", lambda _name: True)

    bm.run_benchmark(
        snapshot_path=_write_snapshot(tmp_path),
        db_path=tmp_path / "bench.sqlite",
        runs_per_provider=3,
        provider_names=["mistral", "deepseek", "kimi"],
    )

    assert calls == [
        "mistral", "deepseek", "kimi",
        "mistral", "deepseek", "kimi",
        "mistral", "deepseek", "kimi",
    ]


def test_run_benchmark_records_a_failed_run_without_aborting_the_sweep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One provider's run raising (e.g. a real API error) must not stop the rest of the sweep -- a benchmark comparing N providers shouldn't die on the first flaky one."""
    call_count = {"n": 0}

    def _flaky_compose(*, article_input, provider_name, session_register) -> ComposeRunResult:  # noqa: ANN001, ARG001
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated API failure")
        return ComposeRunResult(
            fields=MistralArticleFields(title="t", summary="s", body="b"),
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            provider=provider_name,
            model="m",
            duration_ms=1,
        )

    monkeypatch.setattr(bm, "compose", _flaky_compose)
    monkeypatch.setattr(bm, "_provider_configured", lambda _name: True)

    results = bm.run_benchmark(
        snapshot_path=_write_snapshot(tmp_path),
        db_path=tmp_path / "bench.sqlite",
        runs_per_provider=2,
        provider_names=["mistral"],
    )

    assert len(results) == 2
    assert "error" in results[0]
    assert "error" not in results[1]
