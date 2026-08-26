"""Keyword-based relevance scoring for crawled pages."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from algorand_shared.keyword_relevance import (
    _AMBIGUOUS_KEYWORD_RE,
    _KEYWORD_FAMILY_CAP,
    AMBIGUOUS_KEYWORDS,
    KNOWN_DOMAINS,
    _compile_keyword_pattern,
)
from algorand_shared.keyword_relevance import keyword_hits as keyword_hits

# KNOWN_DOMAINS/AMBIGUOUS_KEYWORDS/keyword_hits() (and RELEVANCE_KEYWORDS,
# which this module no longer needs directly) moved to
# algorand_shared.keyword_relevance (2026-08-26): pure text/regex matching
# with zero workers-only dependency, needed by both this module's own
# score_page() AND algorand_shared.artifact_priority.ecosystem_listed_score()
# -- which previously could only import them from here, and this module
# doesn't exist in backend's codebase. KNOWN_DOMAINS/AMBIGUOUS_KEYWORDS/etc.
# are re-imported above because score_page() itself still uses them;
# `keyword_hits` is re-imported (as itself, the standard explicit-re-export
# idiom already used for ArtifactStmts/ToComposeStmts in app/core/statements.py)
# purely so `publish_classifier.py`'s existing `from app.modules.search.
# classifier.score import keyword_hits` keeps working unchanged, even though
# nothing in this module calls it directly anymore.

# Algorand block explorers — a page that links to a SPECIFIC asset/account
# there (not just uses the word) is about as strong a relevance signal as
# exists, and catches multi-chain services whose own prose never says
# "Algorand" at all: quantoz.com's EURQ product page links straight to its
# allo.info ASA page but the word "algorand" appears nowhere in the page's own
# visible text — EURQ is also issued on Ethereum/XRPL/Stellar/Xahau, so the
# marketing copy stays chain-agnostic (2026-07-21).
EXPLORER_DOMAINS: frozenset[str] = frozenset({"allo.info", "explorer.perawallet.app"})

# Chain-agnostic blockchain-infra vocabulary: "defi"/"testnet"/"mainnet"/
# "walletconnect"/"indexer" describe concepts common to essentially every
# chain's own documentation (Ethereum, Aave, Bitcoin, ...) and carry NO
# Algorand-specific signal on their own. Root-caused 2026-08-26: calnix.
# gitbook.io (Aave/Ethereum docs, zero Algorand mentions) and protegecoin.
# com.br (a generic Bitcoin custody guide, zero Algorand mentions) both
# landed exactly at content_relevance=0.500 — precisely
# FRONTIER_CONTENT_PROMOTE_SCORE — purely from repeated hits in this family,
# with no domain anchor, explorer link, or exact "algorand" mention anywhere
# on either page. Kept separate from ALGORAND_KEYWORDS below and capped hard
# in score_page() so this family alone can never clear either that promote
# threshold or this module's own DEFAULT_THRESHOLD.
GENERIC_KEYWORDS: tuple[str, ...] = (
    "walletconnect",
    "defi",
    "testnet",
    "mainnet",
    "indexer",
)

# AMBIGUOUS_KEYWORDS ("algo"/"asa" -- Algorand's own name/ticker, but also
# ordinary standalone words in other languages) moved to
# algorand_shared.keyword_relevance and re-imported above -- see that
# module's docstring. Still used here for score_page's own ambiguous tier
# (_AMBIGUOUS_KEYWORD_RE, also re-imported above).

# Terms specific enough to Algorand itself (its full name, its ARC/PPoS/algod
# technical vocabulary, and named ecosystem projects) that a genuine hit is
# real signal, not a same-spelling word in an unrelated language -- full
# weight. "algorand" itself also earns a separate flat exact-mention bonus
# below, on top of whatever it contributes here.
ALGORAND_KEYWORDS: tuple[str, ...] = (
    "algorand",
    "arc-",
    "microalgo",
    "algod",
    "pure proof of stake",
    "ppos",
    # Ecosystem proper nouns — a story can be entirely Algorand-relevant
    # without repeating the word "algorand" (e.g. a TxnLab/Haystack Router
    # or Tinyman/Folks Finance piece named after the project, not the chain).
    "txnlab",
    "haystack router",
    "deflex",
    "tinyman",
    "folks finance",
    "nfdomains",
    "algokit",
    "hesabpay",
)

# Back-compat combined list — content_categorizer's fallback category guesser
# just wants "does this page look Algorand-ish at all" (presence-based, not
# weighted), so it's fine for it to keep seeing the full family here.
POSITIVE_KEYWORDS: tuple[str, ...] = ALGORAND_KEYWORDS + GENERIC_KEYWORDS + AMBIGUOUS_KEYWORDS


# _compile_keyword_pattern moved to algorand_shared.keyword_relevance
# (re-imported above) -- still the compiler for THIS module's own
# ALGORAND_KEYWORDS/GENERIC_KEYWORDS tiers below (AMBIGUOUS_KEYWORDS'
# _AMBIGUOUS_KEYWORD_RE is built there and re-imported, not rebuilt here).
_ALGORAND_KEYWORD_RE: tuple[re.Pattern[str], ...] = tuple(
    _compile_keyword_pattern(kw) for kw in ALGORAND_KEYWORDS
)
_GENERIC_KEYWORD_RE: tuple[re.Pattern[str], ...] = tuple(
    _compile_keyword_pattern(kw) for kw in GENERIC_KEYWORDS
)


def _ecosystem_listed() -> frozenset[str]:
    """Directory-listed domains from the crawler's sync (cached there); the classifier must keep working with no DB, so failures mean 'no extras'."""
    try:
        from app.modules.crawler.ecosystem_sync import ecosystem_listed_domains

        return ecosystem_listed_domains()
    except Exception:
        return frozenset()


REJECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\balgorithm\b", re.IGNORECASE),
    re.compile(r"\balgebra\b", re.IGNORECASE),
    re.compile(r"\balgonquin\b", re.IGNORECASE),
)

# Evergreen SEO-farm shapes ("Algorand price prediction 2024, 2025 - 2030",
# "how to buy ALGO", casino/exchange listicles). These pages mention Algorand
# heavily, so keyword density alone ranks them ABOVE real news — they must be
# penalized even when keyword hits are present (unlike REJECT_PATTERNS, which
# only fires on keyword-free noise). Shared so the 0-1 score, the priority
# ranking's timeliness withhold, and the frontier all agree on the verdict.
SEO_SPAM_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"price\s+(prediction|forecast)", re.IGNORECASE),
    re.compile(r"\b20\d{2}\s*[-–—,]\s*20\d{2}\b"),
    re.compile(r"how\s+to\s+buy\b", re.IGNORECASE),
    # Gambling-affiliate spam phrasing, not the bare topic word. A real
    # web3 casino/gaming project (rantlabs.xyz) legitimately says "casino"
    # once in an otherwise ordinary product description ("web3 casino
    # platform ... provably fair games") with none of this framing anywhere
    # near it; root-caused 2026-08-26 after the bare `\bcasino` pattern (every
    # other entry in this list is a genuine spam PHRASE, not a single
    # topic-adjacent keyword) dragged its storage score toward 0 for that one
    # incidental mention. Genuine crypto-casino listicles reliably pair
    # "casino" with promotional/affiliate language instead — bonuses, free
    # spins, "online casino" as a listicle category, or a review/list framing
    # — and the sibling `best\s+crypto\s+...\s+casino` pattern below still
    # catches the "best crypto casino" shape on its own.
    re.compile(
        r"online\s+casino|casino\s+(bonus(es)?|no\s*deposit|free\s*spins?"
        r"|sites?\s+(review|list)|games?\s+list)",
        re.IGNORECASE,
    ),
    re.compile(r"best\s+crypto\s+(exchange|wallet|app|casino|saving)", re.IGNORECASE),
    re.compile(r"is\s+it\s+a\s+good\s+investment", re.IGNORECASE),
    re.compile(r"should\s+you\s+(buy|invest)", re.IGNORECASE),
)


