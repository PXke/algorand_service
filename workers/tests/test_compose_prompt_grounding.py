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
    # The problem->solution frame must not demand mechanics the research
    # didn't return.
    assert "borrowed from similar products" in mc._NARRATIVE_GUIDANCE


def test_stakes_rule_no_longer_forces_a_rationale(monkeypatch):
    """'Establish the Stakes' used to read 'never announce ... without
    immediately explaining ...', which forced an explanation to exist even
    when no source documented one. Both system prompts must now carry the
    verified-material bound instead."""
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
    assert "never construct a" in captured["system"]
    assert "never announce a technical upgrade" not in captured["system"]


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
