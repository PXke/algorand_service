"""The DeepSeek raw-mode research digest (_format_full_research_trace): categorized, deduped, error-filtered trace text handed to Stage 2 as ground truth.

Root-caused 2026-09-09 (AlgoChess incident, digest-attention followup): the
prior version was one flat chronological list of every trace entry
(including {"error": ...} noise and exact-duplicate reruns), with a single
8000-char cap for every tool regardless of value. A fabricated on-chain-note
claim slipped through partly because the one real record lookup that
mattered sat at line 143 of 188 undifferentiated lines. These tests pin the
categorization, filtering, and truncation behavior of the fix.
"""

from __future__ import annotations

import app.modules.ai.llm_compose as mc


def _entry(tool: str, result: object, arguments: object | None = None) -> dict:
    return {"tool": tool, "arguments": arguments or {}, "result": result}


def test_empty_trace_returns_empty_string() -> None:
    """No entries at all -- the caller's `if not full_trace.strip(): return ""` short-circuit must still fire."""
    assert mc._format_full_research_trace([]) == ""


def test_all_error_entries_returns_empty_string() -> None:
    """A trace made entirely of errors has nothing usable -- same empty short-circuit as a genuinely empty trace."""
    trace = [_entry("fetch_url", {"error": "dns resolution failed"})]
    assert mc._format_full_research_trace(trace) == ""


def test_error_entries_dropped_but_zero_findings_kept() -> None:
    """A tool ERROR carries no information and is dropped; a genuine ZERO/EMPTY finding (the GoPlausible incident's "0K+ Credentials issued") is real negative evidence and must survive."""
    trace = [
        _entry("fetch_url", {"error": "timeout"}),
        _entry("fetch_url", {"body": "0K+ Credentials issued. 0+ Events & hackathons."}),
    ]
    out = mc._format_full_research_trace(trace)
    assert "timeout" not in out
    assert "0K+ Credentials issued" in out


def test_exact_duplicate_calls_deduped_to_first_occurrence() -> None:
    """The same (tool, arguments) pair called twice collapses to one line -- a rerun adds no new information."""
    trace = [
        _entry("lookup_asset", {"id": 1}, arguments={"asset_id": 1}),
        _entry("lookup_asset", {"id": 1}, arguments={"asset_id": 1}),
    ]
    out = mc._format_full_research_trace(trace)
    assert out.count("lookup_asset(") == 1


def test_same_tool_different_arguments_not_deduped() -> None:
    """Two calls to the same tool with genuinely different arguments are both distinct findings and both survive."""
    trace = [
        _entry("lookup_asset", {"name": "A"}, arguments={"asset_id": 1}),
        _entry("lookup_asset", {"name": "B"}, arguments={"asset_id": 2}),
    ]
    out = mc._format_full_research_trace(trace)
    assert out.count("lookup_asset(") == 2


def test_chain_tools_grouped_under_on_chain_records_section() -> None:
    """A chain_tools.py lookup lands in the ON-CHAIN RECORDS section, not mixed in with web/other findings."""
    trace = [_entry("lookup_transaction_note", {"note": "AC1|duel|0-1|timeout"})]
    out = mc._format_full_research_trace(trace)
    records_section = out.split("### WEB SOURCES EXPLORED")[0]
    assert "### ON-CHAIN RECORDS VERIFIED" in records_section
    assert "lookup_transaction_note(" in records_section


def test_web_tools_grouped_under_web_sources_section() -> None:
    """fetch_url/search_crawled_pages land in WEB SOURCES, separate from on-chain records."""
    trace = [_entry("fetch_url", {"body": "some page text"})]
    out = mc._format_full_research_trace(trace)
    assert "### WEB SOURCES EXPLORED" in out
    web_section = out.split("### WEB SOURCES EXPLORED")[1].split("### OTHER RESEARCH FINDINGS")[0]
    assert "fetch_url(" in web_section


def test_other_tools_grouped_under_other_findings_section() -> None:
    """A tool that is neither a chain lookup nor a web fetch (e.g. search_x) falls into the catch-all OTHER FINDINGS section."""
    trace = [_entry("search_x", {"posts": []})]
    out = mc._format_full_research_trace(trace)
    other_section = out.split("### OTHER RESEARCH FINDINGS")[1]
    assert "search_x(" in other_section


def test_empty_section_renders_explicit_placeholder() -> None:
    """A section with zero findings still renders with an explicit 'none looked up' note -- an empty ON-CHAIN RECORDS section is itself useful negative evidence when at least some other research happened."""
    trace = [_entry("fetch_url", {"body": "page text"})]
    out = mc._format_full_research_trace(trace)
    records_section = out.split("### WEB SOURCES EXPLORED")[0]
    assert "no on-chain record lookups were made" in records_section


def test_record_results_truncated_less_aggressively_than_web_results() -> None:
    """Per-category budgets, not one flat cap: a huge web-page dump is cut harder than an equally-sized on-chain record result."""
    huge_text = "x" * 10_000
    trace = [
        _entry("lookup_transaction_note", {"note": huge_text}),
        _entry("fetch_url", {"body": huge_text}),
    ]
    out = mc._format_full_research_trace(trace)
    records_section = out.split("### WEB SOURCES EXPLORED")[0]
    web_section = out.split("### WEB SOURCES EXPLORED")[1].split("### OTHER RESEARCH FINDINGS")[0]
    assert len(records_section) > len(web_section)


def test_section_order_is_records_then_web_then_other() -> None:
    """Sections are ordered by fabrication risk (records first, closest to the top of the digest), not alphabetically or by trace order."""
    trace = [
        _entry("search_x", {"posts": []}),
        _entry("fetch_url", {"body": "page"}),
        _entry("lookup_transaction_note", {"note": "..."}),
    ]
    out = mc._format_full_research_trace(trace)
    records_i = out.index("### ON-CHAIN RECORDS VERIFIED")
    web_i = out.index("### WEB SOURCES EXPLORED")
    other_i = out.index("### OTHER RESEARCH FINDINGS")
    assert records_i < web_i < other_i


def test_entries_with_no_tool_name_skipped() -> None:
    """A malformed entry with no tool name contributes nothing rather than a blank '- ()' line."""
    trace = [{"tool": "", "arguments": {}, "result": {"x": 1}}]
    assert mc._format_full_research_trace(trace) == ""
