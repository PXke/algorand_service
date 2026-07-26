"""Grounding rules added after the rug.ninja draft (2026-07-08): the writer transplanted a competitor launchpad's redemption feature (a GluedLaunch search snippet) onto the subject, invented a "same price for every buyer" guarantee when the official docs were a TBD stub, and relayed token devs' self-published claims as fact. These tests pin the prompt text that guards each failure so a future prompt refactor can't silently drop it."""

from types import SimpleNamespace

import pytest

import app.modules.ai.mistral_compose as mc


def test_tools_guidance_has_cross_product_and_gap_rules() -> None:
    """Research/single-stage passes: no lookalike-product transplants, no reconstructed mechanics, self-published claims attributed."""
    assert "ONE PRODUCT, ONE SOURCE" in mc._TOOLS_GUIDANCE
    assert "UNDOCUMENTED MECHANICS" in mc._TOOLS_GUIDANCE
    assert "SELF-PUBLISHED CLAIMS" in mc._TOOLS_GUIDANCE


def test_research_phase_inherits_grounding_rules() -> None:
    """The rules sit before the SELF-REVIEW split, so the two-stage research pass keeps them."""
    assert "ONE PRODUCT, ONE SOURCE" in mc._RESEARCH_PHASE_GUIDANCE
    assert "SELF-PUBLISHED CLAIMS" in mc._RESEARCH_PHASE_GUIDANCE
    assert "NAMED REAL-WORLD ASSET CLAIMS" in mc._RESEARCH_PHASE_GUIDANCE


def test_tools_guidance_scrutinizes_named_asset_ownership_claims() -> None:
    """The GEO World Energy draft (2026-07-14): the writer wrote up a claim of fractional ownership of specific, real, government/utility-owned dams (Xiluodu, Belo Monte, Grand Coulee, Robert-Bourassa) as fact, even though its own research turned up the project's own tweet calling it a 'play2earn game' — a tell that the 'ownership' language was gamified branding, not a real asset-backed claim."""
    assert "NAMED REAL-WORLD ASSET CLAIMS" in mc._TOOLS_GUIDANCE
    assert "play2earn" in mc._TOOLS_GUIDANCE
    assert "extraordinary claim" in mc._TOOLS_GUIDANCE


def test_narrative_guidance_carries_grounding_to_stage_two() -> None:
    """Stage 2 (warm generation) is a fresh conversation WITHOUT _TOOLS_GUIDANCE — the digest hands it raw search snippets as ground truth, which is exactly where the GluedLaunch cross-contamination happened. The grounding rules must ride in via _NARRATIVE_GUIDANCE."""
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


def test_stakes_rule_allows_algorand_expert_knowledge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thin sources must not block layer-1 explanation — use protocol expertise, not invented partnerships or quotes."""
    captured = {}

    def _fake_via_tools(**kwargs: object) -> mc.MistralArticleFields:
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


def test_first_coverage_allows_honest_doc_gaps(monkeypatch: pytest.MonkeyPatch) -> None:
    """First-coverage introductions must be told to report missing docs as missing, not reconstruct the product's mechanics."""
    captured = {}

    def _fake_via_tools(**kwargs: object) -> mc.MistralArticleFields:
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


def test_writer_prompt_bans_pr_fluff_and_narrative_bullets(monkeypatch: pytest.MonkeyPatch) -> None:
    """The system prompt bans marketing-speak, bulleted narrative sections, and unsafe JSON formatting."""
    captured = {}

    def _fake_via_tools(**kwargs: object) -> mc.MistralArticleFields:
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


def test_narrative_guidance_anchors_length_to_facts() -> None:
    """Stage 2 guidance ties word count to the volume of verified facts, not a fixed target range."""
    assert "Scale the word count strictly to the volume of verified facts" in mc._NARRATIVE_GUIDANCE
    assert "400-600" not in mc._NARRATIVE_GUIDANCE


