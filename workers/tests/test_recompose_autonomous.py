"""Autonomous mode for recompose_published: the decision to auto-apply a draft onto a LIVE article is a strict AND over grade / headline / gatekeeper factuality — any missing or failing signal must fail CLOSED to manual review, never open. Gatekeeper *completeness* (OSINT tool-call coverage) is recorded in metadata but deliberately excluded from the gate (see test_completeness_fail_alone_does_not_block).

These test the decision predicate directly (grade floor, headline shape, gate
pass) rather than running the full Celery task, which needs Cassandra/Mistral.
The real thresholds live in app.core.config; a change there should be a
deliberate, reviewed decision — these tests pin the logic, not the numbers.
"""

from __future__ import annotations

from typing import Never

import pytest

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
    """Auto-applies when every signal (enabled, grade, headline, gate) clears."""
    assert _auto_apply_decision(enabled=True, grade=8.6, floor=8.0, title=_GOOD_TITLE, gate_ok=True)


def test_disabled_flag_blocks_regardless_of_quality() -> None:
    """The disabled flag blocks auto-apply regardless of how good the other signals are."""
    assert not _auto_apply_decision(
        enabled=False, grade=10.0, floor=8.0, title=_GOOD_TITLE, gate_ok=True
    )


def test_grade_below_floor_blocks() -> None:
    """A grade below the floor blocks auto-apply."""
    assert not _auto_apply_decision(
        enabled=True, grade=7.9, floor=8.0, title=_GOOD_TITLE, gate_ok=True
    )


def test_missing_grade_fails_closed() -> None:
    """A missing grade fails closed and blocks auto-apply."""
    assert not _auto_apply_decision(
        enabled=True, grade=None, floor=8.0, title=_GOOD_TITLE, gate_ok=True
    )


def test_colon_label_headline_blocks_even_with_perfect_grade() -> None:
    """This is the real-world case: Delaware's recompose (grade 8.6) was correctly held for review because its title missed the length cap — a high grade alone must never override the headline check."""
    assert not _auto_apply_decision(
        enabled=True, grade=10.0, floor=8.0, title=_COLON_TITLE, gate_ok=True
    )


_FACT_MIN = 0.80


def test_low_factuality_blocks() -> None:
    """Ungrounded numeric claims must still fail closed — this is the one gatekeeper signal recompose keeps as a hard gate."""
    gate = DeterministicGate(factuality_score=0.4, completeness_passed=True, passed=False)
    gate_ok = gate.factuality_score >= _FACT_MIN
    assert not _auto_apply_decision(
        enabled=True, grade=9.0, floor=8.0, title=_GOOD_TITLE, gate_ok=gate_ok
    )


def test_completeness_fail_alone_does_not_block() -> None:
    """2026-07-12: completeness (did the writer call domain/registry/sanctions tools) fires on any source mentioning a website/founder/company — true for nearly every service profile — but the writer only sporadically calls the matching OSINT tools mid-compose, so this rule alone blocked ~all Tier-2 recomposes despite consistently good grades (7.3-10). It's designed to triage under-researched NEW candidates, not gate a rewrite of a service a human already approved once — so it's tracked in metadata but no longer part of gate_ok. Factuality remains a hard gate (see test above)."""
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
    """gate_draft() returns None when GATEKEEPER_ENABLED is off — no signal to fail on, so the recompose task treats that case as gate_ok=True."""
    gate = None
    gate_ok = True if gate is None else gate.passed
    assert _auto_apply_decision(
        enabled=True, grade=9.0, floor=8.0, title=_GOOD_TITLE, gate_ok=gate_ok
    )


