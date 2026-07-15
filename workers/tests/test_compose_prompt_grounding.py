"""Grounding rules added after the rug.ninja draft (2026-07-08): the writer
transplanted a competitor launchpad's redemption feature (a GluedLaunch search
snippet) onto the subject, invented a "same price for every buyer" guarantee
when the official docs were a TBD stub, and relayed token devs' self-published
claims as fact. These tests pin the prompt text that guards each failure so a
future prompt refactor can't silently drop it.
"""

from types import SimpleNamespace

import app.modules.ai.mistral_compose as mc


def test_tools_guidance_has_cross_product_and_gap_rules():
    """Research/single-stage passes: no lookalike-product transplants, no
    reconstructed mechanics, self-published claims attributed."""
    assert "ONE PRODUCT, ONE SOURCE" in mc._TOOLS_GUIDANCE
    assert "UNDOCUMENTED MECHANICS" in mc._TOOLS_GUIDANCE
    assert "SELF-PUBLISHED CLAIMS" in mc._TOOLS_GUIDANCE


def test_research_phase_inherits_grounding_rules():
    """The rules sit before the SELF-REVIEW split, so the two-stage research
    pass keeps them."""
    assert "ONE PRODUCT, ONE SOURCE" in mc._RESEARCH_PHASE_GUIDANCE
    assert "SELF-PUBLISHED CLAIMS" in mc._RESEARCH_PHASE_GUIDANCE
    assert "NAMED REAL-WORLD ASSET CLAIMS" in mc._RESEARCH_PHASE_GUIDANCE


def test_tools_guidance_scrutinizes_named_asset_ownership_claims():
    """The GEO World Energy draft (2026-07-14): the writer wrote up a claim
    of fractional ownership of specific, real, government/utility-owned dams
    (Xiluodu, Belo Monte, Grand Coulee, Robert-Bourassa) as fact, even though
    its own research turned up the project's own tweet calling it a
    'play2earn game' — a tell that the 'ownership' language was gamified
    branding, not a real asset-backed claim."""
    assert "NAMED REAL-WORLD ASSET CLAIMS" in mc._TOOLS_GUIDANCE
    assert "play2earn" in mc._TOOLS_GUIDANCE
    assert "extraordinary claim" in mc._TOOLS_GUIDANCE


def test_narrative_guidance_carries_grounding_to_stage_two():
    """Stage 2 (warm generation) is a fresh conversation WITHOUT
    _TOOLS_GUIDANCE — the digest hands it raw search snippets as ground truth,
    which is exactly where the GluedLaunch cross-contamination happened. The
    grounding rules must ride in via _NARRATIVE_GUIDANCE."""
    assert "GROUNDING RULES" in mc._NARRATIVE_GUIDANCE
    assert "competitor products" in mc._NARRATIVE_GUIDANCE
    assert "undocumented" in mc._NARRATIVE_GUIDANCE
    assert "transplanted onto the story's subject" in mc._NARRATIVE_GUIDANCE
    # 2026-07-14: adding a rule to _TOOLS_GUIDANCE alone does NOT reach the
    # pass that actually writes the article — _NARRATIVE_GUIDANCE must carry
    # its own copy, same as SELF-PUBLISHED CLAIMS/ONE PRODUCT ONE SOURCE
    # above. Caught this gap in the NAMED REAL-WORLD ASSET CLAIMS rule itself
    # right after adding it — see test_tools_guidance_scrutinizes_named_asset_
    # ownership_claims for the _TOOLS_GUIDANCE half.
    assert "extraordinary claim" in mc._NARRATIVE_GUIDANCE
    assert "play2earn" in mc._NARRATIVE_GUIDANCE


def test_stakes_rule_allows_algorand_expert_knowledge(monkeypatch):
    """Thin sources must not block layer-1 explanation — use protocol expertise,
    not invented partnerships or quotes."""
    captured = {}

    def _fake_via_tools(**kwargs):
        captured.update(kwargs)
        return mc.MistralArticleFields(title="t", summary="s", body="b")

    monkeypatch.setattr(mc, "_compose_via_writer_tools", _fake_via_tools)
    mc.compose_scrape_article_mistral(
        service_name="Example",
        source_url="https://example.com/",
        page_title="example",
        page_text="Algorand tooling page " * 30,
        txid="tx",
        round_num=1,
        diff=None,
        is_first_snapshot=True,
        client=SimpleNamespace(),
    )
    system = captured["system"]
    assert "expert knowledge of Algorand" in system
    assert "Do not invent false quotes" in system
    assert "never announce a technical upgrade" not in system