def test_research_digest_synthesis_schema() -> None:
    """The digest-synthesis prompt asks for Verified Facts and Verbatim Quotes sections."""
    assert "Verified Facts" in mc._RESEARCH_DIGEST_SYNTHESIS
    assert "Verbatim Quotes" in mc._RESEARCH_DIGEST_SYNTHESIS
    assert "READY" not in mc._RESEARCH_PHASE_GUIDANCE


def test_research_digest_synthesis_carries_chart_verbatim() -> None:
    """Root-caused 2026-07-14: a chart_data tool call succeeded (returned a valid markdown_fence) during research, but the digest-synthesis prompt had no section for it — none of Verified Facts/Verbatim Quotes/Liveness Signals/Numeric Conversions/Unresolved Gaps fit a code-fence artifact, so the digest LLM silently dropped it and the published article had no chart at all despite the tool succeeding. Stage 2 has no tools and works ONLY from the digest, so this section is the only way a chart can survive into the final body."""
    assert "### Chart" in mc._RESEARCH_DIGEST_SYNTHESIS
    assert "markdown_fence" in mc._RESEARCH_DIGEST_SYNTHESIS
    assert "VERBATIM" in mc._RESEARCH_DIGEST_SYNTHESIS


def test_narrative_guidance_tells_stage2_to_paste_chart_verbatim() -> None:
    """Stage 2 guidance instructs pasting a digest's chart section verbatim into the article."""
    assert "CHART" in mc._NARRATIVE_GUIDANCE
    assert "### Chart" in mc._NARRATIVE_GUIDANCE
    assert "paste it VERBATIM" in mc._NARRATIVE_GUIDANCE


def test_research_phase_truncation_discipline() -> None:
    """The research-phase prompt tells the model to use continue_reading rather than a raw start_char offset."""
    assert "TRUNCATION DISCIPLINE" in mc._RESEARCH_PHASE_GUIDANCE
    assert "continue_reading=true" in mc._RESEARCH_PHASE_GUIDANCE
    assert "start_char" not in mc._RESEARCH_PHASE_GUIDANCE


def test_stage2_digest_only_no_tools() -> None:
    """Stage 2 guidance states the model has no tools and must work only from the Research Digest."""
    assert "NO tools" in mc._STAGE2_GENERATION_GUIDANCE
    assert "Research Digest" in mc._STAGE2_GENERATION_GUIDANCE


def test_stage2_expert_knowledge_carveout_excludes_business_facts() -> None:
    """Root-caused 2026-07-15: a draft invented '0.001 ALGO per transfer' as a marketplace-SPECIFIC fee (the network's real ~0.001 ALGO minimum fee is fine to cite generally; claiming it as one marketplace's own fee is not) and separately asserted, unverified, that named marketplaces specifically implement ASA-parameter-based royalty enforcement (ARC-18) when the Research Digest never confirmed this for any of them. NOTE: ARC-18 is a real Algorand standard — it genuinely repurposes an ASA's Clawback/ Freeze/Manager fields to route transfers through a separate Royalty Enforcer application; it is opt-in and bypassable, not fake and not an automatic built-in ASA field. An earlier version of this fix incorrectly called it 'nonexistent', which was itself wrong and has been corrected — the rule is about verifying WHICH project uses it, not denying the mechanism exists. Stage 2's unqualified 'use your expert knowledge of Algorand layer-1 mechanics' permission was being stretched from 'explain general protocol behavior' to 'invent this specific project's specific business facts'. The carve-out must draw that line explicitly."""
    assert "GENERAL protocol behavior" in mc._STAGE2_GENERATION_GUIDANCE
    assert "never use that" in mc._STAGE2_GENERATION_GUIDANCE.lower()
    assert "fee schedule" in mc._STAGE2_GENERATION_GUIDANCE
    assert "opt-in and can be" in mc._STAGE2_GENERATION_GUIDANCE
    assert "nonexistent" not in mc._STAGE2_GENERATION_GUIDANCE.lower()


