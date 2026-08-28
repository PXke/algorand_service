"""Usage accounting for the rubric/digest-tier ephemeral LLM calls.

Covers rubric-grading / digest-synthesis / entity-enumeration / narrative-
outline LLM calls that (2026-08-28 audit) build their OWN ephemeral client
via get_llm_rubric_client()/get_llm_digest_client() instead of reusing the
compose's research_llm/llm pair -- so their real spend never reached
_usage_so_far()'s research_llm+llm sum, and therefore never reached the
compose_sessions row an admin actually reads token totals from.

These tests exercise the fix (_merge_usage + the extra_usage accumulator
threaded through _synthesize_research_digest / _run_entity_enumeration /
_run_narrative_outline / _review_and_revise / _run_two_stage_compose /
_compose_via_writer_tools_locked), and pin that the pre-existing
research_llm/llm accounting is unaffected.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.ai import llm_compose as mc

_ZERO_USAGE = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0}


def _usage(prompt: int, completion: int, cached: int = 0) -> dict[str, int]:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "cached_tokens": cached,
    }


class _FakeClient:
    """Minimal LLMProvider stand-in: every LLM-shaped method plus a fixed usage_totals() return."""

    def __init__(
        self,
        *,
        model: str = "fake-model",
        provider: str = "mistral",
        usage: dict[str, int] | None = None,
    ) -> None:
        self.model = model
        self.provider = provider
        self._usage = usage if usage is not None else dict(_ZERO_USAGE)
        self.calls = 0

    def usage_totals(self) -> dict[str, int]:
        return self._usage

    def chat_completion(self, *_a: object, **_kw: object) -> str:
        self.calls += 1
        return "some text"

    def chat_json_object(self, *_a: object, **_kw: object) -> dict:
        self.calls += 1
        return {"title": "A Title", "summary": "A summary.", "body": "A body.", "tags": ["algo"]}

    def chat_with_tools(self, *_a: object, **_kw: object) -> str:
        self.calls += 1
        return "{}"


# --------------------------------------------------------------------------- #
# _merge_usage itself
# --------------------------------------------------------------------------- #


def test_merge_usage_sums_into_accumulator() -> None:
    """Each merge adds onto the running accumulator rather than replacing it."""
    acc = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cached_tokens": 1}
    mc._merge_usage(acc, _usage(5, 3, cached=2))
    assert acc == {
        "prompt_tokens": 15,
        "completion_tokens": 5,
        "total_tokens": 20,
        "cached_tokens": 3,
    }


def test_merge_usage_noop_when_accumulator_is_none() -> None:
    """Every ephemeral-client call site passes extra_usage unconditionally; callers that never wired one up (extra_usage=None) must not crash."""
    mc._merge_usage(None, _usage(5, 3))  # must not raise


# --------------------------------------------------------------------------- #
# Digest-tier ephemeral clients: _synthesize_research_digest / raw-mode gap
# extraction / entity enumeration / narrative outline
# --------------------------------------------------------------------------- #


def test_synthesize_research_digest_merges_ephemeral_digest_client_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-raw-mode synthesis spends on its own get_llm_digest_client() instance -- that spend must reach extra_usage."""
    digest_client = _FakeClient(usage=_usage(120, 40))
    monkeypatch.setattr(mc, "get_llm_digest_client", lambda: digest_client)

    extra_usage = dict(_ZERO_USAGE)
    trace = [{"tool": "fetch_url", "arguments": {}, "result": {"url": "https://x"}}]
    digest = mc._synthesize_research_digest(
        trace=trace, research_context="ctx", provider="mistral", extra_usage=extra_usage
    )

    assert digest_client.calls == 1
    assert extra_usage == _usage(120, 40)
    assert isinstance(digest, str)


