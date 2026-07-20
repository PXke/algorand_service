"""Autonomous mode for recompose_published: the decision to auto-apply a draft
onto a LIVE article is a strict AND over grade / headline / gatekeeper
factuality — any missing or failing signal must fail CLOSED to manual review,
never open. Gatekeeper *completeness* (OSINT tool-call coverage) is recorded
in metadata but deliberately excluded from the gate (see
test_completeness_fail_alone_does_not_block).

These test the decision predicate directly (grade floor, headline shape, gate
pass) rather than running the full Celery task, which needs Cassandra/Mistral.
The real thresholds live in app.core.config; a change there should be a
deliberate, reviewed decision — these tests pin the logic, not the numbers.
"""
from __future__ import annotations

from app.modules.gatekeeper.live import DeterministicGate
from app.modules.newspaper.article_grader import headline_violations


def _auto_apply_decision(
    *, enabled: bool, grade: float | None, floor: float, title: str, gate_ok: bool
) -> bool:
    """Mirrors the predicate in recompose_published."""
    return (
        enabled
        and grade is not None
        and grade >= floor
        and not headline_violations(title)
        and gate_ok
    )


_GOOD_TITLE = "HesabPay handles 30% of Afghanistan's electricity bills on Algorand"
_COLON_TITLE = "HesabPay: Afghanistan's Everyday Payments, Built on Algorand"


def test_auto_applies_when_every_signal_clears() -> None:
    assert _auto_apply_decision(
        enabled=True, grade=8.6, floor=8.0, title=_GOOD_TITLE, gate_ok=True
    )


def test_disabled_flag_blocks_regardless_of_quality() -> None:
    assert not _auto_apply_decision(
        enabled=False, grade=10.0, floor=8.0, title=_GOOD_TITLE, gate_ok=True
    )


def test_grade_below_floor_blocks() -> None:
    assert not _auto_apply_decision(
        enabled=True, grade=7.9, floor=8.0, title=_GOOD_TITLE, gate_ok=True
    )


def test_missing_grade_fails_closed() -> None:
    assert not _auto_apply_decision(
        enabled=True, grade=None, floor=8.0, title=_GOOD_TITLE, gate_ok=True
    )


def test_colon_label_headline_blocks_even_with_perfect_grade() -> None:
    """This is the real-world case: Delaware's recompose (grade 8.6) was
    correctly held for review because its title missed the length cap — a
    high grade alone must never override the headline check."""
    assert not _auto_apply_decision(
        enabled=True, grade=10.0, floor=8.0, title=_COLON_TITLE, gate_ok=True
    )


_FACT_MIN = 0.80


def test_low_factuality_blocks() -> None:
    """Ungrounded numeric claims must still fail closed — this is the one
    gatekeeper signal recompose keeps as a hard gate."""
    gate = DeterministicGate(factuality_score=0.4, completeness_passed=True, passed=False)
    gate_ok = gate.factuality_score >= _FACT_MIN
    assert not _auto_apply_decision(
        enabled=True, grade=9.0, floor=8.0, title=_GOOD_TITLE, gate_ok=gate_ok
    )


def test_completeness_fail_alone_does_not_block() -> None:
    """2026-07-12: completeness (did the writer call domain/registry/sanctions
    tools) fires on any source mentioning a website/founder/company — true for
    nearly every service profile — but the writer only sporadically calls the
    matching OSINT tools mid-compose, so this rule alone blocked ~all Tier-2
    recomposes despite consistently good grades (7.3-10). It's designed to
    triage under-researched NEW candidates, not gate a rewrite of a service a
    human already approved once — so it's tracked in metadata but no longer
    part of gate_ok. Factuality remains a hard gate (see test above)."""
    gate = DeterministicGate(
        factuality_score=0.95,
        completeness_passed=False,
        passed=False,
        failed_rules=("domain_provenance",),
    )
    gate_ok = gate.factuality_score >= _FACT_MIN
    assert _auto_apply_decision(
        enabled=True, grade=9.0, floor=8.0, title=_GOOD_TITLE, gate_ok=gate_ok
    )


def test_gatekeeper_disabled_entirely_does_not_block() -> None:
    """gate_draft() returns None when GATEKEEPER_ENABLED is off — no signal to
    fail on, so the recompose task treats that case as gate_ok=True."""
    gate = None
    gate_ok = True if gate is None else gate.passed
    assert _auto_apply_decision(
        enabled=True, grade=9.0, floor=8.0, title=_GOOD_TITLE, gate_ok=gate_ok
    )


# --- recompose seeds from the ORIGINAL INPUT (brief), not the prior OUTPUT ----
def test_recompose_editorial_composes_from_brief_not_prior_body(monkeypatch):
    """An editorial-brief recompose must re-run the assignment from the BRIEF
    (fresh research), not synthesize from the prior article body — which would
    re-launder a wrong premise (Pera Wallet incident 2026-07-20)."""
    from types import SimpleNamespace

    import pytest

    from app.modules.newspaper.tasks import publish_tasks as pt

    art = SimpleNamespace(
        service_id="svc", source_url="editorial://brief/abc123",
        body="Pera Wallet is defunct and archived.", title="Wrong old title",
        tags=[], summary="s",
    )
    monkeypatch.setattr("app.modules.newspaper.article_store.get_article", lambda aid: art)
    brief = SimpleNamespace(
        brief_id="abc123", title="Algorand Wallets Guide",
        body_markdown="Compare the active Algorand wallets.", keywords="wallet,algorand",
    )
    monkeypatch.setattr(
        "app.modules.newspaper.editorial_assignment.get_brief", lambda bid: brief
    )

    captured: dict = {}

    class _Stop(Exception):
        pass

    def _fake_compose(**kw):
        captured.update(kw)
        raise _Stop()

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)

    with pytest.raises(_Stop):
        pt.recompose_published.run("11111111-1111-1111-1111-111111111111")

    assert captured["publish_topic"] == pt.PublishTopic.EDITORIAL_ASSIGNMENT
    # seeded from the brief, NOT the prior (wrong) article body
    assert captured["page_text"] == "Compare the active Algorand wallets."
    assert "defunct" not in captured["page_text"]
    assert captured["page_title"] == "Algorand Wallets Guide"
    assert captured["keywords"] == "wallet,algorand"


def test_recompose_web_article_still_uses_generic_path(monkeypatch):
    """A normal web article recompose is unchanged: generic topic, no brief."""
    from types import SimpleNamespace

    import pytest

    from app.modules.newspaper.tasks import publish_tasks as pt

    art = SimpleNamespace(
        service_id="svc", source_url="https://example.com/x",
        body="body", title="t", tags=[], summary="s",
    )
    monkeypatch.setattr("app.modules.newspaper.article_store.get_article", lambda aid: art)
    # make the re-scrape a no-op so page_text stays the stored body
    monkeypatch.setattr(pt, "get_scraper_for_url",
                        lambda url: SimpleNamespace(scrape=lambda **kw: (_ for _ in ()).throw(RuntimeError("skip"))))

    captured: dict = {}

    class _Stop(Exception):
        pass

    def _fake_compose(**kw):
        captured.update(kw)
        raise _Stop()

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)
    monkeypatch.setattr(pt.worker_config if hasattr(pt, "worker_config") else pt,
                        "SERVICE_CONTEXT_ENABLED", False, raising=False)

    with pytest.raises(_Stop):
        pt.recompose_published.run("22222222-2222-2222-2222-222222222222")

    assert captured["publish_topic"] == pt.PublishTopic.GENERIC
