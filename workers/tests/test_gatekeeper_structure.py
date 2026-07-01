"""Deterministic Markdown structure heuristics."""

from app.modules.gatekeeper import structure as s


def test_formatting_deserts_flags_wall_of_prose() -> None:
    md = "\n\n".join(f"Paragraph number {i} with some prose." for i in range(6))
    blocks = s._classify_blocks(md)
    assert not s.formatting_deserts(blocks).passed  # 6 > 4


def test_heading_resets_desert_run() -> None:
    md = "p1\n\np2\n\n## Heading\n\np3\n\np4"
    blocks = s._classify_blocks(md)
    assert s.formatting_deserts(blocks).passed  # longest run is 2


def test_buried_metrics_flags_metric_dump() -> None:
    md = "The network hit 9400 TPS at 2.9 ms latency with $312 million TVL and 84,000 ALGO staked."
    blocks = s._classify_blocks(md)
    h = s.buried_metrics(blocks)
    assert not h.passed  # 4 distinct metrics in one paragraph


def test_metrics_in_a_table_do_not_count() -> None:
    md = (
        "| Metric | Value |\n| -- | -- |\n| TPS | 9400 |\n"
        "| Latency | 2.9 ms |\n| TVL | $312 million |"
    )
    blocks = s._classify_blocks(md)
    assert s.buried_metrics(blocks).passed  # they're in a table, not prose


def test_hierarchy_jump_fails() -> None:
    assert not s.hierarchy_integrity("# A\n\n#### D").passed  # h1 -> h4
    assert s.hierarchy_integrity("# A\n\n## B\n\n### C").passed


def test_citation_density() -> None:
    sparse = "word " * 200 + "[only one](http://x)"
    assert not s.citation_density(sparse).passed  # < 1.0 / 100 words
    dense = "[a](u) [b](u) some words here " * 3
    assert s.citation_density(dense).passed


def test_code_block_excluded_from_words_and_headings() -> None:
    md = "Intro.\n\n```\n# not a heading\nmore code\n```\n\n## Real Heading"
    # The '# not a heading' inside the fence must not count as an h1.
    assert s.hierarchy_integrity(md).passed


def test_structure_score_and_issues() -> None:
    good = "# Title\n\nIntro paragraph with a [link](http://x).\n\n- bullet one\n- bullet two"
    assert s.structure_score(good) == 1.0
    assert s.structure_issues(good) == []
    bad = "# T\n\n#### Deep\n\n" + "9400 TPS, 2.9 ms, $312 million, 84,000 ALGO all here."
    assert s.structure_score(bad) < 1.0
    assert any("Hierarchy" in i for i in s.structure_issues(bad))


def test_report_table_renders() -> None:
    out = s.structure_report_markdown("# A\n\nsome text [l](u)")
    assert out.startswith("| Heuristic |")
    assert "PASS" in out or "FAIL" in out
