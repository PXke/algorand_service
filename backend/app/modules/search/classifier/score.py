from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# Known Algorand ecosystem domains (lowercase hostnames, no leading www).
KNOWN_DOMAINS: frozenset[str] = frozenset(
    {
        "algorand.co",
        "algorand.foundation",
        "algorand.com",
        "perawallet.app",
        "defly.app",
        "explorer.perawallet.app",
        "allo.info",
        "vestige.fi",
        "tinyman.org",
        "folks.finance",
        "github.com/algorand",
    }
)

POSITIVE_KEYWORDS: tuple[str, ...] = (
    "algorand",
    "algo ",
    " algo",
    "asa ",
    "arc-",
    "walletconnect",
    "defi",
    "testnet",
    "mainnet",
    "microalgo",
    "algod",
    "indexer",
    "pure proof of stake",
    "ppos",
)

# Generic "algo" noise unrelated to Algorand (word-salad / off-topic).
REJECT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\balgorithm\b", re.IGNORECASE),
    re.compile(r"\balgebra\b", re.IGNORECASE),
    re.compile(r"\balgonquin\b", re.IGNORECASE),
)

DEFAULT_THRESHOLD = 0.35


@dataclass(frozen=True)
class ClassifierResult:
    score: float
    in_scope: bool
    reasons: tuple[str, ...]


def _hostname(url: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def score_page(*, url: str, text: str, threshold: float = DEFAULT_THRESHOLD) -> ClassifierResult:
    """Heuristic relevance score for crawled pages (not registry-tied articles)."""
    reasons: list[str] = []
    score = 0.0
    host = _hostname(url)
    lowered = text.lower()

    if host:
        for domain in KNOWN_DOMAINS:
            if host == domain or host.endswith(f".{domain}"):
                score += 0.45
                reasons.append(f"known_domain:{domain}")
                break

    keyword_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in lowered)
    if keyword_hits:
        kw_score = min(0.5, keyword_hits * 0.08)
        score += kw_score
        reasons.append(f"keywords:{keyword_hits}")

    reject_hits = sum(1 for pat in REJECT_PATTERNS if pat.search(text))
    if reject_hits and keyword_hits == 0:
        score -= 0.25
        reasons.append(f"reject_noise:{reject_hits}")

    if "algorand" in lowered:
        score += 0.15
        reasons.append("exact:algorand")

    score = max(0.0, min(1.0, score))
    in_scope = score >= threshold
    if not in_scope and not reasons:
        reasons.append("below_threshold")
    return ClassifierResult(score=round(score, 3), in_scope=in_scope, reasons=tuple(reasons))
