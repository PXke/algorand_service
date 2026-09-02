"""Owner-supplied article sources, workers-side compose wiring (docs/newspaper-article-sources-design.md, Phase 1).

Covers: the prompt block builder, the trace-seed chunker, and
compose_scrape_article/_compose_via_writer_tools's forwarding of
`admin_sources` through to both -- the two things design doc section 3
requires to ALWAYS happen together.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

import app.modules.ai.llm_compose as mc
from app.modules.newspaper.admin_source_store import AdminSource


def _source(**overrides: object) -> AdminSource:
    base = {
        "source_id": "22222222-2222-2222-2222-222222222222",
        "added_by": "0xADMIN",
        "label": "Exclusive interview with the Sprout creator, 2026-09-02",
        "kind": "text",
        "attribution_url": "",
        "content": "The creator said the Builder Challenge pays out 25,000 ALGO.",
        "added_at": datetime(2026, 9, 2, tzinfo=UTC),
    }
    base.update(overrides)
    return AdminSource(**base)


# --------------------------------------------------------------------------- #
# _admin_source_prompt_block
# --------------------------------------------------------------------------- #


def test_admin_source_prompt_block_empty_when_no_sources() -> None:
    """No sources attached (the normal case) -> empty string, not an empty section header."""
    assert mc._admin_source_prompt_block(None) == ""
    assert mc._admin_source_prompt_block([]) == ""


def test_admin_source_prompt_block_contains_label_content_and_provenance_framing() -> None:
    """The block carries the exact provenance framing design doc section 3 specifies -- attribution instructions, primary-source framing, and the source's own label/content/added date."""
    block = mc._admin_source_prompt_block([_source()])
    assert "OWNER-SUPPLIED SOURCE MATERIAL" in block
    assert "not on the public web" in block
    assert "primary source" in block
    assert "told PXke Algorand in an interview" in block
    assert "Exclusive interview with the Sprout creator, 2026-09-02" in block
    assert "25,000 ALGO" in block
    assert "2026-09-02" in block  # added-date stamp


def test_admin_source_prompt_block_carries_every_source() -> None:
    """Multiple attached sources all appear, each under its own labeled subsection."""
    block = mc._admin_source_prompt_block(
        [
            _source(label="Interview transcript", content="Alpha fact."),
            _source(label="Follow-up figures", content="Beta fact."),
        ]
    )
    assert "Interview transcript" in block
    assert "Alpha fact." in block
    assert "Follow-up figures" in block
    assert "Beta fact." in block


def test_admin_source_prompt_block_clipped_to_its_own_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clipped as a WHOLE block to ADMIN_SOURCE_PROMPT_MAX_CHARS -- kept separate from LLM_MAX_SOURCE_CHARS so owner material can never crowd out the scrape."""
    monkeypatch.setattr(mc, "ADMIN_SOURCE_PROMPT_MAX_CHARS", 500)
    block = mc._admin_source_prompt_block([_source(content="x" * 5000)])
    assert len(block) <= 500


# --------------------------------------------------------------------------- #
# _seed_admin_source_trace
# --------------------------------------------------------------------------- #


def test_seed_admin_source_trace_noop_on_no_sources() -> None:
    """None and [] both leave the trace untouched."""
    trace: list = []
    mc._seed_admin_source_trace(trace, None)
    mc._seed_admin_source_trace(trace, [])
    assert trace == []


def test_seed_admin_source_trace_uses_the_reserved_tool_name() -> None:
    """`admin_supplied_source` is reserved and must never look like a real tool call (design doc section 0a: no completeness rule accepts it, on purpose)."""
    trace: list = []
    mc._seed_admin_source_trace(trace, [_source(content="short content")])
    assert len(trace) == 1
    assert trace[0]["tool"] == "admin_supplied_source"
    assert (
        trace[0]["arguments"]["label"] == "Exclusive interview with the Sprout creator, 2026-09-02"
    )
    assert trace[0]["arguments"]["added_at"] == "2026-09-02T00:00:00+00:00"
    assert trace[0]["result"]["text"] == "short content"
    assert trace[0]["arguments"]["part"] == "1/1"


def test_seed_admin_source_trace_chunks_at_the_result_max_chars_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source larger than INVESTIGATION_RESULT_MAX_CHARS splits into multiple trace entries with `part N/M` markers, so store_investigation_findings' per-row cap never silently truncates a transcript."""
    monkeypatch.setattr("app.core.config.INVESTIGATION_RESULT_MAX_CHARS", 100)
    content = "a" * 250  # 3 chunks: 100 + 100 + 50
    trace: list = []
    mc._seed_admin_source_trace(trace, [_source(content=content)])

    assert len(trace) == 3
    assert all(e["tool"] == "admin_supplied_source" for e in trace)
    assert [e["arguments"]["part"] for e in trace] == ["1/3", "2/3", "3/3"]
    assert len(trace[0]["result"]["text"]) == 100
    assert len(trace[1]["result"]["text"]) == 100
    assert len(trace[2]["result"]["text"]) == 50
    assert (
        trace[0]["result"]["text"] + trace[1]["result"]["text"] + trace[2]["result"]["text"]
        == content
    )


