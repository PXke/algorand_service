"""Hold-for-review veto for articles that cite a provably-defunct entity.

Root-caused 2026-07-19 (NFT-marketplaces article): the writer recommended
"MyAlgo Wallet" as a current top-three Algorand wallet with a live link — MyAlgo
was breached and shut down in 2023, and its domain no longer resolves. Its own
research had even fetched myalgo.com and gotten a DNS failure, which it ignored.

Why the existing gates missed it:
- The numeric gatekeeper can't see urls at all.
- The link gate KEEPS any url that appeared in the research trace without a
  network check, and only DELINKS untraced dead urls (drops the url, keeps the
  anchor text) — so a defunct entity recommended in PROSE ("Pera, Defly, and
  MyAlgo") survives either way.

This gate actively resolves every domain the body links and HOLDS the whole
draft for human review if any is unreachable — whether the writer fetched it in
research or recommended it blind from stale training memory. Holding (not
delinking) is deliberate: the prose recommendation, not just the hyperlink, is
the defect a human must judge.

False-hold guardrails:
- Runs BEFORE the link gate's delinker (see mistral_compose), so it sees the
  writer's original links, but its verdict is a fresh live DNS lookup, not the
  trace — a link the research flagged dead that has since come back will resolve
  and won't hold.
- "Dead" means a definitive negative DNS answer only: the name does not exist
  (EAI_NONAME) or has no address record (EAI_NODATA). A transient resolver
  failure (EAI_AGAIN) or any other error is treated as ALIVE — the gate never
  holds on a resolver hiccup.
- Each lookup is bounded by a short timeout (a hung resolver reads as alive),
  results dedup by host, and the number of lookups is capped per article.

Fail-open throughout: any error scanning or resolving yields "no defunct
domains" — the gate hardens the pipeline, it must never block a release on its
own bug.
"""

from __future__ import annotations

import logging
import re
import socket
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
# net_guard raises "dns resolution failed for {host}"; used only to annotate the
# hold reason (was the writer already TOLD it was dead?), never for the verdict.
_DNS_FAIL_RE = re.compile(
    r"(?:dns resolution failed for|name or service not known[:\s]*|"
    r"no address associated with hostname[:\s]*|nodename nor servname[^a-z0-9]*)"
    r"\s*([a-z0-9.-]+\.[a-z]{2,})",
    re.I,
)

# A definitive "this host has no usable address" DNS answer. EAI_NONAME = the
# name does not exist; EAI_NODATA = the name exists but has no A/AAAA record
# (real MyAlgo case — the parent zone is parked, wallet.myalgo.com has no
# address). Everything else — notably EAI_AGAIN (transient) and EAI_FAIL — is
# treated as alive so a resolver blip never holds an article.
_DEAD_ERRNOS = {socket.EAI_NONAME}
if hasattr(socket, "EAI_NODATA"):
    _DEAD_ERRNOS.add(socket.EAI_NODATA)

_MAX_DNS_CHECKS = 20  # unique hosts per article; past this, fail-open (skip)
_DNS_TIMEOUT = 3.0  # seconds per host; a hung lookup reads as alive


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").strip().lower().rstrip(".")
    except ValueError:
        return ""


def _dns_failed_hosts(trace: list[dict] | None) -> set[str]:
    """Hosts the research trace recorded as DNS-unresolvable — for annotating the hold reason only (a link the writer was explicitly shown to be dead is a stronger faithfulness failure than one recommended blind)."""
    import json

    hosts: set[str] = set()
    for entry in trace or ():
        try:
            blob = json.dumps(entry)
        except (TypeError, ValueError):
            blob = str(entry)
        for m in _DNS_FAIL_RE.finditer(blob):
            hosts.add(m.group(1).strip().lower().rstrip("."))
    return hosts


def _resolve_blocking(host: str) -> bool:
    """True if the host has a usable address; False only on a definitive no-address DNS answer. Fail-open (alive) on any non-definitive error."""
    try:
        socket.getaddrinfo(host, None)
        return True
    except socket.gaierror as exc:
        return exc.errno not in _DEAD_ERRNOS
    except Exception:
        return True


def _resolves(host: str) -> bool:
    """Bounded, fail-open live DNS check. Split out for test injection; runs the blocking lookup in a worker thread so a hung resolver can't stall the compose (a timeout reads as alive)."""
    if not host:
        return True
    import concurrent.futures as _cf

    try:
        with _cf.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(_resolve_blocking, host).result(timeout=_DNS_TIMEOUT)
    except Exception:
        return True  # timeout / executor failure → assume alive, never hold


def _linked_hosts(body: str) -> list[str]:
    """Unique hosts of every markdown link in the body, first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for match in _MD_LINK_RE.finditer(body):
        host = _host(match.group(2))
        if host and host not in seen:
            seen.add(host)
            out.append(host)
    return out


def defunct_linked_domains(body: str) -> list[str]:
    """Body-linked hosts that do not resolve to a usable address right now — the entities the article presents as live but that are actually gone. Bounded by a per-article lookup cap; anything past the cap is left unchecked (fail-open) rather than stalling the compose on a link farm."""
    if not body:
        return []
    dead: list[str] = []
    for i, host in enumerate(_linked_hosts(body)):
        if i >= _MAX_DNS_CHECKS:
            break
        if not _resolves(host):
            dead.append(host)
    return dead


def flag_defunct_entities(
    payload: dict[str, Any], trace: list[dict] | None = None
) -> dict[str, Any]:
    """Set ``payload['_defunct_domains']`` (and a human-readable ``_hold_reason``) when the body links a domain that does not resolve, so the publish gate diverts the draft to human review. Never mutates the body — the prose, not just the link, needs a human. No-op when disabled. Must run BEFORE the link gate's delinker so it still sees the writer's original links."""
    from app.core.config import DEFUNCT_ENTITY_GATE_ENABLED

    if not DEFUNCT_ENTITY_GATE_ENABLED:
        return payload
    body = payload.get("body")
    if not isinstance(body, str) or not body:
        return payload
    try:
        dead = defunct_linked_domains(body)
    except Exception:
        logger.warning("defunct-entity gate failed (fail-open)", exc_info=True)
        return payload
    if not dead:
        return payload

    payload["_defunct_domains"] = dead
    # Distinguish the two failure modes in the audit trail: a domain the research
    # already flagged dead (writer ignored the signal) vs one recommended blind.
    flagged = _dns_failed_hosts(trace) if trace else set()
    known = [h for h in dead if any(h == f or h.endswith("." + f) for f in flagged)]
    reason = "links unreachable domain(s): " + ", ".join(dead[:5])
    if known:
        reason += " (research already flagged: " + ", ".join(known[:5]) + ")"
    payload["_hold_reason"] = reason
    logger.warning(
        "defunct-entity gate holding draft for review — unreachable linked domain(s): %s%s",
        ", ".join(dead[:10]),
        " [research-flagged: " + ", ".join(known[:10]) + "]" if known else "",
    )
    return payload
