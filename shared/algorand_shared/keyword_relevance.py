"""On-topic keyword relevance: pure text/regex matching, zero external dependencies.

Moved here from `workers/app/modules/search/classifier/score.py` (2026-08-26)
alongside the rest of tonight's backend/artifact_priority gap-closing work.
`KNOWN_DOMAINS` and `keyword_hits()` are the two pieces of
`ecosystem_listed_score` (algorand_shared.artifact_priority) that had NO real
workers-only dependency in the first place -- score.py just happened to be
where they lived, because that's also where the *other*, genuinely
workers-only-flavored keyword tiers (`ALGORAND_KEYWORDS`/`GENERIC_KEYWORDS`
and `score_page`'s 0-1 classifier, which stay in score.py) live. Splitting
these out means `ecosystem_listed_score` can import them unconditionally in
BOTH backend and workers, instead of only succeeding in workers and failing
open to 0.0 everywhere else.

`score.py` re-exports everything below (`from algorand_shared.keyword_relevance
import ...`) so existing workers call sites (`from
app.modules.search.classifier.score import KNOWN_DOMAINS, keyword_hits`) keep
working unchanged -- same functions, one location, same pattern already used
for `algorand_shared.artifact_priority`/`artifact_store` since this session's
earlier moves.
"""

from __future__ import annotations

import re

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
        # ZeroSignal (private AI chat, settled per-request on Algorand): the
        # Algorand connection is only ever stated on ITS DISCOVERER's page
        # (txnlab.dev) — checked all 6 crawled pages of zerosignal.ai itself
        # (home, /chat, /login, /recover, /dev/calculator, /dev/analytics),
        # zero mentions of "algorand" anywhere, no explorer links either.
        # Owner-confirmed relevant (2026-07-22).
        "zerosignal.ai",
        # dark-coin.com: an NFT game powered by dark-coin.io, on Algorand —
        # owner-confirmed (2026-07-22); the .com marketing site doesn't state
        # the chain itself.
        "dark-coin.com",
        "dark-coin.io",
        # Sow & Reap: introduced on r/AlgorandOfficial and other official
        # Algorand channels, but not stated on sowandreap.in's own pages —
        # owner-confirmed (2026-07-22).
        "sowandreap.in",
    }
)

# "algo" and "asa" are Algorand's own name/ticker and asset-type abbreviation
# -- but, word-boundaries or not, both are ALSO ordinary standalone words in
# other languages ("algo" = "something" in Spanish/Portuguese; "asa" = "wing"
# in Portuguese, a name in several others). Proper \b-boundary matching (see
# _compile_keyword_pattern) already fixed the FRAGMENT-collision bug (matching
# inside "casa"/"algorithmically"/"algorand" itself) — it can't fix a page
# that's simply written in a language where these are real, common,
# completely unrelated words. protegecoin.com.br (Portuguese-language Bitcoin
# content) still landed a real, boundary-correct 0.26 purely from genuine
# "algo"/"asa" hits with zero Algorand relevance (root-caused 2026-08-26,
# alongside the fragment-matching fix -- capping the family's ceiling there
# was the first pass, this is the second: don't trust these two words at the
# same weight as an unambiguous term in the first place). Kept in their own
# tier, capped like GENERIC_KEYWORDS (score.py), rather than folded into
# either family.
AMBIGUOUS_KEYWORDS: tuple[str, ...] = (
    "algo",
    "asa",
)


