"""Deterministic gate for unsourced HARD SPECIFICS in a composed body.

Two incidents share one mechanism — the writer supplies impressive, verifiable-
looking specifics that are NOT in the fetched evidence:
- MyAlgo (2026-07-19): a defunct wallet recommended as current.
- GoPlausible (2026-07-20): the rendered homepage the writer fetched showed its
  stat-counters at ZERO ("0+ Events & hackathons", "0K+ Credentials issued") and
  an EMPTY partners section; the draft wrote "over 1,000 issuers", "70+ events
  and hackathons", and a "Borderless Capital" partnership. The tokens "1,000",
  "70", "issuer", "Borderless" appear nowhere in the trace.

The existing gates don't catch this class (they do dead links / fake quotes /
fabricated benchmarks / authority-phrasing). This one generalises quote_gate's
"verbatim-in-corpus" check from quotations to two kinds of specific claim:

  1. NUMERIC traction/funding figures — a number adjacent to a traction noun
     (users, issuers, events, integrations, …) or a funding noun (raised, TVL,
     valuation, …) or a currency amount. The number's digit-run must appear in
     the ground corpus (research trace + compose input).
  2. NAMED partners/backers — a proper-noun name introduced by a partnership /
     backing trigger ("partners with", "backed by", "investors include", …).
     The name must appear in the ground corpus.

Precision levers (this is why it ships read-only first, to tune on real data):
- A bare number is only a candidate if a traction/funding noun sits within a few
  words — so protocol names (x402, ARC-69), years (2027), block times (2.8s) and
  version strings (OAuth 2.2) are ignored, they have no traction noun beside them.
- Numbers are matched by digit-RUN (commas stripped), not substring, so "70"
  does not spuriously match "1970" in the corpus.

Fail-open throughout: any error yields no findings — a gate bug must never block
a release. Read-only unless UNSOURCED_SPECIFICS_GATE_ENFORCE; ENABLED only
records payload['_unsourced_specifics'] + logs, never mutates the body.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Nouns whose count is an adoption/traction claim a reader would take as fact.
_TRACTION_NOUNS = {
    "user",
    "users",
    "issuer",
    "issuers",
    "customer",
    "customers",
    "holder",
    "holders",
    "member",
    "members",
    "developer",
    "developers",
    "wallet",
    "wallets",
    "download",
    "downloads",
    "install",
    "installs",
    "signup",
    "signups",
    "event",
    "events",
    "hackathon",
    "hackathons",
    "integration",
    "integrations",
    "partner",
    "partners",
    "project",
    "projects",
    "dapp",
    "dapps",
    "validator",
    "validators",
    "subscriber",
    "subscribers",
    "follower",
    "followers",
    "community",
    "communities",
    "merchant",
    "merchants",
    "participant",
    "participants",
}
# Discrete FUNDING-EVENT nouns — a raise/round is announced as a round figure
# ("$5M seed"), unlike live price/TVL, so a currency amount beside one is a
# checkable claim. Deliberately excludes price/tvl/volume (live, reformatted).
_FUNDING_NOUNS = {
    "raised",
    "raise",
    "funding",
    "seed",
    "round",
    "valuation",
    "grant",
    "grants",
    "investment",
    "backing",
    "backed",
    "led",
}
# Percentages attach only to TRACTION nouns here ("70% of users churned").
# On-chain SHARE percentages (of supply / holders / market) are deliberately
# excluded: the read-only tuning pass showed they are live on-chain data (compx
# et al., reformatted → false positives) and they are already chain_entity_gate's
# job (it resolves the cited asset/address and verifies the true share on-chain).
_PCT_NOUNS = _TRACTION_NOUNS
# A percentage token: 40%, 12.5%.
_PCT_TOKEN_RE = re.compile(r"\d[\d,]*(?:\.\d+)?%")

# A number token: optional $, digits with thousands separators, optional decimal,
# optional K/M/B or word multiplier, optional trailing +.
_NUM_RE = re.compile(
    r"\$?\d[\d,]*(?:\.\d+)?\s?(?:[KMB]\b|thousand|million|billion)?\+?",
    re.I,
)
_WORD_RE = re.compile(r"[A-Za-z0-9$][A-Za-z0-9$.,+%-]*")
_DIGIT_RUN_RE = re.compile(r"\d+")

# Partnership / backing triggers → the text after them may name an entity.
_PARTNER_TRIGGER_RE = re.compile(
    r"(?:in partnership with|partner(?:s|ed|ing)?\s+with|partnership(?:s)? with|"
    r"backed by|funded by|investor[s]?\s+(?:include|are|including)|"
    r"affiliation[s]?\s+with|backers?\s+include|partners?\s+include)\s+([^.;\n]{2,120})",
    re.I,
)
# A capitalised proper-noun run (1-4 words), allowing &/./digits inside a name.
_PROPER_NOUN_RE = re.compile(r"[A-Z][A-Za-z0-9.&]+(?:\s+[A-Z][A-Za-z0-9.&]+){0,3}")
# Generic capitalised words that are not a partner identity on their own.
_NAME_STOPWORDS = {
    "algorand",
    "the",
    "defi",
    "defi protocols",
    "web3",
    "web2",
    "ai",
    "nft",
    "layer",
    "foundation",  # "the Foundation" alone isn't a specific backer name
    "mainnet",
    "testnet",
    "dao",
}

_FOLD_RE = re.compile(r"[^a-z0-9]+")


def _fold(text: str) -> str:
    return _FOLD_RE.sub(" ", (text or "").lower()).strip()


def _ground_corpus(trace: list[dict] | None, extra_texts: list[str]) -> str:
    parts: list[str] = []
    for entry in trace or ():
        try:
            parts.append(json.dumps(entry))
        except (TypeError, ValueError):
            parts.append(str(entry))
    parts.extend(t for t in extra_texts if t)
    return " ".join(parts)


def _tokens(text: str) -> list[str]:
    return _WORD_RE.findall(text)


def _stem(noun: str) -> str:
    """Crude singularisation so a claim 'issuers' matches corpus 'issuer'."""
    if noun.endswith("ies") and len(noun) > 4:
        return noun[:-3] + "y"
    if noun.endswith("s") and len(noun) > 3:
        return noun[:-1]
    return noun


def _number_grounded(digits: str, noun: str, corpus_ctx: str) -> bool:
    """A count is grounded only if its digit-run appears NEAR its own noun in the corpus — not merely somewhere in it. A bare digit-run match is far too weak: common runs like '70' or '1000' turn up in almost any fetched page (a 70px style, a 1000ms timing, a URL id), which would spuriously ground a fabricated 'issued to 1,000 issuers'. Require the number and the (stemmed) noun to co-occur within a short window, in either order."""
    if not noun:
        return digits in set(_DIGIT_RUN_RE.findall(corpus_ctx))
    stem = re.escape(_stem(noun))
    d = re.escape(digits)
    pat = re.compile(rf"{d}\D{{0,40}}{stem}|{stem}\D{{0,40}}{d}")
    return bool(pat.search(corpus_ctx))


def _skip_non_count_token(tok: str, nxt: str) -> bool:
    """True when a number-shaped token is out of scope for count-claim checking: a currency figure, a bare decimal (version/ratio/block-time), a bare year, or the day inside a written date."""
    # v1 scope = discrete COUNTS. Currency figures ($ prices, TVL, volumes)
    # come from live market/chain tools and are reformatted/rounded in the
    # body (0.083787 → 0.0838), so literal digit-matching false-positives on
    # grounded data; neither fabrication incident involved currency. Out of
    # scope — skip. (Chain/on-chain values are covered by chain_entity_gate.)
    if tok.strip().startswith("$"):
        return True
    # A bare decimal (2.2, 2.8) is a version/ratio/block-time, never a
    # headcount — traction counts are integers or magnitude (K/M/B). Skip
    # decimals unless they carry a magnitude suffix.
    if "." in tok and not re.search(r"[kmb]", tok, re.I):
        return True
    # A bare 4-digit year (1900-2099) is a date, not a count, even when it
    # sits next to a traction noun ("the 2019 validators", "sunset in 2023").
    # Counts of this magnitude are written with a separator ("2,000 users"),
    # which this pattern (no comma) does not match — so we keep those.
    if re.fullmatch(r"(?:19|20)\d\d", tok.strip(".,:;!?()+")):
        return True
    # A day inside a written date ("June 12, 2027") — the number is followed
    # by ", <year>". Not a count; skip so it can't grab a nearby noun via the
    # proximity window (the "12, … validators" false positive, birthday site).
    return bool(tok.rstrip(",").isdigit() and re.fullmatch(r"(?:19|20)\d\d", nxt))


def _numeric_findings(body: str, corpus_ctx: str) -> list[dict[str, str]]:
    """Count-shaped numbers adjacent to a traction/funding noun whose value is not grounded (near its noun) in the corpus. ``corpus_ctx`` is the folded, comma-stripped ground corpus."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    tokens = _tokens(body)
    # Strip edge punctuation so a noun ending a sentence ("issuers.") still
    # matches the noun set; the numeric token itself is read from `tokens`.
    lowered = [t.strip(".,:;!?()").lower() for t in tokens]
    for i, tok in enumerate(tokens):
        if not _NUM_RE.fullmatch(tok):
            continue
        digits = "".join(_DIGIT_RUN_RE.findall(tok.replace(",", "")))
        if not digits:
            continue
        nxt = lowered[i + 1] if i + 1 < len(lowered) else ""
        if _skip_non_count_token(tok, nxt):
            continue
        # Only a claim when a COUNT noun sits within a few words — this excludes
        # protocol names (x402), years (2027) and version strings, which have no
        # count noun beside them. Financial nouns (TVL, valuation) are omitted:
        # currency is out of v1 scope, and raw TVL integers were pure noise.
        noun = _noun_near(lowered, i, _TRACTION_NOUNS)
        if not noun:
            continue
        if _number_grounded(digits, noun, corpus_ctx):
            continue
        key = f"{digits}:{noun}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": "numeric", "claim": tok, "context": noun})
    return out