# --- recompose seeds from the ORIGINAL INPUT (brief), not the prior OUTPUT ----
def test_recompose_editorial_composes_from_brief_not_prior_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An editorial-brief recompose must re-run the assignment from the BRIEF (fresh research), not synthesize from the prior article body — which would re-launder a wrong premise (Pera Wallet incident 2026-07-20)."""
    from types import SimpleNamespace

    import pytest

    from app.modules.newspaper.tasks import publish_tasks as pt

    art = SimpleNamespace(
        service_id="svc",
        source_url="editorial://brief/abc123",
        body="Pera Wallet is defunct and archived.",
        title="Wrong old title",
        tags=[],
        summary="s",
    )
    monkeypatch.setattr("app.modules.newspaper.article_store.get_article", lambda _aid: art)
    brief = SimpleNamespace(
        brief_id="abc123",
        title="Algorand Wallets Guide",
        body_markdown="Compare the active Algorand wallets.",
        keywords="wallet,algorand",
        is_special_edition=False,
    )
    monkeypatch.setattr("app.modules.newspaper.editorial_assignment.get_brief", lambda _bid: brief)

    captured: dict = {}

    class _Stop(Exception):
        pass

    def _fake_compose(**kw: object) -> Never:
        captured.update(kw)
        raise _Stop

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)

    with pytest.raises(_Stop):
        pt.recompose_published.run("11111111-1111-1111-1111-111111111111")

    assert captured["publish_topic"] == pt.PublishTopic.EDITORIAL_ASSIGNMENT
    # seeded from the brief, NOT the prior (wrong) article body
    assert captured["page_text"] == "Compare the active Algorand wallets."
    assert "defunct" not in captured["page_text"]
    assert captured["page_title"] == "Algorand Wallets Guide"
    assert captured["keywords"] == "wallet,algorand"


def test_recompose_published_skips_compose_when_a_review_is_already_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-caused 2026-08-13: recompose_published had no dedup against an already-pending review for the same article, so repeated manual/API triggers (admin "Recompose" click, recompose_archive.py) each paid for a full compose and left yet another orphaned unlisted draft -- one real article accumulated 10 of them. Must skip BEFORE composing, same as the normal pipeline's _pending_review_veto."""
    from types import SimpleNamespace

    from app.modules.newspaper.tasks import publish_tasks as pt

    art = SimpleNamespace(
        service_id="svc",
        source_url="https://example.com/x",
        body="body",
        title="t",
        tags=[],
        summary="s",
    )
    monkeypatch.setattr("app.modules.newspaper.article_store.get_article", lambda _aid: art)
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url",
        lambda _url: True,
    )

    def _boom(**_kw: object) -> Never:
        raise AssertionError("must not compose when a review is already pending")

    monkeypatch.setattr(pt, "compose_scrape_article", _boom)

    result = pt.recompose_published.run("33333333-3333-3333-3333-333333333333")
    assert result == {
        "status": "duplicate_review_pending",
        "article_id": "33333333-3333-3333-3333-333333333333",
    }


def test_recompose_published_proceeds_when_the_pending_check_itself_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dedup check fails OPEN: a Cassandra hiccup on has_pending_review_for_url must never block a legitimate recompose."""
    from types import SimpleNamespace

    from app.modules.newspaper.tasks import publish_tasks as pt

    art = SimpleNamespace(
        service_id="svc",
        source_url="https://example.com/x",
        body="body",
        title="t",
        tags=[],
        summary="s",
    )
    monkeypatch.setattr("app.modules.newspaper.article_store.get_article", lambda _aid: art)

    def _raise(_url: str) -> Never:
        raise ConnectionError("cassandra down")

    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url", _raise
    )
    monkeypatch.setattr(
        pt,
        "get_scraper_for_url",
        lambda _url: SimpleNamespace(
            scrape=lambda **_kw: (_ for _ in ()).throw(RuntimeError("skip"))
        ),
    )

    captured: dict = {}

    class _Stop(Exception):
        pass

    def _fake_compose(**kw: object) -> Never:
        captured.update(kw)
        raise _Stop

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)
    monkeypatch.setattr(
        pt.worker_config if hasattr(pt, "worker_config") else pt,
        "SERVICE_CONTEXT_ENABLED",
        False,
        raising=False,
    )

    with pytest.raises(_Stop):
        pt.recompose_published.run("44444444-4444-4444-4444-444444444444")

    assert captured  # compose was reached despite the check erroring


def test_recompose_web_article_still_uses_generic_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A normal web article recompose is unchanged: generic topic, no brief."""
    from types import SimpleNamespace

    import pytest

    from app.modules.newspaper.tasks import publish_tasks as pt

    art = SimpleNamespace(
        service_id="svc",
        source_url="https://example.com/x",
        body="body",
        title="t",
        tags=[],
        summary="s",
    )
    monkeypatch.setattr("app.modules.newspaper.article_store.get_article", lambda _aid: art)
    # make the re-scrape a no-op so page_text stays the stored body
    monkeypatch.setattr(
        pt,
        "get_scraper_for_url",
        lambda _url: SimpleNamespace(
            scrape=lambda **_kw: (_ for _ in ()).throw(RuntimeError("skip"))
        ),
    )

    captured: dict = {}

    class _Stop(Exception):
        pass

    def _fake_compose(**kw: object) -> Never:
        captured.update(kw)
        raise _Stop

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)
    monkeypatch.setattr(
        pt.worker_config if hasattr(pt, "worker_config") else pt,
        "SERVICE_CONTEXT_ENABLED",
        False,
        raising=False,
    )

    with pytest.raises(_Stop):
        pt.recompose_published.run("22222222-2222-2222-2222-222222222222")

    assert captured["publish_topic"] == pt.PublishTopic.GENERIC
    # Root-caused live 2026-08-17: with no first_coverage AND no real diff, an
    # archive-refresh recompose got no "give a comprehensive picture" guidance
    # at all and gravitated toward whatever felt newest in the source material
    # (a Downbad.farm recompose wrote almost entirely about one newly-
    # previewed feature despite fetching material on the site's full feature
    # set). first_coverage=True gives it the same "comprehensive, standalone
    # picture" instruction a genuinely first-time compose gets.
    assert captured["first_coverage"] is True


