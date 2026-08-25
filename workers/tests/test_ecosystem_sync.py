"""Ecosystem-directory sync (2026-07-08): curated listings are the discovery + relevance path for chain-silent services (HesabPay/Lofty class — real Algorand services whose own homepages contain zero chain mentions, so link-following discovery and keyword scoring both structurally miss them)."""

from types import SimpleNamespace
from typing import Any, Never

import pytest

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
    """Extracts real service domains from an awesome-list markdown, dropping code forges, badges, packages, and social links."""
    domains = es.extract_directory_domains(_MARKDOWN)
    assert domains == {"aramid.finance", "compx.io"}


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    status: dict[str, Any] | None = None,
    owned: bool = False,
    reachable: bool = True,
) -> dict[str, list]:
    calls = {"updated": [], "ensured": []}
    monkeypatch.setattr("app.modules.crawler.domain_tracker.get_domain_status", lambda _d: status)
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.update_domain_status",
        lambda d, **kw: calls["updated"].append((d, kw)),
    )
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.ensure_monitored_service",
        lambda d, scrape_url="": calls["ensured"].append(d) or True,  # noqa: ARG005 -- name must match the real callee's keyword arg
    )
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain",
        lambda _d: "owner-svc" if owned else "",
    )
    monkeypatch.setattr(es, "_reachable", lambda _d: reachable)
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda _url, **_kw: SimpleNamespace(
            text=_MARKDOWN, status_code=200, raise_for_status=lambda: None
        ),
    )
    monkeypatch.setattr("app.core.config.ECOSYSTEM_DIRECTORY_URLS", ["https://example.com/list.md"])
    return calls


def test_sync_approves_and_monitors_new_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    """Approves and starts monitoring each new domain found in the directory listing."""
    calls = _wire(monkeypatch, status=None)
    stats = es.sync_ecosystem_directories()
    assert stats["created"] == 2
    assert sorted(calls["ensured"]) == ["aramid.finance", "compx.io"]
    for _d, kw in calls["updated"]:
        assert kw["metadata"]["ecosystem_listed"] == "true"
        assert kw["frontier_status_override"] == "approved"


def test_sync_never_resurrects_admin_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips a domain the admin permanently rejected instead of re-approving it via directory sync."""
    rejected = {
        "is_relevant": False,
        "frontier_status": "dead_end",
        "metadata": {"frontier_set_by_admin": "true"},
    }
    calls = _wire(monkeypatch, status=rejected)
    stats = es.sync_ecosystem_directories()
    assert stats["skipped_admin"] == 2
    assert not calls["ensured"]
    assert not calls["updated"]


def test_sync_skips_unreachable_domains(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skips approving and monitoring a domain that fails the reachability check."""
    calls = _wire(monkeypatch, status=None, reachable=False)
    stats = es.sync_ecosystem_directories()
    assert stats["skipped_unreachable"] == 2
    assert not calls["ensured"]


def test_sync_flags_already_monitored_without_respawning(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stamps the ecosystem-listed anchor flag on an already-monitored domain without re-ensuring monitoring."""
    calls = _wire(monkeypatch, status={"metadata": {}, "relevance_score": 2.0}, owned=True)
    stats = es.sync_ecosystem_directories()
    assert stats["skipped_existing"] == 2
    assert not calls["ensured"]
    # But the anchor flag gets stamped for score_page.
    assert all(kw["metadata"]["ecosystem_listed"] == "true" for _d, kw in calls["updated"])


def test_score_page_anchors_ecosystem_listed_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    """A directory-listed, chain-silent page must clear the 0.35 relevance floors even with zero Algorand keywords."""
    monkeypatch.setattr(es, "ecosystem_listed_domains", lambda: frozenset({"dork.fi"}))
    result = score_page(
        url="https://dork.fi/",
        text="Unlock liquidity without selling. Borrow against your assets.",
    )
    assert result.score >= 0.35
    assert any(r.startswith("ecosystem_domain:") for r in result.reasons)


_INDEX_HTML = """
<a href="https://algorand.co/case-studies/wholechain-can">Wholechain</a>
<a href="https://algorand.co/case-studies/kare-wallet-can">Kare</a>
<a href="https://algorand.co/case-studies/tag/impact">tag</a>
<a href="https://algorand.co/case-studies/page/2">next</a>
<a href="https://algorand.co/case-studies/rss.xml">rss</a>
<a href="https://algorand.co/case-studies">index</a>
"""


def test_case_study_detail_links_keeps_only_detail_pages() -> None:
    """Keeps only real case-study detail links, dropping tag/pagination/feed/index links."""
    links = es.case_study_detail_links(_INDEX_HTML, "https://algorand.co/case-studies")
    assert links == {
        "https://algorand.co/case-studies/wholechain-can",
        "https://algorand.co/case-studies/kare-wallet-can",
    }


def test_extract_case_study_domains_drops_site_furniture() -> None:
    """Drops a domain repeated across every case-study page as site furniture, keeping only each page's unique subject org."""
    # liquidauth.com sits in the site footer -> appears on every page ->
    # boilerplate; each subject org appears on its own page only.
    pages = {
        f"https://algorand.co/case-studies/p{i}": (
            f'<a href="https://liquidauth.com/">footer</a><a href="https://subject{i}.org/">org</a>'
        )
        for i in range(4)
    }
    domains = es.extract_case_study_domains(pages)
    assert "liquidauth.com" not in domains
    assert set(domains) == {f"subject{i}.org" for i in range(4)}
    # Attribution points at the page the org was found on.
    assert domains["subject0.org"] == "https://algorand.co/case-studies/p0"


def test_sync_case_studies_ingests_subject_orgs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Crawls the case-study index and detail pages and ingests each subject org's domain."""
    calls = _wire(monkeypatch, status=None)
    index = "https://algorand.co/case-studies"
    detail_html = {
        f"{index}/wholechain-can": '<a href="https://wholechain.com/">site</a>',
        f"{index}/kare-wallet-can": '<a href="https://www.aid.technology/kare">site</a>',
    }

    def fake_get(url: str, **_kw: object) -> SimpleNamespace:
        if url == index:
            return SimpleNamespace(text=_INDEX_HTML, status_code=200, raise_for_status=lambda: None)
        if url in detail_html:
            return SimpleNamespace(
                text=detail_html[url], status_code=200, raise_for_status=lambda: None
            )
        raise RuntimeError(f"404 {url}")  # /page/2 etc.

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    monkeypatch.setattr("app.core.config.ECOSYSTEM_CASE_STUDY_INDEXES", [index])
    stats = es.sync_ecosystem_case_studies()
    assert stats["case_studies"] == 2
    assert stats["created"] == 2
    assert sorted(calls["ensured"]) == ["aid.technology", "wholechain.com"]
    for _d, kw in calls["updated"]:
        assert kw["metadata"]["ecosystem_listed"] == "true"
        assert kw["metadata"]["ecosystem_source"].startswith(f"{index}/")


def test_score_page_survives_lookup_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Scores a page as zero relevance, without raising, when the ecosystem-domains lookup itself fails."""

    def _boom() -> Never:
        raise RuntimeError("cassandra down")

    monkeypatch.setattr(es, "ecosystem_listed_domains", _boom)
    result = score_page(url="https://unknown-site.com/", text="nothing relevant here")
    assert result.score == 0.0