def _compile_keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Word-boundary regex for one keyword-family entry.

    The original list matched with plain ``str.count`` substring search, with
    a couple of entries (" algo", "algo ") leaning on a leading/trailing
    space as a cheap boundary approximation. That approximation only checked
    ONE side, so it silently matched keyword FRAGMENTS inside unrelated
    words: " algo" (leading space, no trailing check) matched the "algo" in
    "determined algorithmically", and "ppos" matched inside "opposed" — both
    live on calnix.gitbook.io's real Aave/Ethereum documentation pages,
    root-caused 2026-08-26 alongside the generic-keyword-family fix in
    score.py. The same gap bit "asa " and " algo" on protegecoin.com.br's
    Portuguese text, where "asa" is a substring of "casa" (house) and "algo"
    is itself an ordinary Portuguese word ("something") — proper word
    boundaries on both sides reject the "casa" collision (no boundary between
    'c' and 'asa') and correctly still match a real standalone Portuguese
    "algo", same as RELEVANCE_KEYWORDS/_RELEVANCE_KEYWORD_RE below already
    does for the separate crude hit-counter.

    "algo"/"asa" are now each a SINGLE list entry (the old " algo"/"algo "
    pair used to double-count one standalone "algo" mention on purpose, to
    reward repetition) — collapsing that duplication matters here specifically
    because "algo" and "asa" are both real, unremarkable words in Portuguese
    ("something" and "wing"), so a non-English off-topic page can rack up
    genuine, boundary-correct hits on them with zero Algorand relevance
    (protegecoin.com.br, above). Halving that family's ceiling keeps such a
    page well clear of both score.py's DEFAULT_THRESHOLD and
    FRONTIER_CONTENT_PROMOTE_SCORE instead of merely below the latter by a
    slim, crawl-to-crawl-fragile margin.
    """
    stripped = keyword.strip()
    if stripped == "arc-":
        # ARC citations are always "ARC-<digits>" (arc-3, arc-4, arc-200...);
        # require the leading boundary so this doesn't fire inside compound
        # words ending "...arc-", but don't demand a boundary on the right
        # since there's no natural one right after the hyphen.
        return re.compile(r"\barc-", re.IGNORECASE)
    return re.compile(rf"\b{re.escape(stripped)}\b", re.IGNORECASE)


_AMBIGUOUS_KEYWORD_RE: tuple[re.Pattern[str], ...] = tuple(
    _compile_keyword_pattern(kw) for kw in AMBIGUOUS_KEYWORDS
)


# The single source of Algorand keyword truth for crude hit-counting (storage
# score + the enqueue/quality floor). Matched on WORD BOUNDARIES so "algo" no
# longer fires on "algorithm" and "asa" not on "nasa" — the substring matching
# that the two ad-hoc keyword lists in publish_classifier used to do. score.py
# keeps its own phrase-tuned POSITIVE_KEYWORDS for the weighted 0-1 classifier.
#
# "algo" and "asa" are deliberately NOT in this list — see AMBIGUOUS_KEYWORDS
# above: both are ordinary standalone words in Spanish/Portuguese ("algo" =
# "something", "asa" = "wing"), so a non-English, non-Algorand page can rack
# up genuine, word-boundary-correct hits on them alone. keyword_hits() below
# still counts them, but folded into _AMBIGUOUS_KEYWORD_RE at a fraction of a
# real hit's weight (same 0.3 ratio score_page uses for its own ambiguous
# tier), so repeated "algo"/"asa" mentions with zero other signal can never
# alone clear is_content_quality_sufficient's floor (root-caused 2026-08-26,
# same protegecoin.com.br-class Portuguese page that motivated the score_page
# fix).
RELEVANCE_KEYWORDS: tuple[str, ...] = (
    "algorand",
    "defi",
    "mainnet",
    "testnet",
    "microalgo",
    "ppos",
    "algod",
)
_RELEVANCE_KEYWORD_RE: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"\b{kw}\b", re.IGNORECASE) for kw in RELEVANCE_KEYWORDS
)


_KEYWORD_FAMILY_CAP = 3

# Same de-weighting ratio score_page applies to its AMBIGUOUS_KEYWORDS tier
# (0.03/hit vs 0.10/hit for a genuine Algorand-specific term -- 0.3). At this
# weight, two fully-capped ambiguous keywords (_KEYWORD_FAMILY_CAP each) add
# at most 2 * 3 * 0.3 = 1.8 points here -- deliberately kept BELOW 2, not just
# below 3, so a long page (len>=300) padded entirely with "algo"/"asa" still
# can't clear is_content_quality_sufficient's relaxed >=2 floor on the
# ambiguous tier alone.
_AMBIGUOUS_RELEVANCE_WEIGHT = 0.3


def keyword_hits(text: str) -> float:
    """Weighted on-topic keyword signal in ``text`` (word-boundary matched, so no algorithm/nasa false positives). Each RELEVANCE_KEYWORDS entry contributes its OCCURRENCE count, capped at ``_KEYWORD_FAMILY_CAP`` so one repeated term can't inflate the score without bound — but a page that says "Algorand" repeatedly now scores above one that name-drops it once, instead of both being flattened to the same single point. Root-caused 2026-07-24: urvote.ca's homepage says "Algorand" 2+ times in body copy (built-on-Algorand blurb, Startup Challenge mention) and nothing else on the family list, so the old presence-only count gave it 1 point regardless — same as a single incidental mention — and it failed the quality floor despite being genuinely, specifically Algorand-related. One shared helper so the storage score and the quality gate can't drift apart again.

    "algo"/"asa" hits (_AMBIGUOUS_KEYWORD_RE) are added on top at
    ``_AMBIGUOUS_RELEVANCE_WEIGHT`` instead of full weight — see the
    RELEVANCE_KEYWORDS comment above for why.
    """
    if not text:
        return 0.0
    specific_hits = sum(
        min(len(pat.findall(text)), _KEYWORD_FAMILY_CAP) for pat in _RELEVANCE_KEYWORD_RE
    )
    ambiguous_hits = sum(
        min(len(pat.findall(text)), _KEYWORD_FAMILY_CAP) for pat in _AMBIGUOUS_KEYWORD_RE
    )
    return specific_hits + ambiguous_hits * _AMBIGUOUS_RELEVANCE_WEIGHT
