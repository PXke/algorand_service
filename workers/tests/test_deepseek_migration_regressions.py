"""W1-D: writer regressions surfaced by the DeepSeek migration.

Two fixes on the same compose orchestration (llm_compose.py):

1. The legacy tool-loop's generic ``except Exception`` used to swallow a
   tool/parse failure and fall through to an ungrounded, tool-less
   ``chat_json_object()`` single-shot -- exactly the "ungrounded fallback
   compose" CLAUDE.md invariant #4 forbids (owner decision 2026-07-14). It
   must now re-raise so the caller sees the same failure any other broken
   compose produces, never a quietly-downgraded draft.

2. RESEARCH_DIGEST_MODE=raw / deepseek research skips the LLM-synthesized
   Research Digest entirely, so its output never has a "### Unresolved
   Gaps" section for _extract_unresolved_gaps to find -- silently killing
   _run_digest_gap_fill's safety-net pass for every deepseek-routed
   compose. A cheap, separate gap-extraction call now backfills that
   section in raw mode.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest

from app.modules.ai import llm_compose as mc
from app.modules.ai.llm_provider import LLMProvider


class _FakeRegister:
    """Minimal SessionRegister stand-in -- never touches Cassandra."""

    def new_ref(self) -> tuple[Any, datetime]:
        return uuid4(), datetime.now(UTC)

    def upsert(self, **_kw: object) -> bool:
        return True


def _usage() -> dict[str, int]:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cached_tokens": 0}


class _RaisingToolsClient(LLMProvider):
    """A writer-tier client whose legacy single-loop tool call raises a plain (non-LLMError) exception -- a tool/parse failure, not an API error."""

    provider = "deepseek"
    model = "deepseek-v4-flash"

    def chat_completion(self, *_a: object, **_kw: object) -> str:  # pragma: no cover
        raise AssertionError("not exercised by this test")

    def chat_with_tools(self, *_a: object, **_kw: object) -> str:
        raise ValueError("tool call blew up mid-loop")

    def chat_json_object(self, *_a: object, **_kw: object) -> dict:  # pragma: no cover
        raise AssertionError("must never fall back to an ungrounded chat_json_object() call")

    def usage_totals(self) -> dict[str, int]:
        return _usage()


def test_tool_loop_failure_reraises_and_never_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-LLMError tool/parse failure propagates out of compose -- no article, no ungrounded fallback compose."""
    monkeypatch.setattr("app.core.config.WRITER_TOOLS_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.WRITER_TWO_STAGE", False, raising=False)
    monkeypatch.setattr(
        "app.modules.ai.writer_tools.all_tools",
        lambda **_kw: ([], {}),
    )
    monkeypatch.setattr("app.modules.scraper.core.browser_scrape.maybe_start_session", lambda: None)

    client = _RaisingToolsClient()

    with pytest.raises(ValueError, match="tool call blew up mid-loop"):
        mc._compose_via_writer_tools_locked(
            system="sys",
            user="user prompt",
            source_url="https://example.com/",
            llm=client,
            research_client=client,
            session_register=_FakeRegister(),
        )
    # _RaisingToolsClient.chat_json_object asserts if it's ever called --
    # reaching this line without that AssertionError firing is itself part
    # of the regression coverage.


class _FakeDigestClient:
    """Digest-tier client used only by the raw-mode gap-extraction call."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[str] = []

    def chat_completion(self, messages: list[dict], **_kw: object) -> str:
        self.calls.append(messages[-1]["content"])
        return self._response


def test_raw_mode_digest_gap_fill_runs_for_deepseek(monkeypatch: pytest.MonkeyPatch) -> None:
    """RESEARCH_DIGEST_MODE=raw (deepseek's own path) still produces an '### Unresolved Gaps' section via a cheap extraction call, so _extract_unresolved_gaps has input and _run_digest_gap_fill actually executes its bounded research pass -- previously dead for any deepseek-routed research."""
    trace = [
        {
            "tool": "fetch_url",
            "arguments": {"url": "https://x.example/"},
            "result": {"text": "..."},
        },
    ]
    gaps_client = _FakeDigestClient(
        "### Unresolved Gaps\n- no confirmed recent TVL figure; try lookup_application"
    )
    monkeypatch.setattr(mc, "get_llm_digest_client", lambda: gaps_client)

    digest = mc._synthesize_research_digest(
        trace=trace, research_context="ctx", provider="deepseek"
    )

    assert "### Unresolved Gaps" in digest
    assert "TVL" in digest
    assert len(gaps_client.calls) == 1

    gap_fill_calls: list[dict] = []

    class _ResearchClient:
        provider = "deepseek"

        def chat_with_tools(self, *_a: object, **kw: object) -> str:
            gap_fill_calls.append(kw)
            return ""

    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", True, raising=False)
    result_digest = mc._run_digest_gap_fill(
        _ResearchClient(),
        "sys",
        "stage1 user",
        [],
        {},
        trace,
        {},
        digest,
    )

    # The bounded gap-fill tool round actually ran (it wouldn't have, before
    # this fix, since digest had no Unresolved Gaps section to find).
    assert len(gap_fill_calls) == 1
    # Re-synthesizes afterward using the same raw-mode + gap-extraction path.
    assert "### Unresolved Gaps" in result_digest


def test_raw_mode_digest_gap_fill_skipped_when_extraction_reports_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An honest 'None' from the gap-extraction call still means no gap-fill pass runs -- the fix adds the section, it doesn't force a gap to always be found."""
    trace = [{"tool": "fetch_url", "arguments": {}, "result": {"text": "..."}}]
    gaps_client = _FakeDigestClient("### Unresolved Gaps\n- None")
    monkeypatch.setattr(mc, "get_llm_digest_client", lambda: gaps_client)

    digest = mc._synthesize_research_digest(
        trace=trace, research_context="ctx", provider="deepseek"
    )

    gap_fill_calls: list[dict] = []

    class _ResearchClient:
        provider = "deepseek"

        def chat_with_tools(self, *_a: object, **kw: object) -> str:
            gap_fill_calls.append(kw)
            return ""

    monkeypatch.setattr("app.core.config.DIGEST_GAP_FILL_ENABLED", True, raising=False)
    mc._run_digest_gap_fill(_ResearchClient(), "sys", "stage1 user", [], {}, trace, {}, digest)

    assert gap_fill_calls == []


def test_raw_mode_gap_extraction_failure_is_non_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken digest client on the gap-extraction call degrades to no gap section, not a crashed compose."""
    trace = [{"tool": "fetch_url", "arguments": {}, "result": {"text": "..."}}]

    class _BrokenClient:
        def chat_completion(self, *_a: object, **_kw: object) -> str:
            raise RuntimeError("digest client down")

    monkeypatch.setattr(mc, "get_llm_digest_client", lambda: _BrokenClient())

    digest = mc._synthesize_research_digest(
        trace=trace, research_context="ctx", provider="deepseek"
    )

    assert "### Unresolved Gaps" not in digest
    assert digest.strip()  # raw trace itself still came through