def seo_spam_hits(text: str) -> int:
    """Count of distinct SEO-spam shapes present (0 = looks organic)."""
    if not text:
        return 0
    return sum(1 for pat in SEO_SPAM_PATTERNS if pat.search(text))


DEFAULT_THRESHOLD = 0.35

# RELEVANCE_KEYWORDS/_RELEVANCE_KEYWORD_RE/_KEYWORD_FAMILY_CAP/
# _AMBIGUOUS_RELEVANCE_WEIGHT/keyword_hits() (the single source of Algorand
# keyword truth for crude hit-counting -- storage score + the enqueue/quality
# floor) moved to algorand_shared.keyword_relevance and are re-imported above.
# See that module's docstring for the full rationale and the "algo"/"asa"
# ambiguous-word history.


@dataclass(frozen=True)
class ClassifierResult:
    """One page's keyword-relevance scoring result.

    ``components`` is the structured counterpart of ``reasons``: the same
    signals, but as their actual numeric contribution to ``score`` rather
    than a flattened human-readable tag. Only keys for signals that actually
    fired are present (mirroring how ``reasons`` only lists tags for things
    that fired) -- e.g. a page with no outbound explorer link and no
    domain-directory match never gets a ``links_to_explorer``/
    ``domain_listed`` key at all, rather than an explicit 0.0. Added
    2026-08-26 so the admin Domains tab can render a real breakdown (like
    the artifact-priority breakdown already does) instead of just the
    flattened ``reasons`` tag line -- purely additive, exposing already-
    computed intermediate values with zero change to ``score``/``in_scope``/
    ``reasons`` themselves.
    """

    score: float
    in_scope: bool
    reasons: tuple[str, ...]
    components: dict[str, float] = field(default_factory=dict)


