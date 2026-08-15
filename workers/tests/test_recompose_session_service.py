"""recompose_session_service must resolve a service by its FULL registered host, not just domain_from_url's deliberately subdomain-collapsed eTLD+1 -- root-caused live 2026-08-07: an admin recompose of the Data History Museum article (registered under service_by_domain['museum.datahistory.org']) silently no-op'd because domain_from_url('https://museum.datahistory.org/') collapses to 'datahistory.org', which service_by_domain has no row for. The UI reported "triggered" but nothing ever appeared in the sessions tab."""

from __future__ import annotations

import pytest

from app.modules.newspaper.tasks import publish_tasks as pt


def test_resolves_service_registered_under_a_full_subdomain_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A service registered by its full host (museum.datahistory.org) is found even though domain_from_url would collapse the URL to the bare eTLD+1 (datahistory.org)."""
    registry = {"museum.datahistory.org": "museum-datahistory-org"}

    def fake_service_for_domain(domain: str) -> str:
        return registry.get(domain, "")

    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain", fake_service_for_domain
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_matching.find_latest_service_article",
        lambda service_id: "570f36c7-48af-44e5-90fd-fbbf242fdf3c" if service_id else None,
    )
    monkeypatch.setattr(
        pt, "recompose_published", lambda article_id: {"status": "ok", "article_id": article_id}
    )

    result = pt.recompose_session_service("https://museum.datahistory.org/")

    assert result == {"status": "ok", "article_id": "570f36c7-48af-44e5-90fd-fbbf242fdf3c"}


def test_falls_back_to_collapsed_domain_for_root_registered_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A service registered under the bare eTLD+1 (no subdomain distinction) still resolves via the fallback."""
    registry = {"algorand.co": "algorand-co"}

    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain",
        lambda domain: registry.get(domain, ""),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_matching.find_latest_service_article",
        lambda service_id: "some-article-id" if service_id else None,
    )
    monkeypatch.setattr(
        pt, "recompose_published", lambda article_id: {"status": "ok", "article_id": article_id}
    )

    result = pt.recompose_session_service("https://xgov.algorand.co/proposal/1")

    assert result == {"status": "ok", "article_id": "some-article-id"}


def test_unresolvable_source_fails_fast_with_a_clean_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A source that matches no service under either lookup returns the clean error reason, not a crash."""
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain", lambda _domain: ""
    )
    result = pt.recompose_session_service("https://totally-untracked-domain.example/")
    assert result == {"status": "error", "reason": "no_live_article_for_source"}


def test_empty_source_url_is_a_clean_error() -> None:
    """An empty source_url is rejected before any lookup, no network/DB call."""
    assert pt.recompose_session_service("") == {"status": "error", "reason": "no_source_url"}


def test_editorial_brief_source_resolves_via_linked_article(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An editorial://brief/<id> source resolves through the brief's own linked_article_id, bypassing domain resolution entirely."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.modules.newspaper.editorial_assignment.get_brief",
        lambda brief_id: (
            SimpleNamespace(linked_article_id="brief-linked-article")
            if brief_id == "abc123"
            else None
        ),
    )
    monkeypatch.setattr(
        pt, "recompose_published", lambda article_id: {"status": "ok", "article_id": article_id}
    )

    result = pt.recompose_session_service("editorial://brief/abc123")

    assert result == {"status": "ok", "article_id": "brief-linked-article"}
