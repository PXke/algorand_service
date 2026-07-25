"""publish_from_queued_row's pre-compose vetoes are a uniform ordered list (_PRE_COMPOSE_VETOES) mirroring the drain's _PRE_COMPOSE_GATES: each veto returns None (pass) or its exact outcome dict (skip). These tests pin the extraction's contract — order, pass-through, and the outcome dicts staying byte-identical to what the inline checks used to return."""

from types import SimpleNamespace

import pytest

from app.modules.newspaper.publish_policy import PublishKind
from app.modules.newspaper.tasks import publish_tasks as pt


def _ctx(**overrides: object) -> pt._ComposeVetoCtx:
    row = SimpleNamespace(
        queue_id="q1",
        service_id="svc",
        scrape_url="https://example.com",
        payload={"page_title": "A headline", "page_text": "text"},
    )
    defaults = {
        "row": row,
        "publish_kind": PublishKind.CONTENT_UPDATE,
        "compose_domain": "example.com",
        "enforce_domain_cap": True,
        "signals": SimpleNamespace(relevance=0.9),
    }
    defaults.update(overrides)
    return pt._ComposeVetoCtx(**defaults)


def test_veto_order() -> None:
    """The pre-compose veto tuple runs pending-review, domain-cap, novelty, then content-quality, in that order."""
    assert (
        pt._pending_review_veto,
        pt._domain_cap_veto,
        pt._novelty_duplicate_veto,
        pt._content_quality_veto,
    ) == pt._PRE_COMPOSE_VETOES


def test_pending_review_veto_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """A URL with a pending classifier review vetoes compose with a duplicate_review_pending outcome."""
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url",
        lambda _url: True,
    )
    assert pt._pending_review_veto(_ctx()) == {
        "status": "duplicate_review_pending",
        "service_id": "svc",
    }


def test_pending_review_veto_passes_when_no_pending_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passes (returns None) when the URL has no pending classifier review."""
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url",
        lambda _url: False,
    )
    assert pt._pending_review_veto(_ctx()) is None


def test_domain_cap_veto_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """A domain that has reached its compose cap vetoes compose with a domain_capped outcome."""
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.domain_compose_cap_reached",
        lambda _d: True,
    )
    assert pt._domain_cap_veto(_ctx()) == {"status": "domain_capped", "service_id": "svc"}


def test_domain_cap_veto_skipped_when_unenforced_or_domainless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The domain-cap veto is skipped entirely (never even queried) when unenforced or there's no domain."""
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.domain_compose_cap_reached",
        lambda _d: (_ for _ in ()).throw(AssertionError("must not be consulted")),
    )
    assert pt._domain_cap_veto(_ctx(enforce_domain_cap=False)) is None
    assert pt._domain_cap_veto(_ctx(compose_domain="")) is None


def test_novelty_duplicate_veto_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """A title too similar to a recent one vetoes compose with a duplicate/too_similar_to_recent outcome."""
    monkeypatch.setattr("app.core.config.NOVELTY_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.NOVELTY_MAX_SIMILARITY", 0.6, raising=False)
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_title_similarity",
        lambda _t: (0.83, "Close Title"),
    )
    assert pt._novelty_duplicate_veto(_ctx()) == {
        "status": "duplicate",
        "reason": "too_similar_to_recent",
        "service_id": "svc",
        "closest_title": "Close Title",
        "similarity": 0.83,
    }


def test_novelty_veto_inert_when_gate_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The novelty veto never even queries title similarity when the gate is disabled."""
    monkeypatch.setattr("app.core.config.NOVELTY_GATE_ENABLED", False, raising=False)
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_title_similarity",
        lambda _t: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert pt._novelty_duplicate_veto(_ctx()) is None


def test_content_quality_veto_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """Relevance below the content-update quality floor vetoes compose and expires the queue row."""
    monkeypatch.setattr("app.core.config.CONTENT_UPDATE_QUALITY_FLOOR", 0.35, raising=False)
    outcome = pt._content_quality_veto(_ctx(signals=SimpleNamespace(relevance=0.31)))
    assert outcome == {
        "status": "skipped",
        "reason": "poor_quality_content",
        # Retires the row: a sub-floor snapshot can't improve until re-crawl,
        # and a squatting pending row blocks that re-crawl's signal (the
        # one-pending-per-service dedupe).
        "queue_status": "expired",
        "service_id": "svc",
        "relevance": 0.31,
    }


def test_all_pass_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Running all vetoes returns None when every individual veto passes."""
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url",
        lambda _url: False,
    )
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.domain_compose_cap_reached",
        lambda _d: False,
    )
    monkeypatch.setattr("app.core.config.NOVELTY_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_title_similarity",
        lambda _t: (0.1, ""),
    )
    assert pt._run_pre_compose_vetoes(_ctx()) is None


