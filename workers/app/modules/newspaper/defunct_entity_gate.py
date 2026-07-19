"""Hold-for-review veto for articles that cite a provably-defunct entity.

Root-caused 2026-07-19 (NFT-marketplaces article): the writer searched for
Algorand wallets, its own research fetched ``myalgo.com`` and got back a DNS
resolution failure, and it recommended "MyAlgo Wallet" as a current top-three
wallet with a live link anyway — MyAlgo was breached and shut down in 2023. Two
in-context signals (a "MyAlgo hack" search snippet and the dead-domain fetch)
were both ignored.

Why the existing gates missed it:
- The numeric gatekeeper can't see urls at all.
- The link gate KEEPS any url that appeared in the research trace without a
  network check — and myalgo.com *did* appear in the trace, as a FAILED fetch.
  "Appeared in the trace" was meant to mean "the writer found it in research,"
  but a DNS-failed fetch is the opposite of a vouched-for source.

This gate targets exactly that: a body link whose host the trace recorded as
DNS-unresolvable. It does NOT merely delink (the link gate's job) — a dead
*recommendation in prose* ("Pera, Defly, and MyAlgo") survives delinking, so the
whole draft is held for a human. Precision guardrails keep false holds near-zero:

- Requires BOTH the writer's own trace DNS-failure AND a confirming live re-check
  that the host still doesn't resolve — a transient research-time DNS blip that
  has since recovered will not hold the article.
- Keys on the LINKED host only. An article that merely mentions a defunct entity
  in prose while linking a live source about its shutdown (e.g. a wallet
  round-up that links Ledger's "MyAlgo sunset" page) never trips this.

Fail-open: any error resolving or scanning returns "no defunct domains" — the
gate hardens, it must never wrongly block a release on its own bug.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
# net_guard raises "dns resolution failed for {host}"; also catch the getaddrinfo
# phrasings that can surface through httpx/socket on other platforms.
_DNS_FAIL_RE = re.compile(
    r"(?:dns resolution failed for|name or service not known[:\s]*|"
    r"no address associated with hostname[:\s]*|nodename nor servname[^a-z0-9]*)"
    r"\s*([a-z0-9.-]+\.[a-z]{2,})",
    re.I,
)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").strip().lower().rstrip(".")
    except ValueError:
        return ""


def _registrable(host: str) -> str:
    """Last two labels — a cheap eTLD-ignorant registrable-domain proxy. Only
    used to widen the suspect match (e.g. wallet.myalgo.com ~ myalgo.com); the
    definitive test is always the per-host live re-check below, so an over-broad
    match here cannot by itself hold an article."""
    labels = host.split(".")
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


def _same_entity(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b or a.endswith("." + b) or b.endswith("." + a):
        return True
    return _registrable(a) == _registrable(b)


def _dns_failed_hosts(trace: list[dict] | None) -> set[str]:
    """Hosts the research trace recorded as DNS-unresolvable (from fetch_url
    error results)."""
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


def _resolves(host: str) -> bool:
    """True if the host resolves right now. Bounded and fail-CLOSED-to-alive: any
    error other than a definitive lookup failure is treated as 'resolves' so we
    never hold an article on a flaky resolver. Split out for test injection."""
    import socket

    if not host:
        return True
    try:
        socket.getaddrinfo(host, None)
        return True
    except socket.gaierror:
        return False
    except Exception:
        return True


def defunct_linked_domains(body: str, trace: list[dict] | None) -> list[str]:
    """Hosts that are (a) linked in the body, (b) recorded DNS-unresolvable in
    the research trace, and (c) still unresolvable now. These are entities the
    writer was shown to be gone yet cited as live."""
    if not body:
        return []
    failed = _dns_failed_hosts(trace)
    if not failed:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for match in _MD_LINK_RE.finditer(body):
        host = _host(match.group(2))
        if not host or host in seen:
            continue
        if not any(_same_entity(host, f) for f in failed):
            continue
        seen.add(host)
        if not _resolves(host):  # confirm still dead — filters transient blips
            out.append(host)
    return out


def flag_defunct_entities(payload: dict[str, Any], trace: list[dict] | None) -> dict[str, Any]:
    """Set ``payload['_defunct_domains']`` (and a human-readable
    ``_hold_reason``) when the body links a provably-defunct entity, so the
    publish gate diverts the draft to human review. Never mutates the body —
    the prose, not just the link, needs a human. No-op when disabled."""
    from app.core.config import DEFUNCT_ENTITY_GATE_ENABLED

    if not DEFUNCT_ENTITY_GATE_ENABLED:
        return payload
    body = payload.get("body")
    if not isinstance(body, str) or not body:
        return payload
    try:
        dead = defunct_linked_domains(body, trace)
    except Exception:
        logger.warning("defunct-entity gate failed (fail-open)", exc_info=True)
        return payload
    if dead:
        payload["_defunct_domains"] = dead
        payload["_hold_reason"] = (
            "links defunct entity the research flagged as unreachable: "
            + ", ".join(dead[:5])
        )
        logger.warning(
            "defunct-entity gate holding draft for review — dead linked domain(s): %s",
            ", ".join(dead[:10]),
        )
    return payload