def test_synthesize_research_digest_merges_usage_even_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed synthesis call still degrades to the raw trace (existing behavior) -- but any usage the ephemeral client DID record before failing must still reach extra_usage (finally, not the happy-path only)."""

    class _FailingClient(_FakeClient):
        def chat_completion(self, *_a: object, **_kw: object) -> str:
            self.calls += 1
            raise RuntimeError("boom")

    digest_client = _FailingClient(usage=_usage(80, 0))
    monkeypatch.setattr(mc, "get_llm_digest_client", lambda: digest_client)

    extra_usage = dict(_ZERO_USAGE)
    trace = [{"tool": "fetch_url", "arguments": {}, "result": {"url": "https://x"}}]
    digest = mc._synthesize_research_digest(
        trace=trace, research_context="ctx", provider="mistral", extra_usage=extra_usage
    )

    assert extra_usage == _usage(80, 0)
    assert isinstance(digest, str)  # degraded to raw trace, never raised


def test_synthesize_research_digest_raw_mode_merges_gap_extraction_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """deepseek/raw-mode research skips the full synthesis pass but still runs the cheap gap-extraction call (_extract_gaps_from_raw_trace) on its own ephemeral digest client -- that spend must land in extra_usage too."""
    gap_client = _FakeClient(usage=_usage(30, 10))
    monkeypatch.setattr(mc, "get_llm_digest_client", lambda: gap_client)
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", True, raising=False)

    extra_usage = dict(_ZERO_USAGE)
    trace = [{"tool": "fetch_url", "arguments": {}, "result": {"url": "https://x"}}]
    digest = mc._synthesize_research_digest(
        trace=trace, research_context="ctx", provider="deepseek", extra_usage=extra_usage
    )

    assert gap_client.calls == 1
    assert extra_usage == _usage(30, 10)
    assert isinstance(digest, str)


def test_run_entity_enumeration_merges_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """The special-edition entity-enumeration pass spends on its own ephemeral digest client too."""
    digest_client = _FakeClient(usage=_usage(15, 5))
    monkeypatch.setattr(mc, "get_llm_digest_client", lambda: digest_client)
    monkeypatch.setattr(mc, "_format_research_digest", lambda _t: "- fetch_url -> {}")

    extra_usage = dict(_ZERO_USAGE)
    mc._run_entity_enumeration(trace=[{"tool": "x"}], digest="digest text", extra_usage=extra_usage)

    assert extra_usage == _usage(15, 5)


def test_run_narrative_outline_merges_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """The special-edition narrative-outline pass spends on its own ephemeral digest client too."""
    digest_client = _FakeClient(usage=_usage(25, 8))
    monkeypatch.setattr(mc, "get_llm_digest_client", lambda: digest_client)

    extra_usage = dict(_ZERO_USAGE)
    mc._run_narrative_outline(
        digest="digest text", enumeration="enum text", extra_usage=extra_usage
    )

    assert extra_usage == _usage(25, 8)


# --------------------------------------------------------------------------- #
# Rubric grading (_review_and_revise): one client instance for the WHOLE
# grade/revise loop, including the "final re-grade" after the last revision
# -- must be merged exactly once (cumulative usage_totals(), not per-pass).
# --------------------------------------------------------------------------- #


def test_review_and_revise_merges_final_cumulative_rubric_usage_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two grading passes (an initial grade + a revision's final re-grade) run against the SAME quality_llm instance. quality_llm.usage_totals() is cumulative, so extra_usage must end up with the FINAL cumulative total (240 tokens across 2 passes), not double the true spend from summing each pass's already-cumulative snapshot."""
    grade_calls = {"n": 0}

    class _FakeRubricClient(_FakeClient):
        def usage_totals(self) -> dict[str, int]:
            # Cumulative as-if-real: after `n` grading passes, 100 prompt / 20
            # completion tokens have been spent per pass.
            n = grade_calls["n"]
            return _usage(100 * n, 20 * n)

    quality_llm = _FakeRubricClient()
    monkeypatch.setattr(mc, "get_llm_rubric_client", lambda **_kw: quality_llm)
    monkeypatch.setattr("app.core.config.WRITER_REVIEW_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_QUALITY_LLM_MIN_SCORE", 4, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_REVISION_MAX_PASSES", 2, raising=False)

    def _fake_grade_current_draft(
        _title: str,
        _summary: str,
        _body: str,
        quality_llm_arg: object,
        *,
        is_special_edition: bool = False,  # noqa: ARG001 -- name must match the real callee's keyword arg
    ) -> dict:
        assert quality_llm_arg is quality_llm
        grade_calls["n"] += 1
        return {
            "grade": 5,
            "issues": [],
            "quality": {
                "model": "llm_rubric",
                "narrative_synthesis": 5,
                "technical_depth": 5,
                "critical_distance": 5,
                "repetition": 5,
                "issues": [],
            },
        }

    monkeypatch.setattr(mc, "_grade_current_draft", _fake_grade_current_draft)

    # First pass looks fixable (forces one revision -> the "final re-grade"
    # is the SECOND call to _grade_current_draft); second pass is clean.
    fixable_calls = {"n": 0}

    def _fake_collect_fixable_issues(*_a: object, **_kw: object) -> list[str]:
        fixable_calls["n"] += 1
        return ["headline: too long"] if fixable_calls["n"] == 1 else []

    monkeypatch.setattr(mc, "_collect_fixable_issues", _fake_collect_fixable_issues)
    monkeypatch.setattr(
        mc,
        "_attempt_revision_with_retry",
        lambda *_a, **_kw: {"title": "T2", "summary": "S", "body": "revised body"},
    )

    extra_usage = dict(_ZERO_USAGE)
    result = mc._review_and_revise(
        SimpleNamespace(),
        {"title": "T", "summary": "S", "body": "original body"},
        system="sys",
        gen_user="gen",
        trace=[],
        extra_usage=extra_usage,
    )

    assert grade_calls["n"] == 2  # initial grade + the post-revision "final re-grade"
    assert extra_usage == _usage(200, 40)  # quality_llm's cumulative total after 2 passes
    assert result["body"] == "revised body"


def test_review_and_revise_skips_merge_when_review_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WRITER_REVIEW_ENABLED=False returns the payload untouched before quality_llm is even built -- extra_usage must stay exactly as given (no rubric client ever ran)."""
    monkeypatch.setattr("app.core.config.WRITER_REVIEW_ENABLED", False, raising=False)
    called = {"yes": False}
    monkeypatch.setattr(mc, "get_llm_rubric_client", lambda **_kw: called.__setitem__("yes", True))

    extra_usage = dict(_ZERO_USAGE)
    payload = {"title": "T", "summary": "S", "body": "B"}
    result = mc._review_and_revise(
        SimpleNamespace(),
        payload,
        system="sys",
        gen_user="gen",
        trace=[],
        extra_usage=extra_usage,
    )

    assert result is payload
    assert called["yes"] is False
    assert extra_usage == _ZERO_USAGE


# --------------------------------------------------------------------------- #
# End-to-end: _run_two_stage_compose folds digest + rubric ephemeral usage
# into the SAME extra_usage accumulator the outer compose reads.
# --------------------------------------------------------------------------- #


def test_run_two_stage_compose_folds_digest_and_rubric_usage_into_extra_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Digest + rubric ephemeral usage lands in extra_usage; research_llm/llm's own usage_totals() stay separate (the caller sums those in on its own)."""
    research_llm = _FakeClient(model="research-model", usage=_usage(2000, 300))
    writer_llm = _FakeClient(model="writer-model", usage=_usage(1000, 200))
    digest_client = _FakeClient(model="digest-model", usage=_usage(50, 10))
    quality_llm = _FakeClient(model="rubric-model", usage=_usage(30, 5))

    monkeypatch.setattr(mc, "get_llm_digest_client", lambda: digest_client)
    monkeypatch.setattr(mc, "get_llm_rubric_client", lambda **_kw: quality_llm)
    monkeypatch.setattr("app.core.config.RESEARCH_FLOOR_ENABLED", False, raising=False)
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", False, raising=False)
    monkeypatch.setattr("app.core.config.SPECIAL_EDITION_OUTLINE_ENABLED", False, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_REVIEW_ENABLED", True, raising=False)
    monkeypatch.setattr(mc, "_format_research_digest", lambda _t: "- fetch_url -> {}")

    def _fake_grade_current_draft(
        _title: str,
        _summary: str,
        _body: str,
        quality_llm_arg: object,
        *,
        is_special_edition: bool = False,  # noqa: ARG001 -- name must match the real callee's keyword arg
    ) -> dict:
        assert quality_llm_arg is quality_llm
        return {
            "grade": 5,
            "issues": [],
            "quality": {
                "model": "llm_rubric",
                "narrative_synthesis": 5,
                "technical_depth": 5,
                "critical_distance": 5,
                "repetition": 5,
                "issues": [],
            },
        }

    monkeypatch.setattr(mc, "_grade_current_draft", _fake_grade_current_draft)
    monkeypatch.setattr(mc, "_collect_fixable_issues", lambda *_a, **_kw: [])

    trace: list = []
    debug: dict = {}
    extra_usage = dict(_ZERO_USAGE)

    payload = mc._run_two_stage_compose(
        research_llm=research_llm,
        llm=writer_llm,
        system="sys",
        user="user prompt",
        research_user=None,
        tool_schemas=[],
        tool_handlers={},
        trace=trace,
        debug=debug,
        checkpoint=lambda *_a, **_kw: None,
        extra_usage=extra_usage,
    )

    assert payload["title"] == "A Title"

    # Ephemeral digest + rubric spend, summed -- the part that was completely
    # invisible before this fix.
    assert extra_usage == _usage(80, 15)

    # research_llm/llm's OWN usage_totals() are tracked separately by the
    # caller (_usage_so_far in _compose_via_writer_tools_locked), not folded
    # into extra_usage -- confirm this function never touches them.
    assert research_llm.usage_totals() == _usage(2000, 300)
    assert writer_llm.usage_totals() == _usage(1000, 200)


# --------------------------------------------------------------------------- #
# Full pipeline: the persisted compose_sessions row (what an admin actually
# reads token totals from) must include research + writer + the ephemeral
# rubric/digest spend, all summed -- and, as a regression check, must be
# UNAFFECTED (no double count, no crash) when nothing needs the accumulator.
# --------------------------------------------------------------------------- #


def _wire_common_compose_mocks(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Shared plumbing every _compose_via_writer_tools test below needs: no real tool loop, no real session store, and a place to capture the FINAL record_compose_session call."""
    monkeypatch.setattr("app.modules.ai.writer_tools.all_tools", lambda **_kw: ([], {}))
    monkeypatch.setattr("app.modules.ai.tool_insights_store.new_session_ref", lambda: ("sid", 0.0))
    captured: dict = {}

    def _fake_record_compose_session(**kwargs: object) -> bool:
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_compose_session",
        _fake_record_compose_session,
    )
    monkeypatch.setattr("app.core.config.WRITER_TOOLS_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_TWO_STAGE", True, raising=False)
    monkeypatch.setattr("app.core.config.RESEARCH_FLOOR_ENABLED", False, raising=False)
    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", False, raising=False)
    monkeypatch.setattr("app.core.config.SPECIAL_EDITION_OUTLINE_ENABLED", False, raising=False)
    monkeypatch.setattr(mc, "_format_research_digest", lambda _t: "- fetch_url -> {}")
    return captured


def test_compose_final_aggregate_includes_rubric_and_digest_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact scenario the 2026-08-28 audit asked to be proven: mock rubric/digest/research/writer each with a DISTINCT usage_totals() and assert the persisted aggregate (what record_compose_session -- and therefore the admin Sessions tab -- sees) includes every one of them."""
    captured = _wire_common_compose_mocks(monkeypatch)

    research_llm = _FakeClient(model="research-model", usage=_usage(2000, 300))
    writer_llm = _FakeClient(model="writer-model", usage=_usage(1000, 200))
    digest_client = _FakeClient(model="digest-model", usage=_usage(50, 10))
    quality_llm = _FakeClient(model="rubric-model", usage=_usage(30, 5))

    monkeypatch.setattr(mc, "get_llm_research_client", lambda **_kw: research_llm)
    monkeypatch.setattr(mc, "get_llm_digest_client", lambda: digest_client)
    monkeypatch.setattr(mc, "get_llm_rubric_client", lambda **_kw: quality_llm)
    monkeypatch.setattr("app.core.config.WRITER_REVIEW_ENABLED", True, raising=False)

    def _fake_grade_current_draft(
        _title: str,
        _summary: str,
        _body: str,
        quality_llm_arg: object,
        *,
        is_special_edition: bool = False,  # noqa: ARG001 -- name must match the real callee's keyword arg
    ) -> dict:
        assert quality_llm_arg is quality_llm
        return {
            "grade": 5,
            "issues": [],
            "quality": {
                "model": "llm_rubric",
                "narrative_synthesis": 5,
                "technical_depth": 5,
                "critical_distance": 5,
                "repetition": 5,
                "issues": [],
            },
        }

    monkeypatch.setattr(mc, "_grade_current_draft", _fake_grade_current_draft)
    monkeypatch.setattr(mc, "_collect_fixable_issues", lambda *_a, **_kw: [])

    mc._compose_via_writer_tools(
        system="sys",
        user="user prompt",
        source_url="https://example.com/usage-accounting",
        llm=writer_llm,
    )

    # research(2000+300) + writer(1000+200) + digest(50+10) + rubric(30+5)
    assert captured["prompt_tokens"] == 2000 + 1000 + 50 + 30
    assert captured["completion_tokens"] == 300 + 200 + 10 + 5
    assert captured["total_tokens"] == 2300 + 1200 + 60 + 35
    assert captured["cached_tokens"] == 0


def test_compose_final_aggregate_unaffected_when_no_ephemeral_clients_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: WRITER_REVIEW_ENABLED off and deepseek raw-mode digest (skips synthesis) mean NO ephemeral rubric/digest client ever runs -- the persisted total must equal research+writer exactly, proving the new extra_usage plumbing doesn't inject phantom tokens or double-count when it has nothing to report."""
    captured = _wire_common_compose_mocks(monkeypatch)

    research_llm = _FakeClient(model="research-model", provider="deepseek", usage=_usage(2000, 300))
    writer_llm = _FakeClient(model="writer-model", usage=_usage(1000, 200))

    monkeypatch.setattr(mc, "get_llm_research_client", lambda **_kw: research_llm)
    monkeypatch.setattr("app.core.config.WRITER_REVIEW_ENABLED", False, raising=False)

    called = {"digest": False, "rubric": False}
    monkeypatch.setattr(mc, "get_llm_digest_client", lambda: called.__setitem__("digest", True))
    monkeypatch.setattr(
        mc, "get_llm_rubric_client", lambda **_kw: called.__setitem__("rubric", True)
    )

    mc._compose_via_writer_tools(
        system="sys",
        user="user prompt",
        source_url="https://example.com/usage-accounting-regression",
        llm=writer_llm,
    )

    assert called == {"digest": False, "rubric": False}
    assert captured["prompt_tokens"] == 2000 + 1000
    assert captured["completion_tokens"] == 300 + 200
    assert captured["total_tokens"] == 2300 + 1200
    assert captured["cached_tokens"] == 0
