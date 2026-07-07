"""publish_from_queued_row's pre-compose vetoes are a uniform ordered list
(_PRE_COMPOSE_VETOES) mirroring the drain's _PRE_COMPOSE_GATES: each veto
returns None (pass) or its exact outcome dict (skip). These tests pin the
extraction's contract — order, pass-through, and the outcome dicts staying
byte-identical to what the inline checks used to return."""

from types import SimpleNamespace

from app.modules.newspaper.publish_policy import PublishKind
from app.modules.newspaper.tasks import publish_tasks as pt


def _ctx(**overrides):
    row = SimpleNamespace(
        queue_id="q1",
        service_id="svc",
        scrape_url="https://example.com",
        payload={"page_title": "A headline", "page_text": "text"},
    )
    defaults = dict(
        row=row,
        publish_kind=PublishKind.CONTENT_UPDATE,
        compose_domain="example.com",
        enforce_domain_cap=True,
        signals=SimpleNamespace(relevance=0.9),
    )
    defaults.update(overrides)
    return pt._ComposeVetoCtx(**defaults)


def test_veto_order():
    assert pt._PRE_COMPOSE_VETOES == (
        pt._domain_cap_veto,
        pt._novelty_duplicate_veto,
        pt._content_quality_veto,
    )


def test_domain_cap_veto_outcome(monkeypatch):
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.domain_compose_cap_reached",
        lambda _d: True,
    )
    assert pt._domain_cap_veto(_ctx()) == {"status": "domain_capped", "service_id": "svc"}


def test_domain_cap_veto_skipped_when_unenforced_or_domainless(monkeypatch):
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.domain_compose_cap_reached",
        lambda _d: (_ for _ in ()).throw(AssertionError("must not be consulted")),
    )
    assert pt._domain_cap_veto(_ctx(enforce_domain_cap=False)) is None
    assert pt._domain_cap_veto(_ctx(compose_domain="")) is None


def test_novelty_duplicate_veto_outcome(monkeypatch):
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


def test_novelty_veto_inert_when_gate_disabled(monkeypatch):
    monkeypatch.setattr("app.core.config.NOVELTY_GATE_ENABLED", False, raising=False)
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_title_similarity",
        lambda _t: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert pt._novelty_duplicate_veto(_ctx()) is None


def test_content_quality_veto_outcome(monkeypatch):
    monkeypatch.setattr("app.core.config.CONTENT_UPDATE_QUALITY_FLOOR", 0.35, raising=False)
    outcome = pt._content_quality_veto(_ctx(signals=SimpleNamespace(relevance=0.31)))
    assert outcome == {
        "status": "skipped",
        "reason": "poor_quality_content",
        "service_id": "svc",
        "relevance": 0.31,
    }


def test_all_pass_returns_none(monkeypatch):
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


def test_first_veto_wins(monkeypatch):
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.domain_compose_cap_reached",
        lambda _d: True,
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_title_similarity",
        lambda _t: (_ for _ in ()).throw(AssertionError("later veto must not run")),
    )
    outcome = pt._run_pre_compose_vetoes(_ctx())
    assert outcome is not None and outcome["status"] == "domain_capped"
