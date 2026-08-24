"""Live deterministic gate: combines completeness + numeric entailment into a single pass/fail with reviewer-facing reasons."""

import pytest

from app.modules.gatekeeper import live as gk_live
from app.modules.gatekeeper.live import (
    GateConfig,
    gate_draft,
    run_deterministic_gate,
)


def test_clean_draft_passes() -> None:
    """A draft whose figures are grounded in the tool trace passes with no failure reasons."""
    g = run_deterministic_gate(
        source_text="Algorand shipped a parameter update.",
        tool_trace='{"tps": 50000}',
        article_text="The network sustained 50,000 TPS after the update.",
    )
    assert g.passed
    assert g.reasons == ()


def test_ungrounded_figure_fails_factuality() -> None:
    """A numeric figure absent from the tool trace fails factuality and is listed as ungrounded."""
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
    """A named human identity introduced in the source but never screened fails completeness, naming the person."""
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


def test_url_mention_alone_no_longer_fails_completeness() -> None:
    """A source merely containing a URL (its own boilerplate, nearly every scraped page) must not fail completeness -- domain_provenance's old trigger-word rule was removed 2026-08-21 because it matched almost everything; the real dead-domain check now lives in gate_draft, not run_deterministic_gate's pure core."""
    g = run_deterministic_gate(
        source_text="The team announced it at https://example.io today.",
        tool_trace="{}",
        article_text="A neutral writeup.",
    )
    assert g.completeness_passed
    assert "domain_provenance" not in g.failed_rules


def test_company_backing_failure_does_not_list_unscreened_names() -> None:
    """company_backing failing alone must not surface named_persons_unscreened's list -- that detail belongs to human_identity, and attaching it to an unrelated rule misleads the reviewer (found 2026-08-07 on a held Polkagold review row, whose reasons named marketing bullet phrases as if they were unscreened people)."""
    g = run_deterministic_gate(
        source_text="The team runs it as a registered company ltd.",
        tool_trace="{}",
        article_text="A neutral writeup.",
    )
    assert not g.completeness_passed
    assert "company_backing" in g.failed_rules
    assert "human_identity" not in g.failed_rules
    assert not any("(" in r for r in g.reasons)


def test_as_metadata_shape() -> None:
    """as_metadata() returns exactly the expected gk_* string-keyed fields."""
    g = run_deterministic_gate("s", "{}", "no numbers here")
    md = g.as_metadata()
    assert md["gk_passed"] == "1"
    assert set(md) == {
        "gk_factuality",
        "gk_completeness",
        "gk_passed",
        "gk_reasons",
        "gk_dead_domains",
    }


# ------------------------------------------------------- dead-domain check
#
# domain_provenance's old trigger-word rule (removed above) tried to answer
# "is this domain legit" from source_text alone and mostly just matched url-ish
# boilerplate. The real safety property the owner wants -- an article can
# never present a confirmed-dead domain as current, prose or link, that would
# be a disaster (the MyAlgo/Pera Wallet defunct-entity incidents) -- lives
# here instead, checked against domain_tracking + a live DNS fallback.


def _patch_domain_status(monkeypatch: pytest.MonkeyPatch, statuses: dict[str, dict]) -> None:
    import app.modules.crawler.domain_tracker as dt

    monkeypatch.setattr(dt, "get_domain_status", lambda d: statuses.get(d))


def _patch_resolves(monkeypatch: pytest.MonkeyPatch, alive: set[str]) -> None:
    import app.modules.newspaper.defunct_entity_gate as deg

    monkeypatch.setattr(deg, "_resolves", lambda host: host in alive)


def test_dead_domain_suppressed_in_tracking_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A domain domain_tracking has suppressed as dead (unexpired dead_project_until) is flagged without any live DNS call."""
    _patch_domain_status(
        monkeypatch,
        {"deadproject.xyz": {"metadata": {"dead_project_until": "2099-01-01T00:00:00+00:00"}}},
    )

    def _boom(_host: str) -> bool:
        raise AssertionError("must not do a live check for an already-tracked domain")

    monkeypatch.setattr("app.modules.newspaper.defunct_entity_gate._resolves", _boom)
    dead = gk_live._dead_domains_referenced("See https://deadproject.xyz for details.")
    assert dead == ["deadproject.xyz"]


def test_expired_suppression_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A domain whose dead_project_until window has already passed is not flagged -- suppress_dead_project_domain is a cooldown, not a permanent verdict, and a project can come back."""
    _patch_domain_status(
        monkeypatch,
        {"revived.xyz": {"metadata": {"dead_project_until": "2020-01-01T00:00:00+00:00"}}},
    )
    dead = gk_live._dead_domains_referenced("See https://revived.xyz for details.")
    assert dead == []


