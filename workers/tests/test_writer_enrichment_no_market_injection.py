"""The writer enrichment context never injects a market section on its own."""

from __future__ import annotations

import pytest

from app.modules.newspaper.writer_enrichment.context import WriterEnrichmentBundle
from app.modules.newspaper.writer_enrichment.gather import format_enrichment_for_writer


def test_format_enrichment_never_renders_market_section() -> None:
    """ALGO market context must not be handed to the writer for free — it bypassed the system prompt's own "ALGO PRICE/MARKET RULE" ("fetch and mention ONLY when the metric materially helps THIS story... when in doubt, leave it out") and was invisible to the gatekeeper's numeric- entailment check (root-caused 2026-07-14: correct ALGO price/mcap/volume numbers got flagged as "ungrounded" because they never went through a tool call). The writer can still fetch this itself via the get_algo_market tool when it judges a story genuinely needs it — that path is untouched.

    Even if a "market" key were somehow still present on the bundle (e.g. a
    stale caller), the renderer itself must not surface it.
    """
    bundle = WriterEnrichmentBundle(service_id="svc", phase="discovery")
    bundle.sections["market"] = {
        "available": True,
        "price_usd": 0.0838,
        "change_24h_pct": 1.37,
        "market_cap_usd": 749_800_000,
        "trend_narrative": "ALGO is up this week.",
    }

    block = format_enrichment_for_writer(bundle)

    assert "ALGO market" not in block
    assert "0.0838" not in block
    assert "749,800,000" not in block
    assert "Market trend" not in block


def test_format_enrichment_omits_market_when_absent() -> None:
    """gather_writer_enrichment itself never populates the bundle's market section."""
    bundle = WriterEnrichmentBundle(service_id="svc", phase="discovery")
    block = format_enrichment_for_writer(bundle)
    assert "ALGO market" not in block


def test_gather_writer_enrichment_no_longer_writes_market_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gather_writer_enrichment() itself must not populate bundle.sections['market']."""
    import app.modules.newspaper.writer_enrichment.gather as gather_module

    monkeypatch.setattr(gather_module, "load_intelligence", lambda _service_id: None)
    monkeypatch.setattr(gather_module, "save_intelligence", lambda **_kw: None)
    monkeypatch.setattr(
        gather_module, "collect_internal_context", lambda **_kw: {"prior_articles": 0}
    )
    monkeypatch.setattr(gather_module, "detect_app_store_links", lambda *_a, **_kw: {})
    monkeypatch.setattr(gather_module, "collect_chain_context", lambda **_kw: {})
    monkeypatch.setattr(
        gather_module,
        "collect_social_signals",
        lambda **_kw: {"linked_posts": [], "note": ""},
    )
    monkeypatch.setattr(gather_module, "search_platform_mentions", lambda **_kw: {})
    monkeypatch.setattr(gather_module, "diff_against_stored_intelligence", lambda **_kw: {})
    monkeypatch.setattr(gather_module, "extract_domains_and_urls", lambda _text: ([], []))
    monkeypatch.setattr(
        gather_module, "primary_domain_from_source", lambda _source_url, _page_text: ""
    )
    monkeypatch.setattr(gather_module.config, "WRITER_ENRICHMENT_ENABLED", False)

    bundle = gather_module.gather_writer_enrichment(
        service_id="svc",
        display_name="Svc",
        source_url="https://example.com/",
        page_text="some text",
    )

    assert "market" not in bundle.sections
