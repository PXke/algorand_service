"""Curated ecosystem-directory sync: the anti-blind-spot for chain-silent services.

Link-following discovery structurally misses real Algorand services whose own
sites never mention the chain (HesabPay has zero chain words on its homepage,
lofty.ai serves an empty JS shell) — their previews score ~0, so auto-approve
can never fire. A curated directory listing (awesome-algorand etc.) is a far
stronger relevance signal than anything on the service's own page, so this task
ingests those directories and:

  - marks each listed domain ``ecosystem_listed`` in domain_tracking (which
    ``score_page`` treats as a known-domain relevance anchor, so future diffs
    clear CONTENT_UPDATE_RELEVANCE_FLOOR),
  - approves it into the frontier and registers it as a monitored service
    (weekly diff watch -> first-coverage introduction on its next snapshot).

Admin decisions stay sovereign: a domain rejected by an admin (permanent
dead_end) is never resurrected by a directory listing. Unreachable domains are
skipped each run and retried on the next.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r'https?://[^)\s"<>\'\]]+')

# Hosts that appear in directory markdown but are never Algorand services:
# code forges, package registries, socials, badges, blob storage.
_SKIP_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "gist.github.com",
        "raw.githubusercontent.com",
        "github.io",  # handled as suffix too, kept for exact matches
        "awesome.re",
        "img.shields.io",
        "api.visitorbadge.io",
        "twitter.com",
        "x.com",
        "youtube.com",
        "medium.com",
        "discord.gg",
        "discord.com",
        "t.me",
        "reddit.com",
        "linkedin.com",
        "facebook.com",
        "docs.google.com",
        "dweb.link",
        "pypi.org",
        "npmjs.com",
        "crates.io",
        "hex.pm",
        "nuget.org",
        "pub.dev",
        "hub.docker.com",
        "rss.com",
        "itch.io",
        "vercel.app",
        "pages.dev",
        "netlify.app",
        "amazonaws.com",
        # Press-release wires / marketing plumbing that case-study pages link.
        "prnewswire.com",
        "businesswire.com",
        "globenewswire.com",
        "hsforms.com",
        "hubspot.com",
        "apple.com",
        "play.google.com",
        "google.com",
        # Docs/paper hosting and feed plumbing seen in mention sources.
        "readthedocs.io",
        "doi.org",
        "superfeedr.com",
    }
)


def _skippable(host: str) -> bool:
    if host in _SKIP_HOSTS:
        return True
    # Suffix match: subdomains of skip hosts (user.github.io, foo.vercel.app,
    # bucket.s3.amazonaws.com) are personal/hosted pages, not services with
    # their own identity — a real product on its own domain is what we want.
    return any(host.endswith(f".{skip}") for skip in _SKIP_HOSTS)


def extract_directory_domains(markdown: str) -> set[str]:
    """Candidate service domains from a directory's markdown: every linked host minus forges/registries/socials/badges and www prefixes."""
    domains: set[str] = set()
    for url in _URL_RE.findall(markdown or ""):
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if host and "." in host and not _skippable(host):
            domains.add(host)
    return domains


def _reachable(domain: str) -> bool:
    """Cheap liveness probe so we don't register weekly watches on dead sites (awesome-list rot is real — algoamm.com black-holes connections)."""
    from app.core.net_guard import guarded_get

    try:
        resp = guarded_get(
            f"https://{domain}/",
            headers={"User-Agent": "Mozilla/5.0 (compatible; pxke-ecosync)"},
            timeout=8.0,
        )
        return resp.status_code < 500
    except Exception:
        return False


def _ingest_domain(domain: str, source_url: str, stats: dict[str, Any]) -> None:
    """Approve + monitor one curated-listed domain (shared by the directory and case-study paths). Admin rejects stay sovereign; already-monitored domains just get the anchor flag stamped."""
    from app.modules.crawler.domain_tracker import (
        ensure_monitored_service,
        get_domain_status,
        update_domain_status,
    )
    from app.modules.newspaper.service_sources import service_for_domain

    stats["domains"] += 1
    try:
        status = get_domain_status(domain) or {}
        meta = status.get("metadata") or {}
        # An explicit admin reject is permanent — a curated listing
        # never overrides it.
        if not status.get("is_relevant", True) and (
            meta.get("frontier_set_by_admin") == "true"
            or status.get("frontier_status") == "dead_end"
            or meta.get("frontier_status") == "dead_end"
        ):
            stats["skipped_admin"] += 1
            return
        if service_for_domain(domain):
            # Already monitored — just make sure the anchor flag is set.
            if meta.get("ecosystem_listed") != "true":
                update_domain_status(
                    domain,
                    relevance_score=float(status.get("relevance_score") or 0.45),
                    metadata={"ecosystem_listed": "true", "ecosystem_source": source_url},
                )
            stats["skipped_existing"] += 1
            return
        if not _reachable(domain):
            stats["skipped_unreachable"] += 1
            return
        update_domain_status(
            domain,
            relevance_score=0.45,
            category="service",
            is_relevant=True,
            frontier_status_override="approved",
            metadata={"ecosystem_listed": "true", "ecosystem_source": source_url},
        )
        if ensure_monitored_service(domain, scrape_url=f"https://{domain}/"):
            stats["created"] += 1
        else:
            stats["skipped_existing"] += 1
    except Exception:
        logger.warning("ecosystem sync: failed on %s", domain, exc_info=True)
        stats["errors"] += 1


