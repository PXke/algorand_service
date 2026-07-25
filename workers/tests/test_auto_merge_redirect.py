"""A scrape that resolves (via a real HTTP redirect, e.g. a rebrand) to a domain a DIFFERENT service already owns must auto-fold into that service, instead of the two polling/composing independently forever — the nodely.io/algonode.io duplicate-article incident this codifies a fix for."""

from typing import Never

import pytest

from app.modules.newspaper.tasks.publish_tasks import _auto_merge_redirect


def test_same_domain_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Does not merge when the redirect stays on the same domain."""
    calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.merge_services",
        lambda **kw: calls.append(kw),
    )
    _auto_merge_redirect(
        original_url="https://nodely.io/",
        final_url="https://nodely.io/status",
        service_id="nodely-io",
    )
    assert calls == []


def test_redirect_to_unclaimed_domain_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Does not merge when the redirect target domain has no owning service."""
    calls = []
    monkeypatch.setattr("app.modules.newspaper.service_sources.service_for_domain", lambda _d: "")
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.merge_services",
        lambda **kw: calls.append(kw),
    )
    _auto_merge_redirect(
        original_url="https://algonode.io/",
        final_url="https://unclaimed.example/",
        service_id="algonode-io",
    )
    assert calls == []


def test_redirect_to_self_owned_domain_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Does not merge when the redirect target maps back to the same service."""
    # The resolved domain maps back to the SAME service_id — already merged.
    calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain", lambda _d: "algonode-io"
    )
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.merge_services",
        lambda **kw: calls.append(kw),
    )
    _auto_merge_redirect(
        original_url="https://algonode.io/",
        final_url="https://algonode.io/",
        service_id="algonode-io",
    )
    assert calls == []


def test_redirect_to_differently_owned_domain_merges(monkeypatch: pytest.MonkeyPatch) -> None:
    """Merges the source service into the target service when the redirect resolves to a domain another service owns."""
    calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain", lambda _d: "nodely-io"
    )
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.merge_services",
        lambda **kw: calls.append(kw),
    )
    _auto_merge_redirect(
        original_url="https://algonode.io/",
        final_url="https://nodely.io/",
        service_id="algonode-io",
    )
    assert calls == [{"target_service_id": "nodely-io", "source_service_ids": ["algonode-io"]}]


def test_merge_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Swallows a merge_services failure instead of letting it fail the in-progress compose."""
    # Best-effort: a Cassandra hiccup here must never fail the compose in progress.
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain", lambda _d: "nodely-io"
    )

    def _boom(**_kw: object) -> Never:
        raise RuntimeError("cassandra timeout")

    monkeypatch.setattr("app.modules.newspaper.service_sources.merge_services", _boom)
    _auto_merge_redirect(
        original_url="https://algonode.io/",
        final_url="https://nodely.io/",
        service_id="algonode-io",
    )  # must not raise
