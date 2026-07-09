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


def test_narrative_guidance_carries_grounding_to_stage_two():
    """Stage 2 (warm generation) is a fresh conversation WITHOUT
    _TOOLS_GUIDANCE — the digest hands it raw search snippets as ground truth,
    which is exactly where the GluedLaunch cross-contamination happened. The
    grounding rules must ride in via _NARRATIVE_GUIDANCE."""
    assert "GROUNDING RULES" in mc._NARRATIVE_GUIDANCE
    assert "competitor products" in mc._NARRATIVE_GUIDANCE
    assert "undocumented" in mc._NARRATIVE_GUIDANCE
    assert "transplanted onto the story's subject" in mc._NARRATIVE_GUIDANCE


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
