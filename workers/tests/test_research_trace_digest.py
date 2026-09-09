"""The DeepSeek raw-mode research digest (_format_full_research_trace): categorized, deduped trace text handed to Stage 2 as ground truth.

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


def test_all_error_entries_produces_a_failed_fetches_only_digest() -> None:
    """Root-cause regression (2026-09-09, design review before shipping): errors used to be dropped entirely, which silently dropped a fetch_url DNS failure's "hint" field too -- the MyAlgo incident's DEFUNCT-domain instruction. A trace of nothing but errors must still produce a real (non-empty) digest, with those errors visible in FAILED FETCHES, not fall back to the empty-trace short-circuit."""
    trace = [_entry("fetch_url", {"error": "dns resolution failed"})]
    out = mc._format_full_research_trace(trace)
    assert out != ""
    assert "### FAILED FETCHES" in out
    assert "dns resolution failed" in out


def test_error_entry_hint_field_survives_into_failed_fetches() -> None:
    """The DEFUNCT-domain "hint" field (research_tools._fetch_failure_hint, the MyAlgo incident's actual fix) must reach the writer, not just the bare error string."""
    trace = [
        _entry(
            "fetch_url",
            {
                "url": "https://myalgo.com",
                "error": "dns resolution failed",
                "hint": "this domain does not resolve — the project is likely DEFUNCT or abandoned",
            },
        )
    ]
    out = mc._format_full_research_trace(trace)
    failed_section = out.split("### FAILED FETCHES")[1]
    assert "DEFUNCT" in failed_section


def test_error_entries_kept_separately_and_zero_findings_still_survive() -> None:
    """A tool ERROR is kept (in FAILED FETCHES), not dropped; a genuine ZERO/EMPTY finding (the GoPlausible incident's "0K+ Credentials issued") is real negative evidence and must survive in its normal section, unaffected."""
    trace = [
        _entry("fetch_url", {"error": "timeout"}),
        _entry("fetch_url", {"body": "0K+ Credentials issued. 0+ Events & hackathons."}),
    ]
    out = mc._format_full_research_trace(trace)
    assert "timeout" in out
    assert "### FAILED FETCHES" in out
    assert "0K+ Credentials issued" in out
    # The zero-finding must NOT be miscategorized into FAILED FETCHES.
    web_section = out.split("### WEB SOURCES EXPLORED")[1].split("### OTHER RESEARCH FINDINGS")[0]
    assert "0K+ Credentials issued" in web_section


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


def test_section_order_is_records_interactive_web_other_failed() -> None:
    """Sections are ordered by fabrication risk (records first, closest to the top of the digest), not alphabetically or by trace order."""
    trace = [
        _entry("search_x", {"posts": []}),
        _entry("fetch_url", {"body": "page"}),
        _entry("lookup_transaction_note", {"note": "..."}),
        _entry("play_interactive", {"screen": "board"}),
        _entry("fetch_url", {"error": "timeout"}),
    ]
    out = mc._format_full_research_trace(trace)
    records_i = out.index("### ON-CHAIN RECORDS VERIFIED")
    interactive_i = out.index("### PRODUCT/INTERACTIVE EXPERIENCE EXPLORED")
    web_i = out.index("### WEB SOURCES EXPLORED")
    other_i = out.index("### OTHER RESEARCH FINDINGS")
    failed_i = out.index("### FAILED FETCHES")
    assert records_i < interactive_i < web_i < other_i < failed_i


def test_entries_with_no_tool_name_skipped() -> None:
    """A malformed entry with no tool name contributes nothing rather than a blank '- ()' line."""
    trace = [{"tool": "", "arguments": {}, "result": {"x": 1}}]
    assert mc._format_full_research_trace(trace) == ""


def test_interactive_tools_grouped_under_their_own_section() -> None:
    """play_interactive/capture_screenshot/click_element/type_into_page get their own section, separate from passive web fetches -- deliberate product exploration, not a generic page dump."""
    trace = [
        _entry("play_interactive", {"screen": "board state"}),
        _entry("capture_screenshot", {"description": "the play screen"}),
        _entry("fetch_url", {"body": "unrelated page text"}),
    ]
    out = mc._format_full_research_trace(trace)
    interactive_section = out.split("### PRODUCT/INTERACTIVE EXPERIENCE EXPLORED")[1].split(
        "### WEB SOURCES EXPLORED"
    )[0]
    assert "play_interactive(" in interactive_section
    assert "capture_screenshot(" in interactive_section
    assert "fetch_url(" not in interactive_section


def test_interactive_results_get_the_generous_record_tier_budget() -> None:
    """Root-cause regression (2026-09-09, design review before shipping): interactive-tool results used to share the small 2000-char WEB budget, directly undercutting THE SCENE INCLUDES THE PRODUCT ITSELF (a writer can't weight product-experience material the digest itself already cut down). Interactive results now get the same generous budget as on-chain records."""
    huge_text = "x" * 10_000
    trace = [
        _entry("play_interactive", {"screen": huge_text}),
        _entry("fetch_url", {"body": huge_text}),
    ]
    out = mc._format_full_research_trace(trace)
    interactive_section = out.split("### PRODUCT/INTERACTIVE EXPERIENCE EXPLORED")[1].split(
        "### WEB SOURCES EXPLORED"
    )[0]
    web_section = out.split("### WEB SOURCES EXPLORED")[1].split("### OTHER RESEARCH FINDINGS")[0]
    assert len(interactive_section) > len(web_section)


def test_failed_retry_after_error_with_same_args_keeps_both_entries() -> None:
    """Root-cause regression (2026-09-09, found while testing the error-visibility fix): a failed fetch_url call retried with the SAME url/args, succeeding the second time, must keep BOTH entries -- deduping on (tool, args) alone treated the successful retry as a "duplicate" of the failure and silently dropped the one result that actually matters."""
    trace = [
        _entry("fetch_url", {"error": "timeout"}, arguments={"url": "https://example.com"}),
        _entry(
            "fetch_url", {"body": "the real page content"}, arguments={"url": "https://example.com"}
        ),
    ]
    out = mc._format_full_research_trace(trace)
    assert "timeout" in out
    assert "the real page content" in out