def test_technical_stakes_bridges_algorand_layer1() -> None:
    """The writing guidelines mention layer-1 architecture, legacy friction, and Pure Proof-of-Stake."""
    guidelines = mc._writing_guidelines("2026-07-09")
    assert "layer-1 architecture" in guidelines
    assert "legacy friction" in guidelines
    assert "Pure Proof-of-Stake" in guidelines


def test_technical_stakes_bridge_is_relevance_gated() -> None:
    """Root-caused 2026-07-18 on the live D13.co article: the old 'if the source lacks technical depth you MUST use expert knowledge to explain theoretical mechanisms' wording made the writer bolt state proofs onto a wallet-phishing post-mortem (state proofs don't fix key theft) — the grader's technical_depth dimension then rewarded exactly that filler. Same failure produced the UNDP/Stellar piece's speculative 'where Algorand fits' padding. The bridge must be relevance-gated: skip it when no mechanic is genuinely implicated, never manufacture one."""
    guidelines = mc._writing_guidelines("2026-07-09")
    assert "RELEVANCE GATES THE BRIDGE" in guidelines
    assert "state proofs do not fix wallet phishing" in guidelines
    assert "skip the bridge rather than" in guidelines
    # The expert-knowledge license survives (thin sources still get depth —
    # see test_stakes_rule_allows_algorand_expert_knowledge) but the
    # always-bridge MANDATE is gone: explaining is permitted, not required.
    assert "MUST use your expert knowledge" not in guidelines
    assert "expert knowledge of Algorand" in guidelines


def test_writing_guidelines_bans_cross_section_restatement() -> None:
    """Root-caused 2026-07-15 on a real NFT-marketplace article: 'fees are undisclosed' and 'AlgoSeas volume is self-reported/unverified' each showed up in the per-subject prose, the comparison table, a bulleted 'why this matters' analysis, AND a reader-guidance section — four independent re-derivations of the same two facts. The existing 'state the challenge once' rule didn't catch this because it's scoped to criticism of a project's shortcomings, not general factual restatement across a table/bullets/guidance structure."""
    guidelines = mc._writing_guidelines("2026-07-09")
    assert "No restatement across sections" in guidelines
    assert "ONCE, in the section it belongs to" in guidelines


def test_narrative_guidance_requires_data_table() -> None:
    """Stage 2 guidance requires a data presentation table with Concept/Real-World Implication columns."""
    assert "DATA PRESENTATION" in mc._NARRATIVE_GUIDANCE
    assert "Concept' and 'Real-World Implication'" in mc._NARRATIVE_GUIDANCE
    assert "expert knowledge of Algorand layer-1" in mc._NARRATIVE_GUIDANCE


def test_build_stage2_user_omits_tool_trace() -> None:
    """Builds the Stage 2 user prompt from the digest and notes the model cannot call tools."""
    user = mc._build_stage2_user(user="base", digest="## Verified Facts\n- fact")
    assert "Research Digest" in user
    assert "cannot call tools" in user
    assert "fact" in user


def test_research_digest_synthesis_asks_for_unresolved_gaps() -> None:
    """The digest-synthesis prompt requires an Unresolved Gaps section, with a literal "None" when empty."""
    assert "Unresolved Gaps" in mc._RESEARCH_DIGEST_SYNTHESIS
    assert "write exactly: None" in mc._RESEARCH_DIGEST_SYNTHESIS


def test_narrative_guidance_bans_facts_outside_digest() -> None:
    """The nf.domains incident: the writer stage added two fabricated sales that weren't even in the (already-fabricated) digest. Stage 2 must be told the Digest is the ceiling, not just a floor, for specific facts."""
    assert "not already in the Research Digest" in mc._NARRATIVE_GUIDANCE
    assert "however plausible or familiar it feels" in mc._NARRATIVE_GUIDANCE


