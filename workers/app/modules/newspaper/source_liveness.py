"""Detect a crawled source whose domain has gone dead since it was last crawled.

Root-caused 2026-08-27 (arima.io): a pending artifact's stored content was
crawled 2026-08-25 from the real, legitimate project site, but the domain's
registration expired sometime after -- it sat in the pending pool with
nothing re-checking it before it could be selected/composed as if the
project were still current. Neither existing safety net catches this shape:

- `domain_probe` (writer_enrichment) checks TLS/HTTP status at compose time,
  purely advisory (a note in the writer's context, never a gate) -- and a
  parked domain returns a normal 200 with valid HTTPS, so its "safety_hint"
  reads as fine.
- `defunct_entity_gate` checks DNS resolution of links inside the WRITTEN
  body, and only fires on a definitive DNS failure (NXDOMAIN/NODATA). A
  parked domain's DNS resolves fine -- only the page BODY (a registrar's
  parking template) gives it away.

Deliberately narrow and fail-open: matches only well-known registrar/
parking-service page signatures, never a fuzzy "looks empty" heuristic.
Any fetch error, timeout, non-2xx status, or unmatched body reads as ALIVE.
A false "dead" verdict permanently discards real content; a missed one is
just the status quo before this module existed -- the asymmetry is
deliberate, matching this codebase's other fail-open content gates (see
`defunct_entity_gate`'s own docstring for the same reasoning).
"""

from __future__ import annotations

import logging

import httpx

from app.core.net_guard import assert_public_url

logger = logging.getLogger(__name__)

_TIMEOUT = 8.0
_MAX_CHARS = 65536

# Matched case-insensitively against the fetched body. Each phrase/marker
# unambiguously asserts the domain itself is no longer controlled by its
# prior owner -- broader "domain for sale" language is deliberately excluded,
# since a legitimate project's own landing page can say that without being
# dead (e.g. a startup pivoting, or a domain broker's own inventory listing).
_PARKING_MARKERS = (
    "domain registration has expired",
    "this domain has expired",
    "domain name has expired",
    "this domain is parked",
    "parked free, courtesy of",
    "sedoparking.com",
    "parkingcrew.net",
    "lander.parity.domains",
)


def is_source_parked_or_expired(url: str) -> bool:
    """True only on a confident match against a known parking-page signature.

    Fails open (False) on any fetch error, timeout, non-2xx status, or
    unmatched body -- never raises.
    """
    if not url or not url.lower().startswith("http"):
        return False
    try:
        assert_public_url(url)
        response = httpx.get(
            url,
            timeout=_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": "algorand-platform-liveness/1.0"},
        )
        if response.status_code >= 400:
            return False
        text = response.text[:_MAX_CHARS].lower()
    except Exception:
        logger.debug("source liveness probe failed for %s -- treating as alive", url, exc_info=True)
        return False

    return any(marker in text for marker in _PARKING_MARKERS)


def find_dead_pending_artifacts(*, scan_limit: int = 200) -> list[dict[str, object]]:
    """Read-only: PENDING crawler-channel artifacts whose source URL now reads as a parked/expired registrar page.

    Scoped to channel == "crawler" -- a forum post, video, or Bluesky item
    (the per-item lanes) has no "site" of its own to go dead; URL liveness
    only applies to a scraped web source.
    """
    from algorand_shared.artifact_store import list_pending_artifacts

    dead: list[dict[str, object]] = []
    for artifact in list_pending_artifacts(limit=scan_limit):
        if artifact.channel != "crawler" or not artifact.url:
            continue
        if is_source_parked_or_expired(artifact.url):
            dead.append(
                {
                    "artifact_id": artifact.artifact_id,
                    "service_id": artifact.service_id,
                    "url": artifact.url,
                }
            )
    return dead


def discard_dead_pending_artifacts(*, scan_limit: int = 200, dry_run: bool = True) -> dict[str, object]:
    """Act on `find_dead_pending_artifacts`: DISCARD each -- a parked/expired domain isn't coming back on its own, so this mirrors the standard-drain's other permanent-discard gates (brief_archived/novelty_collapsed), not a soft defer. `dry_run=True` by default, matching this codebase's other report/act reconciliation pairs."""
    found = find_dead_pending_artifacts(scan_limit=scan_limit)
    if dry_run:
        return {"status": "dry_run", "would_discard": found}

    from algorand_shared.artifact_store import DISCARDED, mark_artifact_status

    discarded: list[str] = []
    for entry in found:
        mark_artifact_status(str(entry["artifact_id"]), DISCARDED)
        discarded.append(str(entry["artifact_id"]))
    return {"status": "ok", "discarded": discarded, "count": len(discarded)}