def test_tracked_domain_with_no_dead_flag_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A domain_tracking row with no dead_project_until at all is treated as alive -- no live DNS call needed, it's already known-good."""
    _patch_domain_status(monkeypatch, {"perawallet.app": {"metadata": {}}})
    dead = gk_live._dead_domains_referenced("See https://perawallet.app for details.")
    assert dead == []


def test_never_tracked_domain_falls_back_to_live_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """A domain domain_tracking has never seen at all gets a live DNS check via defunct_entity_gate's own hardened resolver."""
    _patch_domain_status(monkeypatch, {})
    _patch_resolves(monkeypatch, alive=set())  # nothing resolves
    dead = gk_live._dead_domains_referenced("Compare to the old wallet at https://deadwallet.io.")
    assert dead == ["deadwallet.io"]


def test_never_tracked_domain_that_resolves_is_not_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """A never-tracked domain that resolves fine is not flagged."""
    _patch_domain_status(monkeypatch, {})
    _patch_resolves(monkeypatch, alive={"perawallet.app"})
    dead = gk_live._dead_domains_referenced("See https://perawallet.app for details.")
    assert dead == []


def test_prose_only_mention_is_still_caught(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike defunct_entity_gate's linked-domain scan, a bare domain mentioned in prose with no markdown link is still checked -- this is the coverage gap this check exists to close."""
    _patch_domain_status(monkeypatch, {})
    _patch_resolves(monkeypatch, alive=set())
    dead = gk_live._dead_domains_referenced(
        "Unlike deadwallet.io, which shut down in 2023, this new wallet is thriving."
    )
    assert dead == ["deadwallet.io"]


def test_source_domain_itself_is_never_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The article's own source domain is skipped even if it would otherwise resolve as dead -- it was just scraped successfully moments ago, so a stale suppression flag from a past session must not self-flag."""
    _patch_domain_status(
        monkeypatch,
        {"example.io": {"metadata": {"dead_project_until": "2099-01-01T00:00:00+00:00"}}},
    )
    dead = gk_live._dead_domains_referenced(
        "See https://example.io for details.", source_domain="example.io"
    )
    assert dead == []


def test_gate_draft_hard_fails_on_dead_domain_reference(monkeypatch: pytest.MonkeyPatch) -> None:
    """gate_draft folds a dead-domain hit into the returned gate: passed flips to False and the domain is listed, even when factuality/completeness both cleared."""
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENABLED", True)
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENFORCE", True)
    monkeypatch.setattr(
        "app.modules.newspaper.investigation_store.load_investigation_trace", lambda _sid: "{}"
    )
    _patch_domain_status(monkeypatch, {})
    _patch_resolves(monkeypatch, alive=set())

    gate = gate_draft(
        source_text="A routine update.",
        article_text="Unlike deadwallet.io, this new wallet is thriving.",
        service_id="svc-1",
        source_url="https://newwallet.app",
    )
    assert gate is not None
    assert not gate.passed
    assert gate.dead_domains == ("deadwallet.io",)
    assert any("deadwallet.io" in r for r in gate.reasons)


def test_gate_draft_passes_when_no_domains_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    """gate_draft's normal pass/fail is untouched when no referenced domain is dead."""
    monkeypatch.setattr("app.core.config.GATEKEEPER_ENABLED", True)
    monkeypatch.setattr(
        "app.modules.newspaper.investigation_store.load_investigation_trace", lambda _sid: "{}"
    )
    _patch_domain_status(monkeypatch, {})
    _patch_resolves(monkeypatch, alive={"perawallet.app"})

    gate = gate_draft(
        source_text="A routine update.",
        article_text="See https://perawallet.app for details.",
        service_id="svc-1",
        source_url="https://newwallet.app",
    )
    assert gate is not None
    assert gate.passed
    assert gate.dead_domains == ()