def test_extra_source_material_is_folded_into_page_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """extra_source_material (source-URL-dedup cleanup, 2026-08-17).

    Retiring sibling-article content must reach the writer as clearly-labeled extra material,
    not silently be lost the moment those sibling rows get deleted after this recompose lands.
    """
    from types import SimpleNamespace

    import pytest

    from app.modules.newspaper.tasks import publish_tasks as pt

    art = SimpleNamespace(
        service_id="svc",
        source_url="https://example.com/x",
        body="live page body",
        title="t",
        tags=[],
        summary="s",
    )
    monkeypatch.setattr("app.modules.newspaper.article_store.get_article", lambda _aid: art)
    monkeypatch.setattr(
        pt,
        "get_scraper_for_url",
        lambda _url: SimpleNamespace(
            scrape=lambda **_kw: (_ for _ in ()).throw(RuntimeError("skip"))
        ),
    )
    monkeypatch.setattr("app.core.config.SERVICE_CONTEXT_ENABLED", False)

    captured: dict = {}

    class _Stop(Exception):
        pass

    def _fake_compose(**kw: object) -> Never:
        captured.update(kw)
        raise _Stop

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)

    with pytest.raises(_Stop):
        pt.recompose_published.run(
            "33333333-3333-3333-3333-333333333333",
            extra_source_material="A retiring sibling article's own distinct fact.",
        )

    assert "live page body" in captured["page_text"]
    assert "A retiring sibling article's own distinct fact." in captured["page_text"]
    assert "RETIRING PRIOR COVERAGE" in captured["page_text"]


