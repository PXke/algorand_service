"""Live deterministic gate: combines completeness + numeric entailment into a
single pass/fail with reviewer-facing reasons."""

from app.modules.gatekeeper.live import GateConfig, quality_proba, run_deterministic_gate


def test_clean_draft_passes() -> None:
    g = run_deterministic_gate(
        source_text="Algorand shipped a parameter update.",
        tool_trace='{"tps": 50000}',
        article_text="The network sustained 50,000 TPS after the update.",
    )
    assert g.passed
    assert g.reasons == ()


def test_ungrounded_figure_fails_factuality() -> None:
    g = run_deterministic_gate(
        source_text="A routine update.",
        tool_trace='{"tps": 50000}',
        article_text="It hit 50,000 TPS serving 999,000 daily users.",
        cfg=GateConfig(fact_min=0.8),
    )
    assert not g.passed
    assert "999,000" in g.ungrounded
    assert any("ungrounded" in r for r in g.reasons)


def test_missing_screen_fails_completeness() -> None:
    g = run_deterministic_gate(
        source_text="Founder Jane Doe unveiled the protocol.",
        tool_trace='{"search_web": "..."}',
        article_text="A neutral writeup of the launch.",
    )
    assert not g.passed
    assert not g.completeness_passed
    assert "human_identity" in g.failed_rules
    # The unscreened person is named in the reason for the reviewer.
    assert any("Jane Doe" in r for r in g.reasons)


def test_as_metadata_shape() -> None:
    g = run_deterministic_gate("s", "{}", "no numbers here")
    md = g.as_metadata()
    assert md["gk_passed"] == "1"
    assert set(md) == {"gk_factuality", "gk_completeness", "gk_passed", "gk_reasons"}


def test_quality_proba_off_by_default(monkeypatch) -> None:
    # A checkpoint existing must never be enough on its own -- the explicit
    # live flag is required, so a training run never silently flips grading.
    monkeypatch.setattr("app.core.config.GATEKEEPER_QUALITY_LIVE", False)
    monkeypatch.setattr("app.core.config.GATEKEEPER_MODEL_PATH", __file__)  # any existing path
    assert quality_proba(title="t", body="b") is None


def test_quality_proba_none_without_checkpoint_even_when_live(monkeypatch) -> None:
    monkeypatch.setattr("app.core.config.GATEKEEPER_QUALITY_LIVE", True)
    monkeypatch.setattr("app.core.config.GATEKEEPER_MODEL_PATH", "/nonexistent/gatekeeper.pt")
    assert quality_proba(title="t", body="b") is None