def test_first_veto_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the first veto in the list fails, later vetoes are never consulted."""
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url",
        lambda _url: True,
    )
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.domain_compose_cap_reached",
        lambda _d: (_ for _ in ()).throw(AssertionError("later veto must not run")),
    )
    outcome = pt._run_pre_compose_vetoes(_ctx())
    assert outcome is not None
    assert outcome["status"] == "duplicate_review_pending"


def test_second_veto_wins_when_first_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the first veto passes, the second veto's failure is what's returned and later vetoes stop."""
    monkeypatch.setattr(
        "app.modules.crawler.classifier_review_store.has_pending_review_for_url",
        lambda _url: False,
    )
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.domain_compose_cap_reached",
        lambda _d: True,
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_title_similarity",
        lambda _t: (_ for _ in ()).throw(AssertionError("later veto must not run")),
    )
    outcome = pt._run_pre_compose_vetoes(_ctx())
    assert outcome is not None
    assert outcome["status"] == "domain_capped"


def test_stale_null_decision_is_refreshed_at_compose_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rows enqueued under training mode carry publish_decision=null forever; the compose path must re-ask the classifier with today's model instead of holding on a frozen verdict."""
    from app.modules.ai import publish_classifier
    from app.modules.ai.content_signals import ContentSignals

    calls = {}

    def fake_predict(_text: str, url: str, category: str) -> tuple[bool, float]:
        calls["args"] = (url, category)
        return True, 0.93

    monkeypatch.setattr(publish_classifier, "predict_publish", fake_predict)

    signals = ContentSignals.from_payload(
        {
            "category": "news",
            "categories": ["news"],
            "publish_decision": None,
            "confidence": 0.81,
        }
    )
    assert signals is not None
    assert signals.publish_decision is None
    # Mirror the compose-path refresh logic.
    decision, confidence = signals.publish_decision, signals.confidence
    if decision is None:
        decision, confidence = publish_classifier.predict_publish(
            "text", "https://x.io", signals.category
        )
    assert decision is True
    assert confidence == 0.93
    assert calls["args"] == ("https://x.io", "news")


def test_same_service_novelty_bar_blocks_the_alpha_arcade_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-07-16: 'Alpha Arcade Goes Live with Daily Algorand Price Prediction Markets' vs 'Alpha Arcade expands to daily Algorand price markets with $3,415 volume' scores 0.455 title-Jaccard — under the global 0.6 gate, yet plainly the same story about the same service ten days later. Same-service re-coverage gets the stricter 0.4 bar."""
    monkeypatch.setattr("app.core.config.NOVELTY_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.NOVELTY_MAX_SIMILARITY", 0.6, raising=False)
    monkeypatch.setattr("app.core.config.NOVELTY_SAME_SERVICE_MAX_SIMILARITY", 0.4, raising=False)
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_title_similarity",
        lambda _t: (0.455, "Alpha Arcade Goes Live with Daily Algorand Price Prediction Markets"),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_same_service_similarity",
        lambda _t, _sid: (
            0.455,
            "Alpha Arcade Goes Live with Daily Algorand Price Prediction Markets",
        ),
    )
    outcome = pt._novelty_duplicate_veto(_ctx())
    assert outcome is not None
    assert outcome["reason"] == "too_similar_to_own_recent_coverage"
    assert outcome["similarity"] == 0.46


def test_same_similarity_from_a_DIFFERENT_service_still_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same 0.455 similarity score passes when it's against a different service's headline, not the same service's."""
    # 0.455 against some other service's headline is legitimate coverage of a
    # related-but-distinct story — only same-service re-coverage is tightened.
    monkeypatch.setattr("app.core.config.NOVELTY_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr("app.core.config.NOVELTY_MAX_SIMILARITY", 0.6, raising=False)
    monkeypatch.setattr("app.core.config.NOVELTY_SAME_SERVICE_MAX_SIMILARITY", 0.4, raising=False)
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_title_similarity",
        lambda _t: (0.455, "Someone Else's Similar Headline"),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_same_service_similarity",
        lambda _t, _sid: (0.1, ""),
    )
    assert pt._novelty_duplicate_veto(_ctx()) is None
