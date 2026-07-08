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
    """Candidate service domains from a directory's markdown: every linked
    host minus forges/registries/socials/badges and www prefixes."""
    domains: set[str] = set()
    for url in _URL_RE.findall(markdown or ""):
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        if host and "." in host and not _skippable(host):
            domains.add(host)
    return domains


def _reachable(domain: str) -> bool:
    """Cheap liveness probe so we don't register weekly watches on dead sites
    (awesome-list rot is real — algoamm.com black-holes connections)."""
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


def sync_ecosystem_directories() -> dict[str, Any]:
    """Ingest each configured directory URL; approve + monitor newly listed
    domains. Idempotent: already-monitored domains are counted and skipped."""
    from app.core import config
    from app.core.net_guard import guarded_get
    from app.modules.crawler.domain_tracker import (
        ensure_monitored_service,
        get_domain_status,
        update_domain_status,
    )
    from app.modules.newspaper.service_sources import service_for_domain

    stats = {"directories": 0, "domains": 0, "created": 0, "skipped_admin": 0,
             "skipped_existing": 0, "skipped_unreachable": 0, "errors": 0}

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
            stats["domains"] += 1
            try:
                status = get_domain_status(domain) or {}
                meta = status.get("metadata") or {}
                # An explicit admin reject is permanent — a directory listing
                # never overrides it.
                if not status.get("is_relevant", True) and (
                    meta.get("frontier_set_by_admin") == "true"
                    or status.get("frontier_status") == "dead_end"
                    or meta.get("frontier_status") == "dead_end"
                ):
                    stats["skipped_admin"] += 1
                    continue
                if service_for_domain(domain):
                    # Already monitored — just make sure the anchor flag is set.
                    if meta.get("ecosystem_listed") != "true":
                        update_domain_status(
                            domain,
                            relevance_score=float(status.get("relevance_score") or 0.45),
                            metadata={"ecosystem_listed": "true",
                                      "ecosystem_source": directory_url},
                        )
                    stats["skipped_existing"] += 1
                    continue
                if not _reachable(domain):
                    stats["skipped_unreachable"] += 1
                    continue
                update_domain_status(
                    domain,
                    relevance_score=0.45,
                    category="service",
                    is_relevant=True,
                    frontier_status_override="approved",
                    metadata={"ecosystem_listed": "true",
                              "ecosystem_source": directory_url},
                )
                if ensure_monitored_service(domain, scrape_url=f"https://{domain}/"):
                    stats["created"] += 1
                else:
                    stats["skipped_existing"] += 1
            except Exception:
                logger.warning("ecosystem sync: failed on %s", domain, exc_info=True)
                stats["errors"] += 1

    _ecosystem_cache["at"] = 0.0  # new listings visible to score_page promptly
    return stats


# --------------------------------------------------------------------------- #
# Relevance-anchor lookup for the classifier: which domains are directory-listed.
# Cached scan (the table is a few hundred rows); best-effort — scoring must
# never fail because Cassandra is unreachable.
_ecosystem_cache: dict[str, Any] = {"at": 0.0, "domains": frozenset()}
_ECOSYSTEM_CACHE_TTL_SECONDS = 3600.0


def ecosystem_listed_domains() -> frozenset[str]:
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