def _noun_near(lowered: list[str], i: int, noun_set: set[str]) -> str:
    """First noun from noun_set within ±3 tokens of position i (after-side first).

    A proximity window, not a next-token check, so an adjective or a compound
    ('1,000 verified issuers', '70+ events and hackathons') doesn't break the
    association.
    """
    for w in list(lowered[i + 1 : i + 4]) + list(lowered[max(0, i - 3) : i]):
        if w in noun_set:
            return w
    return ""


def _funding_findings(body: str, corpus_ctx: str) -> list[dict[str, str]]:
    """Currency amounts beside a discrete funding-event noun (raised/seed/round/valuation…) whose value isn't grounded near that noun.

    Currency near price/TVL is still ignored — only a funding EVENT makes a
    $ figure a checkable one-off claim.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    tokens = _tokens(body)
    lowered = [t.strip(".,:;!?()").lower() for t in tokens]
    for i, tok in enumerate(tokens):
        if not tok.strip().startswith("$") or not _NUM_RE.fullmatch(tok):
            continue
        digits = "".join(_DIGIT_RUN_RE.findall(tok.replace(",", "")))
        if not digits:
            continue
        noun = _noun_near(lowered, i, _FUNDING_NOUNS)
        if not noun or _number_grounded(digits, noun, corpus_ctx):
            continue
        key = f"{digits}:{noun}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": "funding", "claim": tok, "context": noun})
    return out


def _percent_findings(body: str, corpus_ctx: str) -> list[dict[str, str]]:
    """Percentages beside an ownership/traction noun ('40% of the supply', '70% of holders') whose value isn't grounded near that noun."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    tokens = _tokens(body)
    lowered = [t.strip(".,:;!?()").lower() for t in tokens]
    for i, tok in enumerate(tokens):
        if not _PCT_TOKEN_RE.fullmatch(tok.strip(".,:;!?()")):
            continue
        digits = "".join(_DIGIT_RUN_RE.findall(tok.replace(",", "")))
        if not digits:
            continue
        noun = _noun_near(lowered, i, _PCT_NOUNS)
        if not noun or _number_grounded(digits, noun, corpus_ctx):
            continue
        key = f"{digits}:{noun}"
        if key in seen:
            continue
        seen.add(key)
        out.append({"kind": "percent", "claim": tok.strip(".,:;!?()"), "context": noun})
    return out


