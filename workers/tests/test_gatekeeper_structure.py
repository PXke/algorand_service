"""Deterministic Markdown structure heuristics."""

from app.modules.gatekeeper import structure as s


def test_formatting_deserts_flags_wall_of_prose() -> None:
    """Fails when 6 consecutive prose paragraphs run without a heading/list break."""
    md = "\n\n".join(f"Paragraph number {i} with some prose." for i in range(6))
    blocks = s._classify_blocks(md)
    assert not s.formatting_deserts(blocks).passed  # 6 > 4


def test_heading_resets_desert_run() -> None:
    """Passes when a heading breaks up a prose run, keeping the longest streak short."""
    md = "p1\n\np2\n\n## Heading\n\np3\n\np4"
    blocks = s._classify_blocks(md)
    assert s.formatting_deserts(blocks).passed  # longest run is 2


def test_buried_metrics_flags_metric_dump() -> None:
    """Fails when 4 distinct metrics are crammed into a single prose paragraph."""
    md = "The network hit 9400 TPS at 2.9 ms latency with $312 million TVL and 84,000 ALGO staked."
    blocks = s._classify_blocks(md)
    h = s.buried_metrics(blocks)
    assert not h.passed  # 4 distinct metrics in one paragraph


def test_metrics_in_a_table_do_not_count() -> None:
    """Passes when the same metrics appear in a markdown table instead of prose."""
    md = (
        "| Metric | Value |\n| -- | -- |\n| TPS | 9400 |\n"
        "| Latency | 2.9 ms |\n| TVL | $312 million |"
    )
    blocks = s._classify_blocks(md)
    assert s.buried_metrics(blocks).passed  # they're in a table, not prose


def test_hierarchy_jump_fails() -> None:
    """Fails on a heading level skip (h1 to h4) but passes a proper h1-h2-h3 progression."""
    assert not s.hierarchy_integrity("# A\n\n#### D").passed  # h1 -> h4
    assert s.hierarchy_integrity("# A\n\n## B\n\n### C").passed


def test_citation_density() -> None:
    """Fails a sparsely-linked body but passes one with enough links per 100 words."""
    sparse = "word " * 200 + "[only one](http://x)"
    assert not s.citation_density(sparse).passed  # < 1.0 / 100 words
    dense = "[a](u) [b](u) some words here " * 3
    assert s.citation_density(dense).passed


def test_code_block_excluded_from_words_and_headings() -> None:
    """Ignores a "#" line inside a fenced code block when checking heading hierarchy."""
    md = "Intro.\n\n```\n# not a heading\nmore code\n```\n\n## Real Heading"
    # The '# not a heading' inside the fence must not count as an h1.
    assert s.hierarchy_integrity(md).passed


def test_structure_score_and_issues() -> None:
    """Scores a well-structured body 1.0 with no issues, a poorly-structured one lower and flagged."""
    good = "# Title\n\nIntro paragraph with a [link](http://x).\n\n- bullet one\n- bullet two"
    assert s.structure_score(good) == 1.0
    assert s.structure_issues(good) == []
    bad = "# T\n\n#### Deep\n\n" + "9400 TPS, 2.9 ms, $312 million, 84,000 ALGO all here."
    assert s.structure_score(bad) < 1.0
    assert any("Hierarchy" in i for i in s.structure_issues(bad))


def test_report_table_renders() -> None:
    """Renders the structure report as a markdown table with PASS/FAIL rows."""
    out = s.structure_report_markdown("# A\n\nsome text [l](u)")
    assert out.startswith("| Heuristic |")
    assert "PASS" in out or "FAIL" in out