def test_seed_admin_source_trace_seeds_one_entry_per_source() -> None:
    """Two attached sources each get their own (possibly chunked) run of trace entries."""
    trace: list = []
    mc._seed_admin_source_trace(
        trace,
        [
            _source(label="First", content="alpha"),
            _source(label="Second", content="beta"),
        ],
    )
    labels = [e["arguments"]["label"] for e in trace]
    assert labels == ["First", "Second"]


# --------------------------------------------------------------------------- #
# compose_scrape_article / _compose_via_writer_tools forward admin_sources
# --------------------------------------------------------------------------- #


def test_compose_scrape_article_appends_block_and_forwards_admin_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The two things design doc section 3 requires to happen TOGETHER: the prompt carries the block, and the same admin_sources list rides through to the trace-seeding call (_call_compose_via_writer_tools)."""
    captured: dict = {}

    def _fake_call(**kwargs: object) -> str:
        captured.update(kwargs)
        return "stopped-before-llm"

    monkeypatch.setattr(mc, "_call_compose_via_writer_tools", _fake_call)

    sources = [_source()]
    result = mc.compose_scrape_article(
        service_name="Sprout",
        source_url="https://sproutalgo.com",
        page_title="Sprout",
        page_text="Sprout relaunches on Algorand Python contracts.",
        txid="tx1",
        round_num=0,
        diff=None,
        is_first_snapshot=True,
        admin_sources=sources,
    )
    assert result == "stopped-before-llm"
    assert captured["admin_sources"] is sources
    assert "OWNER-SUPPLIED SOURCE MATERIAL" in captured["user"]
    assert "25,000 ALGO" in captured["user"]
    # Stage-1 research prompt also carries it (same _build_user closure) --
    # the writer should already know about the interview during research,
    # not just at final generation.
    assert "OWNER-SUPPLIED SOURCE MATERIAL" in captured["research_user"]


def test_compose_scrape_article_admin_sources_defaults_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every existing call site (which never passes admin_sources) is unaffected -- no block, admin_sources forwarded as None."""
    captured: dict = {}

    def _fake_call(**kwargs: object) -> str:
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(mc, "_call_compose_via_writer_tools", _fake_call)

    mc.compose_scrape_article(
        service_name="Svc",
        source_url="https://example.com",
        page_title="T",
        page_text="body text",
        txid="tx1",
        round_num=0,
        diff=None,
        is_first_snapshot=True,
    )
    assert captured["admin_sources"] is None
    assert "OWNER-SUPPLIED SOURCE MATERIAL" not in captured["user"]


def test_compose_via_writer_tools_forwards_admin_sources_through_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_compose_via_writer_tools (the compose_lock wrapper) must pass admin_sources through to _compose_via_writer_tools_locked, not drop it at the lock boundary."""
    captured: dict = {}

    def _fake_locked(**kwargs: object) -> str:
        captured.update(kwargs)
        return "locked-ok"

    monkeypatch.setattr(mc, "_compose_via_writer_tools_locked", _fake_locked)
    from collections.abc import Iterator
    from contextlib import contextmanager

    @contextmanager
    def _noop_lock(**_kw: object) -> Iterator[None]:
        yield

    monkeypatch.setattr("app.modules.newspaper.compose_lock.compose_lock", _noop_lock)

    sources = [_source()]
    out = mc._compose_via_writer_tools(
        system="sys",
        user="usr",
        source_url="https://example.com",
        llm=object(),
        admin_sources=sources,
    )
    assert out == "locked-ok"
    assert captured["admin_sources"] is sources