def _hostname(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _explorer_link_signal(outbound_links: tuple[str, ...]) -> tuple[float, str | None]:
    for link in outbound_links:
        link_host = _hostname(link)
        if link_host and any(
            link_host == d or link_host.endswith(f".{d}") for d in EXPLORER_DOMAINS
        ):
            return 0.5, f"links_to_explorer:{link_host}"
    return 0.0, None


def _domain_signal(host: str) -> tuple[float, str | None]:
    """Known-domain or curated ecosystem-directory anchor. A directory listing is a stronger relevance signal than the page's own text, which for chain-silent services (HesabPay/Lofty class) contains no Algorand mention at all."""
    if not host:
        return 0.0, None
    for domain in KNOWN_DOMAINS:
        if host == domain or host.endswith(f".{domain}"):
            return 0.45, f"known_domain:{domain}"
    for domain in _ecosystem_listed():
        if host == domain or host.endswith(f".{domain}"):
            return 0.45, f"ecosystem_domain:{domain}"
    return 0.0, None


def _reject_signal(text: str, keyword_hits: int) -> tuple[float, str | None]:
    reject_hits = sum(1 for pat in REJECT_PATTERNS if pat.search(text))
    if reject_hits and keyword_hits == 0:
        return -0.25, f"reject_noise:{reject_hits}"
    return 0.0, None


def _spam_signal(text: str) -> tuple[float, str | None]:
    spam_hits = seo_spam_hits(text)
    if not spam_hits:
        return 0.0, None
    return -min(0.45, spam_hits * 0.15), f"seo_spam:{spam_hits}"


def score_page(
    *,
    url: str,
    text: str,
    outbound_links: tuple[str, ...] = (),
    threshold: float = DEFAULT_THRESHOLD,
) -> ClassifierResult:
    """Score a crawled page's relevance and decide whether it's in-scope for the frontier."""
    reasons: list[str] = []
    components: dict[str, float] = {}
    score = 0.0
    host = _hostname(url)
    lowered = text.lower()

    # Keys line up positionally with the two signals below: explorer-link
    # first, domain-directory second -- see ClassifierResult.components.
    for key, (delta, reason) in zip(
        ("links_to_explorer", "domain_listed"),
        (_explorer_link_signal(outbound_links), _domain_signal(host)),
        strict=True,
    ):
        if reason:
            score += delta
            reasons.append(reason)
            components[key] = delta

    # Weighted like keyword_hits() below: each phrase contributes its
    # occurrence count (capped so one repeated phrase can't dominate), not
    # just presence/absence — a page repeating "algorand" several times in
    # body copy should outscore one that name-drops it once, same fix as
    # the quality-floor gate (urvote.ca, 2026-07-24).
    #
    # Algorand-specific and generic-blockchain hits are scored SEPARATELY
    # (root-caused 2026-08-26, calnix.gitbook.io/protegecoin.com.br): the
    # generic family (defi/testnet/mainnet/walletconnect/indexer) applies
    # equally to any chain's own docs, so it's capped at 0.15 — well below
    # both this function's DEFAULT_THRESHOLD (0.35) and
    # FRONTIER_CONTENT_PROMOTE_SCORE (0.5) — and can never by itself carry a
    # page into scope. Only the Algorand-specific family keeps the full 0.5
    # weight budget.
    algorand_hit_count = sum(
        min(len(pat.findall(lowered)), _KEYWORD_FAMILY_CAP) for pat in _ALGORAND_KEYWORD_RE
    )
    generic_hit_count = sum(
        min(len(pat.findall(lowered)), _KEYWORD_FAMILY_CAP) for pat in _GENERIC_KEYWORD_RE
    )
    # "algo"/"asa" are Algorand's own name/ticker, but also ordinary standalone
    # words in other languages (Spanish/Portuguese "algo" = "something",
    # Portuguese "asa" = "wing") -- word-boundary matching only rules out
    # matching INSIDE another word, not a page simply being written in a
    # language where these are real, unrelated vocabulary. Scored in their own
    # tier, capped the same as GENERIC_KEYWORDS, so genuine hits on these two
    # words alone can never carry a page into scope the way an unambiguous
    # term can (root-caused 2026-08-26, protegecoin.com.br: a real,
    # boundary-correct 0.26 from "algo"/"asa" hits in ordinary Portuguese
    # prose with zero Algorand relevance).
    ambiguous_hit_count = sum(
        min(len(pat.findall(lowered)), _KEYWORD_FAMILY_CAP) for pat in _AMBIGUOUS_KEYWORD_RE
    )
    keyword_hit_count = algorand_hit_count + generic_hit_count + ambiguous_hit_count
    if algorand_hit_count:
        # 0.10/hit, not 0.08: fixing the fragment-match bug above (see
        # _compile_keyword_pattern) removed an unintentional boost that a
        # genuinely on-topic page used to get for free — " algo" used to
        # also match as a false prefix of every "algorand" mention, so a
        # page saying "Algorand" twice in body copy (and nothing else)
        # cleared DEFAULT_THRESHOLD partly on that bug. 0.10/hit restores
        # the same real-world outcome (two clean, genuine "algorand"
        # mentions + the exact-mention bonus below still clears 0.35)
        # without resurrecting the fragment-matching itself.
        delta = min(0.5, algorand_hit_count * 0.10)
        score += delta
        reasons.append(f"keywords:{algorand_hit_count}")
        components["algorand_keywords"] = delta
    if generic_hit_count:
        delta = min(0.15, generic_hit_count * 0.03)
        score += delta
        reasons.append(f"generic_keywords:{generic_hit_count}")
        components["generic_keywords"] = delta
    if ambiguous_hit_count:
        delta = min(0.15, ambiguous_hit_count * 0.03)
        score += delta
        reasons.append(f"ambiguous_keywords:{ambiguous_hit_count}")
        components["ambiguous_keywords"] = delta

    for key, (delta, reason) in zip(
        ("reject_noise", "seo_spam"),
        (_reject_signal(text, keyword_hit_count), _spam_signal(text)),
        strict=True,
    ):
        if reason:
            score += delta
            reasons.append(reason)
            components[key] = delta

    if "algorand" in lowered:
        score += 0.15
        reasons.append("exact:algorand")
        components["exact_mention"] = 0.15

    score = max(0.0, min(1.0, score))
    in_scope = score >= threshold
    if not in_scope and not reasons:
        reasons.append("below_threshold")
    return ClassifierResult(
        score=round(score, 3), in_scope=in_scope, reasons=tuple(reasons), components=components
    )
