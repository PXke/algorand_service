"""Ecosystem-directory sync (2026-07-08): curated listings are the discovery +
relevance path for chain-silent services (HesabPay/Lofty class — real Algorand
services whose own homepages contain zero chain mentions, so link-following
discovery and keyword scoring both structurally miss them)."""

from types import SimpleNamespace

import app.modules.crawler.ecosystem_sync as es
from app.modules.search.classifier.score import score_page

_MARKDOWN = """
# Awesome Algorand
- [Aramid Finance](https://aramid.finance/) cross-chain bridge
- [CompX](https://www.compx.io/docs) DeFi suite
- [repo](https://github.com/someorg/sometool) dev tool
- [badge](https://img.shields.io/badge/x.svg)
- [pkg](https://pypi.org/project/algosdk/)
- [personal](https://someuser.github.io/demo)
- [hosted](https://myapp.vercel.app/)
- [social](https://twitter.com/algorand)
"""


def test_extract_keeps_services_drops_forges_and_socials() -> None:
    domains = es.extract_directory_domains(_MARKDOWN)
    assert domains == {"aramid.finance", "compx.io"}


def _wire(monkeypatch, *, status=None, owned=False, reachable=True):
    calls = {"updated": [], "ensured": []}
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.get_domain_status", lambda d: status
    )
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.update_domain_status",
        lambda d, **kw: calls["updated"].append((d, kw)),
    )
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.ensure_monitored_service",
        lambda d, scrape_url="": calls["ensured"].append(d) or True,
    )
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain",
        lambda d: "owner-svc" if owned else "",
    )
    monkeypatch.setattr(es, "_reachable", lambda d: reachable)
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda url, **kw: SimpleNamespace(
            text=_MARKDOWN, status_code=200, raise_for_status=lambda: None
        ),
    )
    monkeypatch.setattr(
        "app.core.config.ECOSYSTEM_DIRECTORY_URLS", ["https://example.com/list.md"]
    )
    return calls


def test_sync_approves_and_monitors_new_domains(monkeypatch) -> None:
    calls = _wire(monkeypatch, status=None)
    stats = es.sync_ecosystem_directories()
    assert stats["created"] == 2
    assert sorted(calls["ensured"]) == ["aramid.finance", "compx.io"]
    for _d, kw in calls["updated"]:
        assert kw["metadata"]["ecosystem_listed"] == "true"
        assert kw["frontier_status_override"] == "approved"


def test_sync_never_resurrects_admin_rejects(monkeypatch) -> None:
    rejected = {
        "is_relevant": False,
        "frontier_status": "dead_end",
        "metadata": {"frontier_set_by_admin": "true"},
    }
    calls = _wire(monkeypatch, status=rejected)
    stats = es.sync_ecosystem_directories()
    assert stats["skipped_admin"] == 2
    assert not calls["ensured"] and not calls["updated"]


def test_sync_skips_unreachable_domains(monkeypatch) -> None:
    calls = _wire(monkeypatch, status=None, reachable=False)
    stats = es.sync_ecosystem_directories()
    assert stats["skipped_unreachable"] == 2
    assert not calls["ensured"]


def test_sync_flags_already_monitored_without_respawning(monkeypatch) -> None:
    calls = _wire(monkeypatch, status={"metadata": {}, "relevance_score": 2.0}, owned=True)
    stats = es.sync_ecosystem_directories()
    assert stats["skipped_existing"] == 2
    assert not calls["ensured"]
    # But the anchor flag gets stamped for score_page.
    assert all(kw["metadata"]["ecosystem_listed"] == "true" for _d, kw in calls["updated"])


def test_score_page_anchors_ecosystem_listed_domain(monkeypatch) -> None:
    """A directory-listed, chain-silent page must clear the 0.35 relevance
    floors even with zero Algorand keywords."""
    monkeypatch.setattr(es, "ecosystem_listed_domains", lambda: frozenset({"dork.fi"}))
    result = score_page(
        url="https://dork.fi/",
        text="Unlock liquidity without selling. Borrow against your assets.",
    )
    assert result.score >= 0.35
    assert any(r.startswith("ecosystem_domain:") for r in result.reasons)


def test_score_page_survives_lookup_failure(monkeypatch) -> None:
    def _boom():
        raise RuntimeError("cassandra down")

    monkeypatch.setattr(es, "ecosystem_listed_domains", _boom)
    result = score_page(url="https://unknown-site.com/", text="nothing relevant here")
    assert result.score == 0.0
