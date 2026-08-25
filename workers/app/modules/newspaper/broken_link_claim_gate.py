"""Deterministic gate for unverified "broken link" claims in a composed body.

Root-caused 2026-08-10 (lumirogue.com): a draft called the site's "About this
project" / "Terms of use" footer links broken because the guessed /about and
/terms URLs genuinely 404. That's not the real user experience — those footer
items are JS buttons with no real href, opening working in-page modals when
clicked via click_element/play_interactive. The fix at the time was a prompt
instruction (see llm_compose.py's "CLIENT-SIDE ROUTE 404 CHECK") telling
the writer to try click_element before reporting something broken — but that
did NOT hold: the identical mistake recurred on the same site's Terms-of-use
link on 2026-08-12, prompt guidance already in place the whole time.

This gate is the mirror image of unsourced_specifics_gate.py: instead of
checking that a POSITIVE claim traces to the research trace, it checks that a
NEGATIVE claim ("this is broken / 404s / doesn't work") is backed by real
verification effort — specifically, that the trace contains at least one
click_element or play_interactive(action="click") attempt. A guessed
fetch_url() 404 alone is not verification for an SPA; the writer needs to
have actually tried clicking before asserting brokenness.

Deliberately coarse for v1: this checks "was ANY click attempted this
compose", not "was THIS SPECIFIC link clicked" — matching precision here
would need per-claim link/target extraction the unsourced_specifics gate
doesn't need either (it works off number/name proximity, not identifying
which UI element a prose sentence refers to). Ships read-only
(ENFORCE=False) so precision can be read from real traffic before it can
hold anything, same rollout discipline the two gates above followed.

Fail-open throughout: any error yields no findings — a gate bug must never
block a release.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Phrases asserting a link/page/feature is dead, broken, or inaccessible.
# Deliberately about LINKS/PAGES/FEATURES, not entities/projects going dark
# (that's defunct_entity_gate's job) or a game/service being down entirely.
_BROKEN_CLAIM_RE = re.compile(
    r"\b(?:"
    r"returns? (?:a |an )?404|"
    r"resolves? to (?:a |an )?404|"
    r"leads? to (?:a |an )?404|"
    r"404 (?:error|page)|"
    r"page not found|"
    r"(?:is|are) (?:a |an )?(?:dead|broken) link|"
    r"link(?:s)? (?:is|are) (?:dead|broken)|"
    r"(?:does not|doesn't) work|"
    r"no longer works?|"
    r"currently broken|"
    r"leads? nowhere|"
    r"unstyled shell"
    r")\b",
    re.I,
)


def _trace_has_click_attempt(trace: list[dict] | None) -> bool:
    """True if the trace shows at least one real click_element call or play_interactive(action="click") — the minimum verification effort for an SPA link/button claim, regardless of whether that click itself succeeded."""
    for entry in trace or ():
        tool = entry.get("tool")
        if tool == "click_element":
            return True
        if tool == "play_interactive":
            args = entry.get("arguments") or {}
            if args.get("action") == "click":
                return True
    return False


def find_unverified_broken_link_claims(
    body: str, trace: list[dict] | None
) -> list[dict[str, str]]:
    """Broken-link/page/feature claims in the body when the trace shows NO click_element/play_interactive click attempt anywhere this compose. Pure — no config, no mutation; safe to call from a tuning script over old sessions."""
    if not body:
        return []
    if _trace_has_click_attempt(trace):
        return []
    seen: set[str] = set()
    findings: list[dict[str, str]] = []
    for m in _BROKEN_CLAIM_RE.finditer(body):
        phrase = m.group(0)
        key = phrase.lower()
        if key in seen:
            continue
        seen.add(key)
        # A short window of surrounding context, for the human reviewer / log
        # line — not used for matching, just readability.
        start = max(0, m.start() - 40)
        end = min(len(body), m.end() + 40)
        findings.append({"claim": phrase, "context": body[start:end].strip()})
    return findings


def broken_link_claim_revision_issues(body: str, trace: list[dict] | None) -> list[str]:
    """Human-readable revision instructions for each unverified broken-link claim, for the in-loop revision pass — gives the writer (which still has tool access during revision) a chance to actually click_element the thing before the post-hoc gate has to hold it."""
    from app.core.config import BROKEN_LINK_CLAIM_GATE_ENABLED

    if not BROKEN_LINK_CLAIM_GATE_ENABLED or not body:
        return []
    try:
        findings = find_unverified_broken_link_claims(body, trace)
    except Exception:
        logger.warning("broken-link-claim revision scan failed", exc_info=True)
        return []
    if not findings:
        return []
    claims = ", ".join(f'"{f["claim"]}"' for f in findings[:5])
    return [
        f"unverified broken-link claim: you wrote {claims} without ever calling "
        "click_element or play_interactive(action=\"click\") this session. A guessed "
        "URL 404ing is not proof an SPA's feature is unreachable — many are JS "
        "buttons with no real href that open working content when clicked. Either "
        "click the actual control to confirm it's really inaccessible, or soften/"
        "remove the broken-link claim if you cannot verify it now."
    ]


def flag_unverified_broken_link_claims(
    payload: dict[str, Any], trace: list[dict] | None
) -> dict[str, Any]:
    """Record (and, when enforcing, act on) unverified broken-link claims in the body.

    Read-only by default: sets payload['_broken_link_claims'] and logs, never
    mutating the body. With BROKEN_LINK_CLAIM_GATE_ENFORCE it also sets
    payload['_broken_link_hold_reason'] for the publish gate.
    """
    from app.core.config import BROKEN_LINK_CLAIM_GATE_ENABLED, BROKEN_LINK_CLAIM_GATE_ENFORCE

    if not BROKEN_LINK_CLAIM_GATE_ENABLED:
        return payload
    body = payload.get("body")
    if not isinstance(body, str) or not body:
        return payload
    try:
        findings = find_unverified_broken_link_claims(body, trace)
    except Exception:
        logger.warning("broken-link-claim gate failed (fail-open)", exc_info=True)
        return payload
    if not findings:
        return payload

    payload["_broken_link_claims"] = findings
    claims = [f["claim"] for f in findings]
    logger.warning(
        "broken-link-claim gate: %d unverified claim(s)%s: %s",
        len(findings),
        "" if BROKEN_LINK_CLAIM_GATE_ENFORCE else " [read-only, not enforced]",
        " | ".join(claims[:8]),
    )
    if BROKEN_LINK_CLAIM_GATE_ENFORCE:
        payload["_broken_link_hold_reason"] = (
            "unverified broken-link claim(s), no click_element/play_interactive "
            "click attempted this session: " + ", ".join(claims[:6])
        )
    return payload