def test_first_coverage_allows_honest_doc_gaps(monkeypatch):
    """First-coverage introductions must be told to report missing docs as
    missing, not reconstruct the product's mechanics."""
    captured = {}

    def _fake_via_tools(**kwargs):
        captured.update(kwargs)
        return mc.MistralArticleFields(title="t", summary="s", body="b")

    monkeypatch.setattr(mc, "_compose_via_writer_tools", _fake_via_tools)
    mc.compose_scrape_article_mistral(
        service_name="Example",
        source_url="https://example.com/",
        page_title="example",
        page_text="Algorand tooling page " * 30,
        txid="tx",
        round_num=1,
        diff="+++ a\n+ x\n+ y\n+ z\n",
        is_first_snapshot=False,
        first_coverage=True,
        client=SimpleNamespace(),
    )
    assert "FIRST COVERAGE MODE" in captured["system"]
    assert "reconstructing how it must work" in captured["system"]


def test_writer_prompt_bans_pr_fluff_and_narrative_bullets(monkeypatch):
    captured = {}

    def _fake_via_tools(**kwargs):
        captured.update(kwargs)
        return mc.MistralArticleFields(title="t", summary="s", body="b")

    monkeypatch.setattr(mc, "_compose_via_writer_tools", _fake_via_tools)
    mc.compose_scrape_article_mistral(
        service_name="Example",
        source_url="https://example.com/",
        page_title="example",
        page_text="Algorand tooling page " * 30,
        txid="tx",
        round_num=1,
        diff=None,
        is_first_snapshot=True,
        client=SimpleNamespace(),
    )
    system = captured["system"]
    assert "BANNED PHRASES" in system
    assert "groundbreaking" in system
    assert "strictly prohibited from using bulleted lists" in system
    assert "Concept' and the 'Real-World Implication'" in system
    assert "JSON SAFETY" in system
    assert "comma-separated sentences" in system


def test_narrative_guidance_anchors_length_to_facts():
    assert "Scale the word count strictly to the volume of verified facts" in mc._NARRATIVE_GUIDANCE
    assert "400-600" not in mc._NARRATIVE_GUIDANCE


def test_research_digest_synthesis_schema():
    assert "Verified Facts" in mc._RESEARCH_DIGEST_SYNTHESIS
    assert "Verbatim Quotes" in mc._RESEARCH_DIGEST_SYNTHESIS
    assert "READY" not in mc._RESEARCH_PHASE_GUIDANCE


def test_research_digest_synthesis_carries_chart_verbatim():
    """Root-caused 2026-07-14: a chart_data tool call succeeded (returned a
    valid markdown_fence) during research, but the digest-synthesis prompt
    had no section for it — none of Verified Facts/Verbatim Quotes/Liveness
    Signals/Numeric Conversions/Unresolved Gaps fit a code-fence artifact, so
    the digest LLM silently dropped it and the published article had no
    chart at all despite the tool succeeding. Stage 2 has no tools and works
    ONLY from the digest, so this section is the only way a chart can survive
    into the final body."""
    assert "### Chart" in mc._RESEARCH_DIGEST_SYNTHESIS
    assert "markdown_fence" in mc._RESEARCH_DIGEST_SYNTHESIS
    assert "VERBATIM" in mc._RESEARCH_DIGEST_SYNTHESIS


def test_narrative_guidance_tells_stage2_to_paste_chart_verbatim():
    assert "CHART" in mc._NARRATIVE_GUIDANCE
    assert "### Chart" in mc._NARRATIVE_GUIDANCE
    assert "paste it VERBATIM" in mc._NARRATIVE_GUIDANCE


def test_research_phase_truncation_discipline():
    assert "TRUNCATION DISCIPLINE" in mc._RESEARCH_PHASE_GUIDANCE
    assert "continue_reading=true" in mc._RESEARCH_PHASE_GUIDANCE
    assert "start_char" not in mc._RESEARCH_PHASE_GUIDANCE


def test_stage2_digest_only_no_tools():
    assert "NO tools" in mc._STAGE2_GENERATION_GUIDANCE
    assert "Research Digest" in mc._STAGE2_GENERATION_GUIDANCE


def test_technical_stakes_bridges_algorand_layer1():
    guidelines = mc._writing_guidelines("2026-07-09")
    assert "layer-1 architecture" in guidelines
    assert "legacy friction" in guidelines
    assert "Pure Proof-of-Stake" in guidelines


def test_narrative_guidance_requires_data_table():
    assert "DATA PRESENTATION" in mc._NARRATIVE_GUIDANCE
    assert "Concept' and 'Real-World Implication'" in mc._NARRATIVE_GUIDANCE
    assert "expert knowledge of Algorand layer-1" in mc._NARRATIVE_GUIDANCE


def test_build_stage2_user_omits_tool_trace():
    user = mc._build_stage2_user(user="base", digest="## Verified Facts\n- fact")
    assert "Research Digest" in user
    assert "cannot call tools" in user
    assert "fact" in user


def test_research_digest_synthesis_asks_for_unresolved_gaps():
    assert "Unresolved Gaps" in mc._RESEARCH_DIGEST_SYNTHESIS
    assert "write exactly: None" in mc._RESEARCH_DIGEST_SYNTHESIS