def sync_ecosystem_directories() -> dict[str, Any]:
    """Ingest each configured directory URL; approve + monitor newly listed domains. Idempotent: already-monitored domains are counted and skipped."""
    from app.core import config
    from app.core.net_guard import guarded_get

    stats = {
        "directories": 0,
        "domains": 0,
        "created": 0,
        "skipped_admin": 0,
        "skipped_existing": 0,
        "skipped_unreachable": 0,
        "errors": 0,
    }

    for directory_url in config.ECOSYSTEM_DIRECTORY_URLS:
        try:
            resp = guarded_get(directory_url, timeout=20.0)
            resp.raise_for_status()
        except Exception:
            logger.warning("ecosystem sync: failed to fetch %s", directory_url, exc_info=True)
            stats["errors"] += 1
            continue
        stats["directories"] += 1

        for domain in sorted(extract_directory_domains(resp.text)):
            _ingest_domain(domain, directory_url, stats)

    _ecosystem_cache["at"] = 0.0  # new listings visible to score_page promptly
    return stats


# --------------------------------------------------------------------------- #
# Case-study indexes (algorand.co/case-studies): the discovery path for the
# institutional/impact class. These orgs' sites are huge and chain-silent
# (mercycorpsventures.com and aid.technology were both crawled and marked
# IRRELEVANT by keyword scoring), but a Foundation case study is the strongest
# relevance signal there is — every subject org linked from a case-study page
# gets the same anchor + watch as a directory listing.

_HREF_RE = re.compile(r'href="(https?://[^"#]+)"')
_CASE_INDEX_PAGE_CAP = 10


def case_study_detail_links(html: str, index_url: str) -> set[str]:
    """Same-site, single-segment detail-page URLs under the index path (tag/pagination/feed links excluded)."""
    prefix = index_url.rstrip("/") + "/"
    out: set[str] = set()
    for href in _HREF_RE.findall(html or ""):
        clean = href.split("?")[0].rstrip("/")
        if not clean.startswith(prefix):
            continue
        tail = clean[len(prefix) :]
        if not tail or "/" in tail or tail.endswith(".xml"):
            continue
        out.add(clean)
    return out


def extract_case_study_domains(pages: dict[str, str]) -> dict[str, str]:
    """Domain -> the detail URL it appeared on. Domains present on ~every detail page are site furniture (liquidauth.com lives in algorand.co's footer), not case-study subjects, and are dropped."""
    from collections import Counter

    per_page = {url: extract_directory_domains(html) for url, html in pages.items()}
    counts = Counter(d for doms in per_page.values() for d in doms)
    n = len(per_page)
    boilerplate_cutoff = max(3, int(n * 0.4)) if n >= 3 else n + 1
    result: dict[str, str] = {}
    for url in sorted(per_page):
        for domain in sorted(per_page[url]):
            if counts[domain] >= boilerplate_cutoff:
                continue
            result.setdefault(domain, url)
    return result


def sync_ecosystem_case_studies() -> dict[str, Any]:
    """Ingest each configured case-study index: walk its pagination, fetch each detail page, and anchor + monitor every subject org domain found."""
    from app.core import config
    from app.core.net_guard import guarded_get

    stats = {
        "indexes": 0,
        "case_studies": 0,
        "domains": 0,
        "created": 0,
        "skipped_admin": 0,
        "skipped_existing": 0,
        "skipped_unreachable": 0,
        "errors": 0,
    }

    for index_url in config.ECOSYSTEM_CASE_STUDY_INDEXES:
        details: set[str] = set()
        for page_no in range(1, _CASE_INDEX_PAGE_CAP + 1):
            page_url = index_url if page_no == 1 else f"{index_url.rstrip('/')}/page/{page_no}"
            try:
                resp = guarded_get(page_url, timeout=20.0)
                resp.raise_for_status()
            except Exception:
                if page_no == 1:
                    logger.warning("case-study sync: failed to fetch %s", page_url, exc_info=True)
                    stats["errors"] += 1
                break
            found = case_study_detail_links(resp.text, index_url)
            if not (found - details):
                break  # pagination exhausted (page repeats or is empty)
            details |= found
        if not details:
            continue
        stats["indexes"] += 1

        pages: dict[str, str] = {}
        for detail_url in sorted(details):
            try:
                resp = guarded_get(detail_url, timeout=20.0)
                resp.raise_for_status()
                pages[detail_url] = resp.text
            except Exception:
                logger.warning("case-study sync: failed on %s", detail_url, exc_info=True)
                stats["errors"] += 1
        stats["case_studies"] += len(pages)

        for domain, source_url in sorted(extract_case_study_domains(pages).items()):
            _ingest_domain(domain, source_url, stats)

    _ecosystem_cache["at"] = 0.0
    return stats


