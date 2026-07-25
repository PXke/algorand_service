"""Deterministic link-validation gate for composed article bodies.

Root-caused 2026-07-16 (RandGallery shutdown article): the writer decorated
real project names with INVENTED urls — downbad.art (real site: downbad.farm),
alchemon.com (project never appeared in the research trace at all), and a
guessed algorand.foundation/ecosystem-projects/… page that 404s. The numeric
gatekeeper can't see urls, so all three shipped to the live feed.

The rule mirrors how a human editor treats citations:
- A link whose url appeared anywhere in the research trace is KEPT without a
  network check — the writer got it from research, and many legitimate hosts
  (x.com, reddit) block server fetches while being perfectly good links for
  readers.
- A link the research never surfaced must prove it exists: one guarded GET,
  and anything that doesn't answer 2xx/3xx gets DELINKED — the anchor text
  survives, only the url is dropped. A 403/404/DNS-fail url the writer can't
  vouch for (it wasn't in research) is exactly the fabrication-suspect set
  this gate exists for.

Delinking (not vetoing) keeps the gate safe to run on every compose: worst
case a reader loses a hyperlink, never a sentence.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_URL_RE = re.compile(r"https?://[^\s\"'\\<>)\]}]+")

# Bound the per-article live-check budget: bodies rarely carry more than a
# handful of untraced links; anything past the cap is kept as-is (fail-open)
# rather than stalling the compose on a link farm.
_MAX_LIVE_CHECKS = 10
_LIVE_CHECK_TIMEOUT = 6.0


def _normalize(url: str) -> str:
    u = (url or "").strip().rstrip("/").lower()
    for prefix in ("https://www.", "http://www.", "https://", "http://"):
        if u.startswith(prefix):
            return u[len(prefix) :]
    return u


def _trace_url_set(trace: list[dict] | None) -> set[str]:
    """Every url that appeared anywhere in the research trace (tool arguments AND results), normalized. Serialization keeps this shape-agnostic."""
    urls: set[str] = set()
    for entry in trace or ():
        try:
            blob = json.dumps(entry)
        except (TypeError, ValueError):
            blob = str(entry)
        # json.dumps escapes / as-is but the regex stops at \" boundaries.
        for m in _URL_RE.finditer(blob):
            urls.add(_normalize(m.group(0).rstrip("\\")))
    return urls


def _link_is_live(url: str) -> bool:
    from app.core.net_guard import guarded_get

    try:
        resp = guarded_get(url, timeout=_LIVE_CHECK_TIMEOUT)
        return resp.status_code < 400
    except Exception:
        return False


def dead_untraced_links(
    body: str,
    trace: list[dict] | None,
    *,
    checked: dict[str, bool] | None = None,
) -> list[str]:
    """Body markdown-link urls that neither appeared in the research trace nor resolve live — the fabrication-suspect set. Pass a shared ``checked`` dict to reuse live-check results across revision passes (urls rarely change between passes; re-fetching them each pass would triple the cost)."""
    if not body:
        return []
    traced = _trace_url_set(trace)
    checked = checked if checked is not None else {}
    dead: list[str] = []
    live_checks = 0
    for match in _MD_LINK_RE.finditer(body):
        url = match.group(2)
        norm = _normalize(url)
        if norm in traced:
            continue
        if norm not in checked:
            if live_checks >= _MAX_LIVE_CHECKS:
                continue  # budget spent — treat the rest as fine, never stall
            live_checks += 1
            checked[norm] = _link_is_live(url)
        if not checked[norm] and url not in dead:
            dead.append(url)
    return dead


def sanitize_untraced_links(
    payload: dict[str, Any],
    trace: list[dict] | None,
    *,
    checked: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Delink body urls that neither appeared in the research trace nor resolve live. Mutates and returns payload; records removals under payload['_links_removed'] so the persisted final_output stays auditable."""
    from app.core.config import LINK_GATE_ENABLED

    if not LINK_GATE_ENABLED:
        return payload
    body = payload.get("body")
    if not isinstance(body, str) or not body:
        return payload
    dead = set(dead_untraced_links(body, trace, checked=checked))
    if not dead:
        return payload
    removed: list[str] = []

    def _replace(match: re.Match) -> str:
        text, url = match.group(1), match.group(2)
        if url not in dead:
            return match.group(0)
        removed.append(url)
        return text

    new_body = _MD_LINK_RE.sub(_replace, body)
    if removed:
        logger.warning(
            "link gate delinked %d untraced dead url(s): %s",
            len(removed),
            ", ".join(removed[:10]),
        )
        payload["body"] = new_body
        payload["_links_removed"] = removed
    return payload