def test_narrative_guidance_names_fees_as_undocumented_product_specifics() -> None:
    """Same 2026-07-15 incident as the Stage-2 carve-out fix — the existing 'product-specific mechanism is undocumented, say so plainly' rule was real but abstract enough that the model didn't recognize a marketplace's fee schedule/royalty enforcement as an instance of it. Pin the concrete example so it can't be missed."""
    assert "fee percentage" in mc._NARRATIVE_GUIDANCE
    assert "royalty standard it enforces" in mc._NARRATIVE_GUIDANCE
    assert "fees are undisclosed" in mc._NARRATIVE_GUIDANCE


def test_tools_guidance_bans_memory_as_source() -> None:
    """Both the tools and research-phase prompts state the model's parametric memory is not a valid source."""
    assert "MEMORY IS NOT A SOURCE" in mc._TOOLS_GUIDANCE
    assert "MEMORY IS NOT A SOURCE" in mc._RESEARCH_PHASE_GUIDANCE


class TestExtractUnresolvedGaps:
    """Extracting the digest's Unresolved Gaps section for the revision prompt."""

    def test_none_returns_empty(self) -> None:
        """None input returns an empty string."""
        digest = "## Research Digest\n\n### Unresolved Gaps\n- None\n"
        assert mc._extract_unresolved_gaps(digest) == ""

    def test_missing_section_returns_empty(self) -> None:
        """A digest missing the Unresolved Gaps section returns an empty string."""
        digest = "## Research Digest\n\n### Verified Facts\n- a fact\n"
        assert mc._extract_unresolved_gaps(digest) == ""

    def test_real_gaps_extracted(self) -> None:
        """Real gap bullets under Unresolved Gaps are extracted."""
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

    def test_stops_at_next_heading(self) -> None:
        """Extraction stops at the next section heading."""
        digest = (
            "### Unresolved Gaps\n- the on-chain app ID is missing\n\n"
            "### Numeric Conversions\n- irrelevant trailing section\n"
        )
        gaps = mc._extract_unresolved_gaps(digest)
        assert "app ID" in gaps
        assert "Numeric Conversions" not in gaps
        assert "irrelevant trailing section" not in gaps


def test_title_rule_avoids_leading_headline_with_unflattering_number() -> None:
    """The CompX clAMM draft (2026-07-14): the writer led the HEADLINE itself with '$2.28K TVL' — factually honest, but it turned a legitimate feature launch into a headline about how small the project still is. The title rule's "prefer a specific verified number" clause needs an exception for small/unflattering numbers, distinct from NUMERIC HONESTY (which governs the body, not headline choice). _ARTICLE_FORMAT_RULES feeds the shared `system` prompt used by both the single-stage and Stage 2 (gen_system = system + _STAGE2_GENERATION_GUIDANCE) paths, so one rule here covers both — no _NARRATIVE_GUIDANCE duplication needed, unlike _TOOLS_GUIDANCE-only rules."""
    assert "tiny TVL" in mc._ARTICLE_FORMAT_RULES
    assert "do NOT make" in mc._ARTICLE_FORMAT_RULES
    assert "lead with what actually happened" in mc._ARTICLE_FORMAT_RULES


def test_writing_guidelines_are_honest_but_empathetic() -> None:
    """Same CompX incident as the title-exception test above: the body itself repeated the tiny-TVL framing in nearly every section, piling on a small early-stage team rather than just stating the challenge once. This is a body-tone rule (separate from the headline-specific fix), so it lives in _writing_guidelines — shared system prompt, no duplication needed."""
    guidelines = mc._writing_guidelines("2026-07-14")
    assert "Honest but empathetic" in guidelines
    assert "not to humiliate a small team" in guidelines