def test_narrative_guidance_bans_facts_outside_digest():
    """The nf.domains incident: the writer stage added two fabricated sales
    that weren't even in the (already-fabricated) digest. Stage 2 must be
    told the Digest is the ceiling, not just a floor, for specific facts."""
    assert "not already in the Research Digest" in mc._NARRATIVE_GUIDANCE
    assert "however plausible or familiar it feels" in mc._NARRATIVE_GUIDANCE


def test_tools_guidance_bans_memory_as_source():
    assert "MEMORY IS NOT A SOURCE" in mc._TOOLS_GUIDANCE
    assert "MEMORY IS NOT A SOURCE" in mc._RESEARCH_PHASE_GUIDANCE


class TestExtractUnresolvedGaps:
    def test_none_returns_empty(self):
        digest = "## Research Digest\n\n### Unresolved Gaps\n- None\n"
        assert mc._extract_unresolved_gaps(digest) == ""

    def test_missing_section_returns_empty(self):
        digest = "## Research Digest\n\n### Verified Facts\n- a fact\n"
        assert mc._extract_unresolved_gaps(digest) == ""

    def test_real_gaps_extracted(self):
        digest = (
            "## Research Digest\n\n"
            "### Verified Facts\n- a fact\n\n"
            "### Unresolved Gaps\n"
            "- No real recent sales data found for the marketplace; a tool "
            "fetch of the analytics/sales-history page could confirm.\n"
        )
        gaps = mc._extract_unresolved_gaps(digest)
        assert "recent sales data" in gaps
        assert "Verified Facts" not in gaps

    def test_stops_at_next_heading(self):
        digest = (
            "### Unresolved Gaps\n- the on-chain app ID is missing\n\n"
            "### Numeric Conversions\n- irrelevant trailing section\n"
        )
        gaps = mc._extract_unresolved_gaps(digest)
        assert "app ID" in gaps
        assert "Numeric Conversions" not in gaps
        assert "irrelevant trailing section" not in gaps


def test_title_rule_avoids_leading_headline_with_unflattering_number():
    """The CompX clAMM draft (2026-07-14): the writer led the HEADLINE itself
    with '$2.28K TVL' — factually honest, but it turned a legitimate feature
    launch into a headline about how small the project still is. The title
    rule's "prefer a specific verified number" clause needs an exception for
    small/unflattering numbers, distinct from NUMERIC HONESTY (which governs
    the body, not headline choice). _ARTICLE_FORMAT_RULES feeds the shared
    `system` prompt used by both the single-stage and Stage 2 (gen_system =
    system + _STAGE2_GENERATION_GUIDANCE) paths, so one rule here covers both
    — no _NARRATIVE_GUIDANCE duplication needed, unlike _TOOLS_GUIDANCE-only
    rules."""
    assert "tiny TVL" in mc._ARTICLE_FORMAT_RULES
    assert "do NOT make" in mc._ARTICLE_FORMAT_RULES
    assert "lead with what actually happened" in mc._ARTICLE_FORMAT_RULES


def test_writing_guidelines_are_honest_but_empathetic():
    """Same CompX incident as the title-exception test above: the body itself
    repeated the tiny-TVL framing in nearly every section, piling on a small
    early-stage team rather than just stating the challenge once. This is a
    body-tone rule (separate from the headline-specific fix), so it lives in
    _writing_guidelines — shared system prompt, no duplication needed."""
    guidelines = mc._writing_guidelines("2026-07-14")
    assert "Honest but empathetic" in guidelines
    assert "not to humiliate a small team" in guidelines


def test_research_completeness_rule_reaches_both_phases():
    """The CompX incident (2026-07-14): the research phase's own first web
    search already surfaced a directly relevant lead (canix402-api.compx.io,
    CompX's separate x402-protocol paid API product) but the model never
    followed up on it — a research-thoroughness gap, not a missing tool.
    Must reach both _TOOLS_GUIDANCE (single-stage/legacy) and
    _RESEARCH_PHASE_GUIDANCE (two-stage research pass) since it's inserted
    before the SELF-REVIEW split marker both derive from."""
    assert "RESEARCH COMPLETENESS" in mc._TOOLS_GUIDANCE
    assert "DISTINCT product, subdomain, or URL" in mc._TOOLS_GUIDANCE
    assert "RESEARCH COMPLETENESS" in mc._RESEARCH_PHASE_GUIDANCE
    assert "DISTINCT product, subdomain, or URL" in mc._RESEARCH_PHASE_GUIDANCE


def test_gap_fill_nudge_forbids_recall_and_names_gaps():
    nudge = mc._gap_fill_nudge("- missing the real sale price")
    assert "missing the real sale price" in nudge
    assert "do NOT guess or recall" in nudge.lower() or "NOT guess or recall" in nudge
    assert "unresolved" in nudge.lower()