# --------------------------------------------------------------------------- #
# Machine-readable API sources: DefiLlama's protocol registry and Pera's
# verified-asset list. Both are third-party-vetted ("has real TVL on Algorand"
# / "passed Pera verification"), so they qualify for the same anchor + watch
# as a curated directory listing.


def _domains_from_defillama() -> dict[str, str]:
    """Domain -> attribution for protocols deployed on Algorand. CEX listings are skipped: Binance 'supporting' ALGO custody is not an Algorand service."""
    from app.core.net_guard import guarded_get

    resp = guarded_get("https://api.llama.fi/protocols", timeout=30.0)
    resp.raise_for_status()
    out: dict[str, str] = {}
    for proto in resp.json():
        if not isinstance(proto, dict):
            continue
        if "Algorand" not in (proto.get("chains") or []):
            continue
        if str(proto.get("category") or "").upper() == "CEX":
            continue
        url = str(proto.get("url") or "").strip()
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if host and "." in host and not _skippable(host):
            out.setdefault(host, f"defillama:{proto.get('slug') or proto.get('name')}")
    return out


def _domains_from_pera_verified(*, asset_cap: int) -> dict[str, str]:
    """Domain -> attribution for Pera-verified ASAs, resolved through each asset's on-chain `url` param (algod lookup, same connector the writer's chain tools use). Content-pointer URLs (ipfs/arweave) are skipped."""
    from app.core.net_guard import guarded_get
    from app.modules.ai.chain_tools import _tool_lookup_asset
    from app.modules.chain_tail.discovery import _ASSET_URL_SKIP_HINTS

    resp = guarded_get(
        "https://mainnet.api.perawallet.app/v1/public/verified-assets/",
        timeout=30.0,
    )
    resp.raise_for_status()
    results = (resp.json() or {}).get("results") or []
    asset_ids = [
        int(a["asset_id"])
        for a in results
        if isinstance(a, dict)
        and str(a.get("verification_tier")) == "verified"
        and a.get("asset_id")
    ][:asset_cap]

    out: dict[str, str] = {}
    for asset_id in asset_ids:
        try:
            info = _tool_lookup_asset(asset_id)
        except Exception:
            continue
        url = str((info or {}).get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            continue
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if not host or "." not in host or _skippable(host):
            continue
        if any(hint in host for hint in _ASSET_URL_SKIP_HINTS):
            continue
        out.setdefault(host, f"pera-verified:{asset_id}")
    return out


def sync_ecosystem_apis() -> dict[str, Any]:
    """Ingest the machine-readable ecosystem registries. Each source fails independently — one API being down never blocks the others."""
    from app.core import config

    stats = {
        "sources": 0,
        "domains": 0,
        "created": 0,
        "skipped_admin": 0,
        "skipped_existing": 0,
        "skipped_unreachable": 0,
        "errors": 0,
    }

    fetchers = [
        ("defillama", _domains_from_defillama),
        (
            "pera-verified",
            lambda: _domains_from_pera_verified(asset_cap=config.PERA_VERIFIED_ASSET_CAP),
        ),
    ]
    for name, fetch in fetchers:
        try:
            domains = fetch()
        except Exception:
            logger.warning("ecosystem api sync: %s failed", name, exc_info=True)
            stats["errors"] += 1
            continue
        stats["sources"] += 1
        for domain in sorted(domains):
            _ingest_domain(domain, domains[domain], stats)

    _ecosystem_cache["at"] = 0.0
    return stats


# --------------------------------------------------------------------------- #
# Relevance-anchor lookup for the classifier: which domains are directory-listed.
# Cached scan (the table is a few hundred rows); best-effort — scoring must
# never fail because Cassandra is unreachable.
_ecosystem_cache: dict[str, Any] = {"at": 0.0, "domains": frozenset()}
_ECOSYSTEM_CACHE_TTL_SECONDS = 3600.0


def ecosystem_listed_domains() -> frozenset[str]:
    """Return the cached set of directory-listed domains, refreshing from Cassandra hourly."""
    import time

    now = time.time()
    if now - float(_ecosystem_cache["at"]) < _ECOSYSTEM_CACHE_TTL_SECONDS:
        return _ecosystem_cache["domains"]
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import DomainTrackingStmts

        rows = get_cassandra_session().execute(DomainTrackingStmts.LIST, (5000,))
        listed = frozenset(
            row.domain
            for row in rows
            if (row.metadata or {}).get("ecosystem_listed") == "true"
            and row.is_relevant is not False
        )
        _ecosystem_cache["domains"] = listed
        _ecosystem_cache["at"] = now
    except Exception:
        # Keep whatever we had; retry after TTL.
        _ecosystem_cache["at"] = now - _ECOSYSTEM_CACHE_TTL_SECONDS + 300.0
    return _ecosystem_cache["domains"]