def test_research_completeness_rule_reaches_both_phases() -> None:
    """The CompX incident (2026-07-14): the research phase's own first web search already surfaced a directly relevant lead (canix402-api.compx.io, CompX's separate x402-protocol paid API product) but the model never followed up on it — a research-thoroughness gap, not a missing tool. Must reach both _TOOLS_GUIDANCE (single-stage/legacy) and _RESEARCH_PHASE_GUIDANCE (two-stage research pass) since it's inserted before the SELF-REVIEW split marker both derive from."""
    assert "RESEARCH COMPLETENESS" in mc._TOOLS_GUIDANCE
    assert "DISTINCT product, subdomain, or URL" in mc._TOOLS_GUIDANCE
    assert "RESEARCH COMPLETENESS" in mc._RESEARCH_PHASE_GUIDANCE
    assert "DISTINCT product, subdomain, or URL" in mc._RESEARCH_PHASE_GUIDANCE


def test_gap_fill_nudge_forbids_recall_and_names_gaps() -> None:
    """The gap-fill nudge names the specific unresolved gap and forbids guessing/recalling facts to fill it."""
    nudge = mc._gap_fill_nudge("- missing the real sale price")
    assert "missing the real sale price" in nudge
    assert "do NOT guess or recall" in nudge.lower() or "NOT guess or recall" in nudge
    assert "unresolved" in nudge.lower()


def test_stage2_forbids_nonverbatim_quotation_marks() -> None:
    """Root-caused 2026-07-16: the RandGallery article attributed an invented phrase to the Goanna Council inside quotation marks — the phrase existed nowhere in the trace or the supplied announcement. Quotation marks are a verbatim claim; the prompt must say so explicitly (the deterministic quote_gate then enforces it — see test_quote_gate.py)."""
    assert "QUOTATION MARKS ARE A VERBATIM CLAIM" in mc._STAGE2_GENERATION_GUIDANCE
    assert "word-for-word" in mc._STAGE2_GENERATION_GUIDANCE
    assert "paraphrase WITHOUT" in mc._STAGE2_GENERATION_GUIDANCE


def test_writing_guidelines_forbid_the_boilerplate_lede() -> None:
    """2026-07-16: every recent article opened with the identical PPoS/ finality/fees paragraph — cross-article repetition the per-article rubric can't see. The guidelines must tell the writer to lead with the story-specific tension and keep protocol mechanics mid-piece."""
    text = mc._writing_guidelines("2026-07-16")
    assert "Vary your lede" in text
    assert "standard" in text
    assert "layer-1 pitch" in text
    assert "specific to THIS story" in text


def test_guidance_composed_from_named_sections_not_string_split() -> None:
    """Refactor 2026-07-16 (owner task #32): _RESEARCH_PHASE_GUIDANCE used to be derived by splitting _TOOLS_GUIDANCE at the literal text 'SELF-REVIEW (MANDATORY' — a rename of that heading would have silently changed which rules reach the research pass. Both prompts now compose named section constants; this pins that every section lands where intended."""
    for section in (
        mc._RESEARCH_MISSION_AND_ROUTING,
        mc._VERIFICATION_DISCIPLINE,
        mc._METRICS_DISCIPLINE,
        mc._SOURCING_AND_FRAMING_RULES,
        mc._NO_FABRICATION,
        mc._FEEDBACK_CHANNELS,
    ):
        assert section in mc._TOOLS_GUIDANCE
        assert section in mc._RESEARCH_PHASE_GUIDANCE
    assert mc._SELF_REVIEW_RULES in mc._TOOLS_GUIDANCE
    assert mc._SELF_REVIEW_RULES not in mc._RESEARCH_PHASE_GUIDANCE
    assert mc._RESEARCH_PHASE_ADDENDUM in mc._RESEARCH_PHASE_GUIDANCE
    assert mc._RESEARCH_PHASE_ADDENDUM not in mc._TOOLS_GUIDANCE


def test_writing_guidelines_reject_diff_noise_as_news() -> None:
    """2026-07-18 quantum-rebrand draft: a canonical-tag capitalization tweak was written up as one of 'three substantive updates', complete with 'normalizing capitalization in line with the Foundation's branding' — mechanical diff artifacts dressed as reporting."""
    guidelines = mc._writing_guidelines("2026-07-09")
    assert "Diff noise is not news" in guidelines
    assert "canonical" in guidelines