def test_blank_extra_source_material_leaves_page_text_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No existing caller (admin Recompose click, weekly cadence, ...) ever passes this kwarg.

    Must be a true no-op, not even an empty appended section.
    """
    from types import SimpleNamespace

    import pytest

    from app.modules.newspaper.tasks import publish_tasks as pt

    art = SimpleNamespace(
        service_id="svc",
        source_url="https://example.com/x",
        body="live page body",
        title="t",
        tags=[],
        summary="s",
    )
    monkeypatch.setattr("app.modules.newspaper.article_store.get_article", lambda _aid: art)
    monkeypatch.setattr(
        pt,
        "get_scraper_for_url",
        lambda _url: SimpleNamespace(
            scrape=lambda **_kw: (_ for _ in ()).throw(RuntimeError("skip"))
        ),
    )
    monkeypatch.setattr("app.core.config.SERVICE_CONTEXT_ENABLED", False)

    captured: dict = {}

    class _Stop(Exception):
        pass

    def _fake_compose(**kw: object) -> Never:
        captured.update(kw)
        raise _Stop

    monkeypatch.setattr(pt, "compose_scrape_article", _fake_compose)

    with pytest.raises(_Stop):
        pt.recompose_published.run("55555555-5555-5555-5555-555555555555")

    assert captured["page_text"] == "live page body"


def _wire_common_recompose_published_mocks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shared plumbing for the two auto-apply-ordering tests below.

    Wires a live article, a successful compose, and every downstream
    storage/tagging call so the task runs end to end without touching
    Cassandra/Redis/an LLM.
    """
    from types import SimpleNamespace

    from app.modules.newspaper.article_composer import ArticleComposeResult
    from app.modules.newspaper.tasks import publish_tasks as pt

    art = SimpleNamespace(
        service_id="hesabpay",
        source_url="https://hesabpay.com",
        body="live page body",
        title="old title",
        tags=[],
        summary="s",
    )
    monkeypatch.setattr("app.modules.newspaper.article_store.get_article", lambda _aid: art)
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url",
        lambda _url: False,
    )
    monkeypatch.setattr(
        pt,
        "get_scraper_for_url",
        lambda _url: SimpleNamespace(
            scrape=lambda **_kw: (_ for _ in ()).throw(RuntimeError("skip"))
        ),
    )
    monkeypatch.setattr("app.core.config.SERVICE_CONTEXT_ENABLED", False)

    composed = ArticleComposeResult(
        title="HesabPay handles 30% of Afghanistan's electricity bills on Algorand",
        summary="s",
        body="new body",
        composer="mistral",
    )
    monkeypatch.setattr(pt, "compose_scrape_article", lambda **_kw: composed)

    class _FakeSession:
        def execute(self, *_a: object, **_kw: object) -> _FakeSession:
            return self

        def one(self) -> None:
            return None

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession())
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.insert_stored_article",
        lambda **_kw: ("dddddddd-dddd-dddd-dddd-dddddddddddd", True),
    )
    monkeypatch.setattr(pt, "_grade_and_gate", lambda *_a, **_kw: ({}, 9.0, True))
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.enqueue_classifier_review",
        lambda **_kw: "rid-99",
    )


def test_recompose_published_auto_apply_applies_before_marking_review_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Store before mark (CLAUDE.md invariant #2).

    apply_recomposed_article must run and succeed BEFORE
    complete_classifier_review marks the review resolved -- not after. The
    old ordering closed the review as "auto_approved" first, so an apply
    failure right after (draft_or_live_missing / replace_failed) left the
    live article never actually updated with no trail back to a human --
    the review already showed resolved.
    """
    from app.modules.newspaper.tasks import publish_tasks as pt

    _wire_common_recompose_published_mocks(monkeypatch)

    call_order: list[str] = []

    def _fake_apply(draft_id: str, article_id: str) -> dict[str, str]:
        call_order.append("apply")
        assert draft_id == "dddddddd-dddd-dddd-dddd-dddddddddddd"
        assert article_id == "66666666-6666-6666-6666-666666666666"
        return {"status": "ok", "article_id": article_id}

    def _fake_complete(review_id: str, *, resolution: str) -> None:
        call_order.append("complete")
        assert review_id == "rid-99"
        assert resolution == "auto_approved"

    monkeypatch.setattr(pt, "apply_recomposed_article", _fake_apply)
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.complete_classifier_review",
        _fake_complete,
    )

    result = pt.recompose_published.run("66666666-6666-6666-6666-666666666666")

    assert call_order == ["apply", "complete"]
    assert result == {
        "status": "auto_applied",
        "review_id": "rid-99",
        "draft_article_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "apply_result": "ok",
    }


def test_recompose_published_auto_apply_failure_leaves_review_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The review must not be marked auto_approved when the apply itself fails.

    When apply_recomposed_article does not report status "ok" (e.g. the live
    article vanished between lookup and apply), the review stays open for a
    human, and the task reports apply_failed rather than pretending the
    recompose landed.
    """
    from app.modules.newspaper.tasks import publish_tasks as pt

    _wire_common_recompose_published_mocks(monkeypatch)

    def _fake_apply(draft_id: str, article_id: str) -> dict[str, str]:  # noqa: ARG001
        return {"status": "error", "reason": "replace_failed"}

    def _boom_complete(*_a: object, **_kw: object) -> None:
        raise AssertionError("complete_classifier_review must not run on a failed apply")

    monkeypatch.setattr(pt, "apply_recomposed_article", _fake_apply)
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.complete_classifier_review",
        _boom_complete,
    )

    result = pt.recompose_published.run("66666666-6666-6666-6666-666666666666")

    assert result == {
        "status": "apply_failed",
        "review_id": "rid-99",
        "draft_article_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "apply_result": "error",
    }
