from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

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
        "txnlab.dev",
        "deflex.fi",
        # Ecosystem services whose own sites never mention Algorand: HesabPay
        # (Afghanistan payments, runs on Algorand — its wallet marketing page
        # has ZERO chain mentions) and Sealed (multi-chain messenger, seeded
        # deliberately). Without a domain anchor their relevance scores 0, so
        # their one-shot discovery rows died at priority ~0 and every future
        # content-update diff would fail CONTENT_UPDATE_RELEVANCE_FLOOR.
        "hesab.com",
        "hesab.af",
        "sealed.channel",
        # Lofty (fractional real estate on Algorand): homepage is a JS shell
        # with zero chain mentions in the served HTML — preview-scored 0 and
        # sat in the pending frontier pool.
        "lofty.ai",
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
    re.compile(r"\bcasino", re.IGNORECASE),
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

# The single source of Algorand keyword truth for crude hit-counting (storage
# score + the enqueue/quality floor). Matched on WORD BOUNDARIES so "algo" no
# longer fires on "algorithm" and "asa" not on "nasa" — the substring matching
# that the two ad-hoc keyword lists in publish_classifier used to do. score_page
# keeps its own phrase-tuned POSITIVE_KEYWORDS for the weighted 0-1 classifier.
RELEVANCE_KEYWORDS: tuple[str, ...] = (
    "algorand", "algo", "asa", "defi", "mainnet", "testnet",
    "microalgo", "ppos", "algod",
)
_RELEVANCE_KEYWORD_RE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"\b{kw}\b", re.IGNORECASE) for kw in RELEVANCE_KEYWORDS
)


def keyword_hits(text: str) -> int:
    """Count of distinct on-topic keyword families present in ``text`` (word-
    boundary matched, so no algorithm/nasa false positives). One shared helper so
    the storage score and the quality gate can't drift apart again."""
    if not text:
        return 0
    return sum(1 for pat in _RELEVANCE_KEYWORD_RE if pat.search(text))


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
        score += min(0.5, keyword_hits * 0.08)
        reasons.append(f"keywords:{keyword_hits}")

    reject_hits = sum(1 for pat in REJECT_PATTERNS if pat.search(text))
    if reject_hits and keyword_hits == 0:
        score -= 0.25
        reasons.append(f"reject_noise:{reject_hits}")

    spam_hits = seo_spam_hits(text)
    if spam_hits:
        score -= min(0.45, spam_hits * 0.15)
        reasons.append(f"seo_spam:{spam_hits}")

    if "algorand" in lowered:
        score += 0.15
        reasons.append("exact:algorand")

    score = max(0.0, min(1.0, score))
    in_scope = score >= threshold
    if not in_scope and not reasons:
        reasons.append("below_threshold")
    return ClassifierResult(score=round(score, 3), in_scope=in_scope, reasons=tuple(reasons))