def _named_findings(body: str, corpus_folded: str) -> list[dict[str, str]]:
    """Proper-noun partners/backers introduced by a partnership trigger whose name is absent from the ground corpus."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in _PARTNER_TRIGGER_RE.finditer(body):
        tail = m.group(1)
        for nm in _PROPER_NOUN_RE.findall(tail):
            folded = _fold(nm)
            if not folded or folded in _NAME_STOPWORDS:
                continue
            # drop leading generic words ("DeFi protocols like Tinyman" → Tinyman)
            if folded in seen:
                continue
            if folded in corpus_folded:
                continue  # grounded
            seen.add(folded)
            out.append({"kind": "named", "claim": nm.strip(), "context": "partner/backer"})
    return out


def find_unsourced_specifics(
    body: str,
    trace: list[dict] | None,
    *,
    extra_texts: list[str] | None = None,
) -> list[dict[str, str]]:
    """All hard specifics in the body not traceable to the ground corpus. Pure — no config, no mutation; safe to call from a tuning script over old sessions."""
    if not body:
        return []
    corpus = _ground_corpus(trace, list(extra_texts or []))
    # Comma-stripped + folded, so a claim's normalised digit-run ("5000") can be
    # matched near its noun and "5,000 members" in the corpus still grounds it.
    corpus_ctx = _fold(corpus.replace(",", ""))
    corpus_folded = _fold(corpus)
    findings = _numeric_findings(body, corpus_ctx)
    findings += _funding_findings(body, corpus_ctx)
    findings += _percent_findings(body, corpus_ctx)
    findings += _named_findings(body, corpus_folded)
    return findings


def unsourced_specifics_revision_issues(
    body: str,
    trace: list[dict] | None,
    *,
    extra_texts: list[str] | None = None,
) -> list[str]:
    """Human-readable revision instructions for each unsourced specific, for the in-loop revision pass (like authority/chain/link feedback). Lets the writer — which read the research — remove or CORRECT the claim before the post-hoc gate has to hold it. The reviser has no tools, only the research digest, so the instruction is 'remove or correct to what your sources show', not 'go fetch it'. Gated by ENABLED (this is non-destructive guidance); the ENFORCE hold remains the backstop for anything that survives revision."""
    from app.core.config import UNSOURCED_SPECIFICS_GATE_ENABLED

    if not UNSOURCED_SPECIFICS_GATE_ENABLED or not body:
        return []
    try:
        findings = find_unsourced_specifics(body, trace, extra_texts=extra_texts)
    except Exception:
        logger.warning("unsourced-specifics revision scan failed", exc_info=True)
        return []
    issues: list[str] = []
    for f in findings:
        label = f["claim"]
        if f["context"] and f["kind"] != "named":
            label = f"{f['claim']} {f['context']}"
        what = "named partner/backer" if f["kind"] == "named" else "figure"
        issues.append(
            f'unsourced specific: the {what} "{label}" does not appear anywhere in '
            "your research — remove it, or correct it to exactly what your sources "
            "show (if a counter reads 0/0+/0K+ or a section is empty, report that; "
            "never substitute a plausible number or name)"
        )
    return issues


def flag_unsourced_specifics(
    payload: dict[str, Any],
    trace: list[dict] | None,
    *,
    extra_texts: list[str] | None = None,
) -> dict[str, Any]:
    """Record (and, when enforcing, act on) unsourced hard specifics in the body.

    Read-only by default: sets payload['_unsourced_specifics'] and logs, never
    mutating the body — so we can measure extraction precision on real traffic
    before it can hold or rewrite anything. With UNSOURCED_SPECIFICS_GATE_ENFORCE
    it also sets payload['_unsourced_hold_reason'] for the publish gate.
    """
    from app.core.config import (
        UNSOURCED_SPECIFICS_GATE_ENABLED,
        UNSOURCED_SPECIFICS_GATE_ENFORCE,
    )

    if not UNSOURCED_SPECIFICS_GATE_ENABLED:
        return payload
    body = payload.get("body")
    if not isinstance(body, str) or not body:
        return payload
    try:
        findings = find_unsourced_specifics(body, trace, extra_texts=extra_texts)
    except Exception:
        logger.warning("unsourced-specifics gate failed (fail-open)", exc_info=True)
        return payload
    if not findings:
        return payload

    payload["_unsourced_specifics"] = findings
    claims = [f"{f['claim']}" + (f" ({f['context']})" if f["context"] else "") for f in findings]
    logger.warning(
        "unsourced-specifics gate: %d ungrounded specific(s)%s: %s",
        len(findings),
        "" if UNSOURCED_SPECIFICS_GATE_ENFORCE else " [read-only, not enforced]",
        " | ".join(claims[:8]),
    )
    if UNSOURCED_SPECIFICS_GATE_ENFORCE:
        payload["_unsourced_hold_reason"] = (
            "unsourced hard specifics not in research: " + ", ".join(claims[:6])
        )
    return payload
