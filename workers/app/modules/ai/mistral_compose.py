"""The writer's research -> compose -> grade/revise loop and its prompts."""

from __future__ import annotations

import contextlib
import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

from app.core.config import MISTRAL_MAX_SOURCE_CHARS
from app.modules.ai.mistral_client import (
    MistralClient,
    MistralCreditError,
    MistralError,
    get_mistral_client,
    get_mistral_digest_client,
    get_mistral_research_client,
)
from app.modules.ai.reference_block import append_reference_block
from app.modules.ai.story_spike import StorySpikedError
from app.modules.metrics.price_metrics_store import load_mistral_context
from app.modules.newspaper.weekly_digest import WeeklyDigestContext

logger = logging.getLogger(__name__)

# Bump whenever a compose prompt in this module changes materially (system
# guidelines, _ARTICLE_FORMAT_RULES, recency/profile rules, etc). Stamped onto
# every stored article so analytics can correlate a prompt edit with a shift in
# grades/engagement instead of guessing from deploy timestamps.
PROMPT_VERSION = "2026-07-20"


@dataclass(frozen=True)
class MistralArticleFields:
    """The composed article's title/summary/body plus grading metadata."""

    title: str
    summary: str
    body: str
    tags: tuple[str, ...] = ()
    prompt_version: str = PROMPT_VERSION
    # Deterministic grade from the two-stage compose's grade/revise pass (Stage
    # 3+4 in _review_and_revise) — the post-revision grade when a revision ran,
    # else the initial one. None when WRITER_REVIEW_ENABLED is off or grading
    # itself failed. Lets a caller (e.g. the publish gate) act on the SAME grade
    # the writer saw, instead of recomputing it blind.
    heuristic_grade: dict | None = None
    # Writer-declared urgency (mark_breaking_news tool, replaces the
    # deterministic keyword classifier disabled 2026-07-17) — None unless the
    # writer actually called the tool this compose.
    breaking_reason: str | None = None
    # Writer-confirmed alert class ("scam_alert"/"network_incident" via the
    # confirm_alert_topic tool) — None unless confirmed. The keyword topic
    # classifier still routes queue rows, but reader-facing alert tags and
    # the scam-topic match-key carve-out require this confirmation
    # (2026-07-18: the Foundation's own homepage rebrand got tagged
    # scam-alert off a research-paper blurb mentioning "malicious servers").
    confirmed_alert: str | None = None
    # Body links a domain the research recorded as DNS-unresolvable and that
    # still doesn't resolve (defunct_entity_gate) — non-empty forces the draft
    # into human review instead of auto-publishing (MyAlgo incident 2026-07-19).
    defunct_domains: tuple[str, ...] = ()
    # Unsourced hard specifics (traction/funding counts, named partners) not
    # grounded in the research (unsourced_specifics_gate, ENFORCE mode) — a
    # non-empty reason forces the draft into human review instead of
    # auto-publishing (GoPlausible incident 2026-07-20).
    unsourced_hold_reason: str = ""


# The single hardest accuracy rule. The small model, told to write full-depth,
# human-sounding journalism, will otherwise invent plausible PR quotes for real
# executives it recognizes from pre-training (e.g. fabricating statements from a
# named CEO/CFO) — the most dangerous hallucination because it reads as true.
_STRICT_QUOTE_GROUNDING = (
    "STRICT QUOTE GROUNDING: never include a quotation, or any sentence presented "
    "as someone's direct words, unless that exact word-for-word text is visible in "
    "a tool result. Do NOT invent, reconstruct, paraphrase-into-quotes, or simulate "
    "a quote — not even a plausible-sounding statement from a CEO, CFO, or "
    "foundation you recognize from training data. If your verified research has no "
    "verbatim quote, describe the action in objective third-person prose instead. A "
    "fabricated quote is the single most damaging error you can make.\n"
)

_BANNED_LEXICON = (
    "\nBANNED PHRASES: Do not use: groundbreaking, revolutionize, game-changer, "
    "seamless, unleashes, paves the way, cutting-edge, innovative (when used as "
    "empty filler), exciting, thrilled to announce. State what the technology does "
    "objectively.\n"
)


def _writing_guidelines(today: str) -> str:
    """Core editorial contract shared by scrape and assignment compose paths."""
    return (
        "WRITING GUIDELINES:\n"
        "- Tone: Professional, objective, educational, and dense with information. "
        "Strictly avoid sensationalism, marketing speak, and fluffy language. The "
        "writing must sound distinctly human and authoritative. Educational means "
        "actually teaching the reader something — when the story rests on a concept "
        "they may not already know (technical or not), explain it in plain language "
        "rather than assuming or name-dropping it.\n"
        "- Honest but empathetic: when a small or early-stage project has real "
        "shortcomings (thin TVL, low adoption, an unfinished feature), report them "
        "plainly — never soften a verified fact — but the goal is to inform readers, "
        "not to humiliate a small team for shipping something real. Where warranted, "
        "let the piece close with a fair, honest note of hope or potential rather "
        "than pure negativity.\n"
        "- NO REPETITION, ANYWHERE (the one rule every other repetition note in "
        "these instructions points back to — this is the single source of truth): "
        "state each specific fact, number, or judgment ONCE, in the single section "
        "it belongs to. This applies to a criticized shortcoming exactly as much as "
        "a data point — don't pile criticism on by repeating it in every section "
        "either. A comparison table, a 'why this matters' analysis, and a "
        "reader-guidance section must each ADD something the reader doesn't already "
        "have — not independently re-derive the same observation from scratch (e.g. "
        "'fees are undisclosed' or 'volume is self-reported and unverified' showing "
        "up nearly verbatim in the prose, the table, AND a bulleted list is a "
        "structure that has failed this rule, root-caused 2026-07-15). If a later "
        "section would just restate an earlier point, either cut it from that "
        "section or phrase it as a callback ('as noted above') instead of rewriting "
        "it as if for the first time. This applies during revision too: fixing one "
        "issue is never license to reintroduce a repeat elsewhere.\n"
        "- Narrative Synthesis: Identify the connective tissue between your research "
        "findings. Weave distinct developments into a unified, flowing narrative. "
        "You are strictly prohibited from using bulleted lists to summarize events, "
        "features, or updates.\n"
        "- Vary your lede: do NOT open the article with the newspaper's standard "
        "layer-1 pitch (Pure Proof-of-Stake, sub-3-second finality, sub-cent fees). "
        "Nearly every recent piece opened with that same paragraph, and readers "
        "scrolling the feed see the identical intro on every story (observed "
        "2026-07-16). Lead with what is specific to THIS story — the tension, the "
        "actor, the change, the stakes. Protocol mechanics belong mid-piece, in the "
        "section where they earn their place explaining this story's friction; "
        "mention them there, once, not as a ritual opener.\n"
        "- Technical Stakes & Depth: Ground abstract announcements in specific "
        "architecture. You must explicitly bridge the announcement to the underlying "
        "layer-1 architecture. If the verified material mentions a partnership or "
        "deployment, identify the specific legacy friction being eliminated AND "
        "explain why Algorand's specific infrastructure (e.g., throughput, Pure "
        "Proof-of-Stake finality, ASA tokenization, state proofs) is the logical "
        "solution — do not just name-drop the foundation. When sources are thin "
        "you may draw on your expert knowledge of Algorand's layer-1 "
        "infrastructure to supply that explanation. RELEVANCE GATES THE "
        "BRIDGE: only explain a mechanic that genuinely bears on THIS story's "
        "friction — state proofs do not fix wallet phishing, a PPoS explainer "
        "does not deepen a partnership announcement. A wrong-context mechanic "
        "reads as filler and damages trust more than saying less; when no "
        "layer-1 mechanic is truly implicated, skip the bridge rather than "
        "manufacture one. Do not invent false quotes, partnerships, or numbers, "
        "but DO explain technology the story actually rests on.\n"
        "- Concrete Scenarios: Translate abstract blockchain concepts into concrete "
        "operational scenarios to make the implications vivid for the reader.\n"
        "- Diff noise is not news: mechanical artifacts in a page diff — canonical "
        "tags, hostname capitalization, tracking parameters, CSS/asset renames — "
        "are never 'substantive updates'. Report only changes a reader could act "
        "on or care about; if a diff line needs the word 'normalizing' to sound "
        "meaningful, drop it.\n"
        "- Audience: Intelligent general readers who are NOT crypto specialists. "
        "Briefly explain blockchain/DeFi/Algorand jargon in plain language on first "
        "use, spell out acronyms once, and never assume prior crypto knowledge.\n"
        "- EXPLAIN YOUR OWN FRAME: if your headline or lede is built around a named "
        "concept from OUTSIDE crypto — a philosophical paradox, a historical "
        "reference, a literary or cultural allusion — pulled from the source "
        "material's own framing (a project's blog post title, its branding), you "
        "must define that concept in plain language on first use, exactly like "
        "crypto jargon, and actually develop it: return to it at least once more "
        "in the body to show HOW it connects to the actual subject, not just "
        "name-drop it in the title and abandon it. Root-caused 2026-08-05 "
        "(Hampelman NFT article): the headline and lede invoked 'Ship of Theseus' "
        "and a token was named after 'Memento Mori', but neither was ever "
        "explained or tied back to the argument — a reader unfamiliar with either "
        "reference had no way to understand why the piece led with it.\n"
        f"- Recency & Temporal Anchoring: Today is {today} (UTC). Source pages often "
        "contain outdated figures. Never present a number, price, ranking, TVL/volume, "
        "or 'current' claim as present-day unless the source clearly dates it to "
        "recently. If source material is several months old, DO NOT write as if the "
        "announcement just happened. For figures that should reflect the present "
        "(ALGO price, market data, chain stats), prefer live tools over stale page "
        "numbers.\n"
        "- Accuracy: Use only facts from the source material; never invent quotes, "
        "numbers, or on-chain events. Never put raw transaction IDs, round numbers, "
        "or 'Service:' labels in the body.\n"
        "- NO UNSOURCED SPECIFICS: Adoption and traction claims — counts of users, "
        "issuers, customers, holders, developers, wallets, events, hackathons, or "
        "integrations; TVL, funding raised, valuation, revenue; and named partners, "
        "backers, or investors — are only permitted if that exact figure or name is "
        "visible in a tool result. Do NOT supply a plausible number or a likely "
        "partner from your own knowledge. Crucially, ABSENCE IS DATA: if a site's "
        "usage counters read 0 (including '0+', '0K+') or a 'partners'/'customers'/"
        "'backers' section is empty, that zero or emptiness is the fact — report that "
        "the metrics are unpublished or the counters read zero, or omit the claim "
        "entirely. Never overwrite an observed zero or an empty section with an "
        "impressive figure or a partner name you did not see in the sources.\n"
        "- STRICT QUOTE GROUNDING: Never include a quotation unless that exact "
        "word-for-word text is visible in a tool result.\n"
    )


def _writer_system_lead(*, assignment: bool = False) -> str:
    if assignment:
        return (
            "You are a senior investigative journalist for a premier "
            "Algorand-focused media outlet. You have been assigned an original story "
            "by an editor — it has NOT been pre-researched, so your job is to "
            "investigate it yourself with your tools and then write a dense, "
            "high-signal article from what you verify.\n\n"
        )
    return (
        "You are a senior investigative journalist for a premier "
        "Algorand-focused media outlet. Your goal is to synthesize verified research "
        "into captivating, high-signal, and highly professional news articles based "
        "on provided source material.\n\n"
    )


def _writer_system_prompt(today: str, *, assignment: bool = False) -> str:
    assignment_extra = ""
    if assignment:
        assignment_extra = (
            "- Editorial briefs are starting pointers, NOT verified fact — confirm "
            "everything via your tools this session before relying on them. If your "
            "research cannot substantiate the assigned topic, say so plainly rather "
            "than inventing content.\n"
        )
    return (
        _writer_system_lead(assignment=assignment)
        + _writing_guidelines(today)
        + assignment_extra
        + _BANNED_LEXICON
        + _ARTICLE_FORMAT_RULES
        + "\n"
        + _JSON_ONLY
    )


# Shared guidance appended to the system prompt when the agentic tool loop is on.
# The ALGO PRICE/MARKET RULE is deliberate: the small model used to fetch the
# price for every story and pad unrelated articles (a dev tool, a partnership)
# with an irrelevant price table, which made them look automated.
# ── Investigation prompt ────────────────────────────────────────────────────
# Refactored 2026-07-16 (owner request): the rules below accreted one
# production incident at a time into a single 150-line string, and the
# research-phase variant was derived by string-splitting it at the literal
# text "SELF-REVIEW (MANDATORY" — fragile, and impossible to see which prompt
# a given rule reached. Now each thematic block is a named constant and
# _TOOLS_GUIDANCE / _RESEARCH_PHASE_GUIDANCE compose them explicitly:
#
#   _RESEARCH_MISSION_AND_ROUTING   what tools exist and when to reach for them
#   _VERIFICATION_DISCIPLINE        legitimacy stories, chaining, completeness
#   _METRICS_DISCIPLINE             market-metrics restraint + numeric skepticism
#   _SOURCING_AND_FRAMING_RULES     liveness, citations, memory-not-a-source,
#                                   one-product-one-source, undocumented
#                                   mechanics, self-published claims, RWA claims
#   _NO_FABRICATION                 the length-vs-invention contract
#   _STRICT_QUOTE_GROUNDING         (defined above, shared with other prompts)
#   _FEEDBACK_CHANNELS              suggest_tool + report_compose_issue
#   _SELF_REVIEW_RULES              single-stage only — the two-stage research
#                                   pass has no draft to review yet
#   _RESEARCH_PHASE_ADDENDUM        research-pass-only: adequacy floor,
#                                   truncation discipline, "don't write yet"
#
# Rule text is deliberately verbatim from before the refactor — every block is
# pinned by tests in test_compose_prompt_grounding.py naming the incident it
# guards against. Add new rules to the section they belong to; both composed
# prompts pick them up automatically (or exactly one, if that's what you want).

_RESEARCH_MISSION_AND_ROUTING = (
    "\n\nThe source material is your STARTING POINT, not the finished article: it "
    "identifies the story but is often thin, dated, or padded with boilerplate. Use "
    "the provided facts only where they are genuinely the story, and reach for tools "
    "to verify claims, pull CURRENT figures (price, chain stats, TVL), and add the "
    "context a reader needs. Use a tool only when it "
    "genuinely serves THIS story. Research like a journalist: search_web to "
    "discover context and sources you were not handed, then fetch the best URL; "
    "source_history for this platform's prior coverage of the same source "
    "(continuity, no repetition); search_bluesky to gauge community sentiment "
    "when the story turns on what people think; discourse_forum for what a "
    "project's community is discussing right now. VERIFY ON-CHAIN rather than "
    "trusting a claim: lookup_account (balances/holdings), lookup_asset (token "
    "supply and who controls it), lookup_application (a contract's decoded global "
    "state — e.g. a governance app's live proposal/vote tallies). For deeper "
    "digging, move through "
    "provenance -> identity -> assets -> history -> network: verify a leaked file "
    "with extract_document_metadata; prove what a "
    "page said with fetch_archive_snapshot; unmask a site with "
    "resolve_domain_infrastructure; check people with screen_sanctions_and_pep; "
    "pierce companies with query_corporate_registry; pull cases with "
    "query_court_dockets; check leaks with search_leak_databases; see who really "
    "builds a project (top contributors, releases, commits) with github_activity.\n"
)

_VERIFICATION_DISCIPLINE = (
    "LEGITIMACY STORIES — when a piece turns on whether a token, coin or project is "
    "real/safe/worth attention, you MUST actually RUN the verification tools before "
    "you conclude, not just describe them: lookup_asset + lookup_account on the "
    "token/creator for the real on-chain footprint, github_activity for genuine "
    "development, and search_leak_databases / screen_sanctions_and_pep on the named "
    "team. Reaching for suggest_tool here is wrong — you already have these tools.\n"
    "TOOL DISCIPLINE: do not re-call the SAME tool with the SAME arguments — its "
    "data will not change and a duplicate call is ignored. But DO chain tools "
    "freely: follow the evidence across as many steps as the story needs, using "
    "one tool's result to choose the next (e.g. fetch_url a project's docs to find "
    "its governance app id, then lookup_application to read that app's on-chain "
    "vote state), varying the arguments to dig deeper. "
    "Stop when you have gathered AND verified the facts this story needs — not at "
    "an arbitrary call count.\n"
    "RESEARCH COMPLETENESS: when a search result surfaces a DISTINCT product, "
    "subdomain, or URL clearly connected to the subject — not just another page "
    "about the same thing you already found — fetch and evaluate it before "
    "concluding research. A related product you never even opened is a bigger gap "
    "than an unanswered question you flagged honestly. This matters most for "
    "first-coverage/new-service stories, where missing a major feature of the "
    "subject is worse than a routine update missing a minor detail.\n"
)

_METRICS_DISCIPLINE = (
    "ALGO PRICE/MARKET RULE: do not add market or chain metrics by default. This "
    "covers ALGO price AND network stats like TVL, volume, node/validator counts and "
    "block times. Fetch and mention any of them ONLY when the metric materially helps "
    "the reader understand THIS story — e.g. a supply-cap or tokenomics change and its "
    "inflation impact, a treasury/funding move, a markets/trading story, or a metric "
    "that is itself the news (a TVL milestone). For product launches, dev tools, "
    "partnerships, governance procedure, NFTs and general news where such numbers add "
    "nothing, omit them — do not append a metrics table or a price line to prove the "
    "piece is current. When in doubt, leave it out.\n"
    "NUMERIC SKEPTICISM: read every number in context before using it — a figure is "
    "only worth including if it means something. A $4,000 TVL, a dozen holders, or a "
    "few hundred dollars of daily volume is NOT traction; say so plainly ('negligible', "
    "'effectively no liquidity') rather than reporting a trivial figure neutrally as if "
    "it were a notable data point. When two sources give materially different numbers "
    "for the SAME metric on the SAME asset (e.g. a price aggregator vs. the actual "
    "DEX/on-chain figure), that is a contradiction, not two facts — prefer the figure "
    "you can verify on-chain/on the actual DEX over a third-party aggregator, and never "
    "print both side by side as if they agree. If you cannot tell which is right, drop "
    "the one you cannot verify rather than including both.\n"
)

_SOURCING_AND_FRAMING_RULES = (
    "LIVENESS CHECK: weigh whether the project is actually ALIVE before choosing a "
    "tense and frame. Signals of dormancy: the primary site times out or errors on "
    "every fetch attempt, on-chain accounts show 'Offline' status with only dust "
    "balances, the newest content you can find is old, or there is no verifiable "
    "recent activity (trades, commits, posts) at all. If dormancy signals stack up, "
    "you MUST NOT write the piece as a current, thriving, forward-looking project — "
    "write it as a historical retrospective instead ('X was an initiative that...', "
    "'development appears to have stalled...'), state plainly what you could not "
    "verify as current, and never write promotional future-tense framing "
    "('is positioned to', 'is building toward') for a subject you could not confirm "
    "is still active.\n"
    "SUPERSESSION CHECK: a project being alive is not the same as a tool, "
    "library, or standard still being the CURRENT recommended way to do the "
    "thing it does. Before centering a piece on a specific tool/framework/SDK "
    "as 'the' way developers should do X, check for a newer successor from the "
    "same ecosystem (a rewrite, a 'migrate to', a deprecation notice, a docs "
    "page recommending something else, a release history that goes cold while "
    "a sibling project's does not). If you find one, name it and frame the "
    "older tool honestly — 'X is still supported, but Y is now the actively "
    "developed/recommended option' — rather than writing X up as the current "
    "state of the art. Every individual fact about X can be true and the piece "
    "still misleads a reader deciding what to use today if the successor goes "
    "unmentioned (root-caused 2026-08-04: a PyTeal explainer was fact-accurate "
    "but omitted that Algorand Python/Puya has superseded it as the recommended "
    "path, reading as current guidance when it was a stale snapshot).\n"
    "JOURNALISM RULES: only state facts a tool actually returned; cite the "
    "tool/source in the text; never assert wrongdoing about a named person or "
    "company unless a tool returned concrete evidence; when a SPECIFIC claim is "
    "unverified, hedge or drop THAT claim — not the rest of the story. The Source "
    "list is the same rule applied to citations: a URL you fetched and that errored "
    "or timed out was NEVER READ — do not list it as a source. A 200 response with "
    "no substantive content (a cookie-consent notice, a bare nav menu, a paywall "
    "stub) is the SAME as an error — you did not read that page either, so do not "
    "characterize what it 'says' or 'reports'; a search-result snippet naming the "
    "same source is a weaker but real citation, use that instead and say so. Only "
    "cite pages you successfully read, or attribute the fact to where it actually "
    "came from (a search result snippet, an on-chain lookup, an archived copy).\n"
    "MEMORY IS NOT A SOURCE: a specific fact you recognize — a real product name, "
    "a transaction, a price, a date — is not verified just because it feels "
    "familiar from training. If it did not appear in an actual tool result THIS "
    "session, it is unverified even when it turns out to be true; never cite it "
    "as coming from a page or tool you did not actually see it in. Either call "
    "the tool that would confirm it now, or leave it out — a plausible-sounding "
    "recollection presented as a live citation is exactly what erodes trust when "
    "a reader checks it against the source and it does not match.\n"
    "ONE PRODUCT, ONE SOURCE: search results routinely surface SIMILAR or "
    "competitor products (same category, other chains). A fact belongs ONLY to "
    "the product whose page/domain returned it — check the result's URL before "
    "using it. Never transplant a feature, guarantee, or mechanism from a "
    "lookalike product onto the story's subject, and never fill a gap in how "
    "THIS product works by assuming it works like a well-known analogue.\n"
    "UNDOCUMENTED MECHANICS: if your sources do not document how something "
    "works (docs missing, page is a stub), describe it only at the level the "
    "sources support and say plainly that the details are not yet documented. "
    "An honest gap is fine; a reconstructed mechanism is fabrication.\n"
    "SELF-PUBLISHED CLAIMS: a project's claims about itself (its own site, "
    "forum posts, token descriptions) are attributed statements, not facts — "
    "write 'according to the project' / 'the team says', and for speculative "
    "or token-launch subjects include the risk context a fair journalist "
    "would; the positive house tone never overrides this.\n"
    "NAMED REAL-WORLD ASSET CLAIMS: when a project claims to tokenize, "
    "fractionalize, or sell ownership/yield rights in a SPECIFIC, NAMED "
    "real-world physical asset (a named dam, building, mine, farm, etc.), "
    "treat that as an extraordinary claim, not a fact to relay — a small "
    "crypto project essentially never holds any actual legal claim to major "
    "infrastructure (a named dam is almost always operated by a government "
    "agency or utility, not sold fractionally through an app). Check the "
    "project's own marketing for tells that the ownership language is "
    "fictional/gamified branding rather than literal ('game', 'play2earn', "
    "or 'card' used for what is framed as a real asset is a strong signal to "
    "treat it as flavor text). If you cannot independently verify any actual "
    "legal or ownership relationship between the project and the named "
    "asset, do not write the piece as though real asset-backed ownership "
    "occurred — state plainly that the 'ownership' is a token/game mechanic "
    "with no verified connection to the real institution, or drop the "
    "specific asset-ownership framing entirely.\n"
)

_NO_FABRICATION = (
    "NO FABRICATION (but DO use what you found): never invent, guess at, or embellish "
    "facts to fill space or hit a length target. Within that bound, fully develop "
    "every angle your research actually supports — explain mechanisms, who is "
    "affected, and why it matters. Match length to verified substance: never pad "
    "with fluff, but never cut relevant, verified context short either. A thin "
    "source earns a short piece; a rich one earns a thorough one. Stop only when the "
    "verified material is genuinely exhausted. Making things up is the one thing "
    "that is never acceptable.\n"
    "SUPPLY-SHARE ARITHMETIC: never hand-compute a holder's percentage share of an "
    "ASA's total supply by dividing lookup_asset's total by lookup_account's raw "
    "holding yourself — the units are almost never adjusted for decimals the same "
    "way, and doing this by hand produced a real fabricated claim (a holder "
    "reported at double their true share). Call get_asset_holder_share(asset_id, "
    "address) and quote its share_pct verbatim; if you cannot call it, state the "
    "raw holding and total without computing a percentage at all.\n"
)

_FEEDBACK_CHANNELS = (
    "TOOL GAPS (do this every story, do not skip): after researching, ask whether "
    "any fact, number, or source would have made THIS story sharper, deeper, or "
    "better verified if a tool could have fetched it. Call suggest_tool for EACH "
    "such gap — even when you finished the article without it. This is NOT only for "
    "hard walls: a fact you worked around with a weaker source, or could verify less "
    "than you wanted, counts too. suggest_tool returns no data and never blocks the "
    "article — naming these gaps is part of the job, not an exception. Before "
    "calling it, check the tool list you were actually given this session — "
    "suggest_tool is for a capability that does not exist yet, not a capability "
    "you forgot you have; calling it on a tool already in your list wastes a round "
    "and gets auto-corrected anyway.\n"
    "FETCH_URL AS A GENERAL FALLBACK: before concluding a capability does not "
    "exist, try fetch_url directly — it is a plain HTTP GET and covers most "
    "specific-data needs a dedicated tool would: a specific report/document at a "
    "guessed or discovered URL, a project's own API endpoint (many publish a "
    "public JSON API at a predictable path like /api/stats or /api/v1/...), an "
    "RSS/Atom feed, a GitHub raw file. Reach for suggest_tool only after a direct "
    "fetch attempt at the specific resource has failed, not merely because no "
    "tool is named exactly for this task.\n"
    "PIPELINE FEEDBACK: when instructions, source material, an existing tool, or "
    "the research→write handoff genuinely blocked or degraded your work, call "
    "report_compose_issue with a specific category and summary (use suggest_tool "
    "only for capabilities that do not exist yet). This feeds engineering — report "
    "real friction, not nitpicks, then continue.\n"
)

_SELF_REVIEW_RULES = (
    "SELF-REVIEW (MANDATORY — every article, no exceptions): you MUST call "
    "review_draft at least once before you finish; do NOT output the final JSON "
    "until you have. When the draft is complete, call review_draft with your "
    "title and full body. It returns a 0-10 grade, "
    "per-dimension subscores (novelty, relevance, recency, length, "
    "structure) and a list of concrete issues. If the grade is below ~7 or any "
    "issues are listed, REVISE the draft to fix them — work toward the target "
    "length the review reports (a well-developed piece, not a short stub) WITHOUT "
    "inventing facts to pad it, and give it scannable structure (section headings plus "
    "a Markdown table when comparative data is present — never narrative bullet lists). "
    "If you cannot reach the length on real material, "
    "leave it shorter. "
    "You may call review_draft at most twice (initial check, then one re-check "
    "after revising). Then output the final JSON article."
)

_RESEARCH_PHASE_ADDENDUM = (
    "\nRESEARCH PHASE ONLY: right now your job is to gather and verify the "
    "facts THIS story needs using the tools — do NOT write the article yet. Chain "
    "as many tool calls as the story needs, letting each result guide the next; "
    "do not stop at the first answer if a follow-up would sharpen or verify a key "
    "fact.\n"
    "RESEARCH ADEQUACY (earn your length): a single static landing page — or one "
    "stale profile you already have on file — is NOT enough to justify a "
    "well-developed article. Build an inventory of at least THREE distinct, "
    "verified sub-narratives before you are ready. If your primary source is thin, "
    "you MUST run at least TWO of these before stopping: search_bluesky for current "
    "community sentiment/reactions on the project; search_web for recent ecosystem "
    "integrations, partnerships or comparisons; a market/on-chain tool for live "
    "metrics when the story is financial. Finding an existing profile is a reason to "
    "look for what is NEW since it, not a reason to stop. If after genuine digging "
    "the tools surface no fresh facts, that is acceptable — stop calling tools; a "
    "structured Research Digest handoff follows in a separate step.\n"
    "TRUNCATION DISCIPLINE: If a tool result contains truncated: true or "
    "has_more: true, assume critical technical data may be buried in the unread "
    "portion. If the first window does not definitively answer the research brief, "
    "you are strictly required to scroll the same URL: call fetch_url again with "
    "the SAME url and continue_reading=true. If scrolling still leaves gaps, follow "
    "specific sub-links or tighten search terms.\n"
    "NAMED-DOCUMENT FALLBACK: once you know a specific document's exact title "
    "(from a search snippet or secondary source, even if you cannot yet read the "
    "document itself), a generic listing/index page on the publisher's site "
    "404ing or archiving empty is NOT the end of the road — search for the exact "
    "title in quotes, which usually surfaces the document's own permalink even "
    "when the index page that would normally link to it is gone or unindexed. "
    "Only report the primary document as unreachable after that targeted search "
    "also comes up empty.\n"
    "When your research is adequate, stop calling tools. Do not write the article "
    "yet — synthesis will produce the handoff digest.\n"
)

# Single-stage/legacy loop: research + write + mandatory self-review in one pass.
_TOOLS_GUIDANCE = (
    _RESEARCH_MISSION_AND_ROUTING
    + _VERIFICATION_DISCIPLINE
    + _METRICS_DISCIPLINE
    + _SOURCING_AND_FRAMING_RULES
    + _NO_FABRICATION
    + _STRICT_QUOTE_GROUNDING
    + _FEEDBACK_CHANNELS
    + _SELF_REVIEW_RULES
)

# Two-stage research pass: same rules WITHOUT the self-review/"then WRITE"
# steering — the warm pass produces the article separately, and review_draft
# has no draft to grade yet.
_RESEARCH_PHASE_GUIDANCE = (
    _RESEARCH_MISSION_AND_ROUTING
    + _VERIFICATION_DISCIPLINE
    + _METRICS_DISCIPLINE
    + _SOURCING_AND_FRAMING_RULES
    + _NO_FABRICATION
    + _STRICT_QUOTE_GROUNDING
    + _FEEDBACK_CHANNELS
    + _RESEARCH_PHASE_ADDENDUM
)


def _format_research_digest(trace: list[dict]) -> str:
    """Condense the research trace into a ground-truth findings block for the warm generation pass: one line per tool call (tool, args -> result)."""
    import json as _json

    lines: list[str] = []
    for entry in trace[:25]:
        tool = str(entry.get("tool", ""))
        if not tool:
            continue
        try:
            args_s = _json.dumps(entry.get("arguments", {}), separators=(",", ":"))[:300]
            result_s = _json.dumps(entry.get("result", {}), separators=(",", ":"))[:1500]
        except Exception:
            args_s = str(entry.get("arguments", ""))[:300]
            result_s = str(entry.get("result", ""))[:1500]
        lines.append(f"- {tool}({args_s}) -> {result_s}")
    return "\n".join(lines)


# Stage-2 narrative guidance: turn the verified findings into an engaging
# journalistic profile, NOT a spec sheet. Expansion here is EXPLANATION of facts
# already found (high-signal), never padding or invention.
_NARRATIVE_GUIDANCE = (
    "\n\nWRITE IT AS JOURNALISM, NOT A SPEC SHEET — develop the verified findings "
    "into cohesive narrative prose. Scale the word count strictly to the volume of "
    "verified facts in the Research Digest — never pad with speculation, generic "
    "industry background, or marketing filler to hit a length target. If the "
    "verified material is brief, write a dense, brief article. Three rules:\n"
    "1. TRANSLATE TECHNICAL FINDINGS: when a finding is technical (an SDK, a token "
    "standard, a protocol like x402, smart-contract/escrow mechanics), don't just "
    "name it — spend 1-2 sentences on what it ENABLES and why it matters to a "
    "developer or business using the platform.\n"
    "2. PROBLEM -> SOLUTION FRAME: ground the opening in the real-world friction "
    "the project addresses — contrast the legacy pain (e.g. multi-day settlement, "
    "intermediary fees) with the specific chain mechanics that resolve it (e.g. "
    "sub-3-second finality — Algorand's dynamic round times average ~2.8s as of "
    "2026, NOT the older '4-second' figure some pretrained knowledge still cites "
    "— sub-cent fees). When verified findings document the mechanics, "
    "use them; when sources are thin, you MUST still complete the frame using your "
    "expert knowledge of Algorand layer-1 infrastructure — never invent quotes, "
    "partnerships, or product-specific guarantees, but DO explain why Algorand's "
    "protocol mechanics address the friction.\n"
    "3. CONNECT THE DISCOVERIES: smooth transitions so it reads as ONE story — link "
    "the user-facing product to the underlying tech you uncovered (e.g. how the web "
    "app sits on the open-source library / escrow contracts).\n"
    "4. DATA PRESENTATION: You are strictly prohibited from using bulleted lists. "
    "When presenting multi-item data (curriculum pillars, comparative metrics, "
    "feature sets), you MUST isolate it into a Markdown table with explicit columns "
    "for 'Concept' and 'Real-World Implication' — never bury multi-item data in "
    "dictionary-style paragraphs or comma-separated sentences.\n"
    "5. CHART: if the Research Digest's ### Chart section contains a fenced "
    "```chart block (not 'None'), paste it VERBATIM into the body at the point "
    "most relevant to the data it shows — you have no tools in this stage and "
    "cannot regenerate or verify new chart numbers, so never re-derive, "
    "reformat, or invent chart JSON yourself. If the Chart section says "
    "'None', do not fabricate a chart.\n"
    "This is EXPANSION BY EXPLANATION — never invent external facts, quotes, or "
    "partnerships, and still no generic filler. This includes specific data points "
    "— a named transaction, a price, a date — that are not already in the Research "
    "Digest above: the Digest is your complete source of truth for facts. Anything "
    "more specific than what it contains, however plausible or familiar it feels, "
    "is fabrication, not expansion.\n"
    "GROUNDING RULES for the findings block: each finding belongs to the "
    "product/domain in its URL — search results often include SIMILAR or "
    "competitor products, and their features/guarantees must never be "
    "transplanted onto the story's subject or used to fill gaps in how it "
    "works. A project's claims about itself (its own site/forum posts) are "
    "attributed statements ('according to the project'), not established facts "
    "— and for speculative or token-launch subjects, include the risk context "
    "a fair journalist would. Where a *product-specific* mechanism is "
    "undocumented — a marketplace's exact fee percentage, which royalty "
    "standard it enforces, its specific commission structure — say so plainly "
    '("fees are undisclosed") rather than inventing a number or naming a '
    "mechanism the Digest never verified; you may still explain Algorand's "
    "general layer-1 mechanics (consensus, finality, ASA tokenization AS A "
    "CONCEPT) when bridging why the story matters on-chain, but that license "
    "never extends to a specific project's specific business facts. "
    "A claim of tokenizing/fractionalizing ownership in a SPECIFIC, NAMED "
    "real-world physical asset (a named dam, building, mine, etc.) is an "
    "extraordinary claim — a small crypto project essentially never holds any "
    "actual legal claim to major infrastructure. If the Digest shows a tell "
    "that the 'ownership' language is fictional/gamified branding (the "
    "project's own marketing calling it a 'game', 'play2earn', or using "
    "'card' for what's framed as a real asset) or shows no independent "
    "verification of any real ownership relationship, do not write it up as "
    "though real asset-backed ownership occurred — say plainly that it is a "
    "token/game mechanic with no verified connection to the real institution.\n"
    "NUMERIC HONESTY: a small number is still a fact — report it as small (a "
    "$4,000 TVL or a handful of holders is negligible, not a headline metric). "
    "Never state two conflicting figures for the same metric on the same asset "
    "(e.g. a price aggregator vs. the actual DEX price) as if they agree — that's "
    "a sign they refer to different things; verify against the Research Digest "
    "and keep only the figure it actually supports, dropping the other."
)


_STAGE2_GENERATION_GUIDANCE = (
    "\n\nSTAGE 2 — WRITE FROM RESEARCH DIGEST ONLY:\n"
    "You have NO tools in this phase. Do not fetch URLs or run searches. Verified "
    "external facts (quotes, partnerships, numbers, dates) must come from the "
    "Research Digest and source material — never invent those. You MAY use your "
    "expert knowledge of Algorand layer-1 mechanics — GENERAL protocol behavior "
    "(consensus, finality time, typical fee ranges, tokenization standards, named "
    "ARC standards like ARC-18 royalty enforcement) — to explain why the story "
    "matters when sources are thin, and if you do, describe it accurately (e.g. "
    "ARC-18 royalty enforcement is a separate smart-contract application the "
    "asset's Clawback/Freeze/Manager point to — it is opt-in and can be "
    "bypassed, not an automatic built-in ASA field). You may NEVER use that same "
    "license to assert a SPECIFIC, unverified fact about the subject itself: a "
    "named project's exact fee schedule/percentages, or CLAIMING THIS PARTICULAR "
    "PROJECT implements a given mechanism (e.g. 'this marketplace enforces "
    "royalties via ARC-18') when the Digest never confirmed it — general protocol "
    "mechanisms are fair game to explain, but whether THIS subject actually uses "
    "one is a business fact, not protocol theory. If the Digest doesn't state it, "
    "say it's undisclosed/unverified, do not fill the gap with a plausible-"
    "sounding invention (root-caused 2026-07-15: a draft invented "
    "'0.001 ALGO per transfer' as a marketplace-specific fee — the network's "
    "real ~0.001 ALGO minimum fee is fine to cite generally, inventing it as a "
    "SPECIFIC marketplace's fee is not — and separately asserted, unverified, "
    "that named marketplaces specifically implement ASA-parameter-based royalty "
    "enforcement the Digest never confirmed for any of them).\n"
    "QUOTATION MARKS ARE A VERBATIM CLAIM: only put words inside quotation "
    "marks when they appear word-for-word in the Research Digest or source "
    "material. To convey the gist of what someone said, paraphrase WITHOUT "
    "quotation marks and attribute it plainly (root-caused 2026-07-16: a "
    "draft invented a phrase and attributed it to a named council in quotes "
    "— fabricating a quotation is worse than fabricating a number).\n"
    "SUPERSESSION CHECK: if the Research Digest mentions a newer successor, "
    "replacement, or migration path for the tool/framework/standard this "
    "piece centers on, say so plainly instead of writing the older one up as "
    "the current recommended way to do the thing — every fact can be accurate "
    "and the piece still misleads a reader deciding what to use today if the "
    "successor goes unmentioned (root-caused 2026-08-04: a PyTeal explainer "
    "omitted that Algorand Python/Puya has superseded it). You have no tools "
    "in this phase, so only act on a successor the Digest itself surfaced — "
    "do not go hunting for one from memory.\n"
)


_SPECIAL_EDITION_STAGE2_OVERRIDE = (
    "\n\nSPECIAL EDITION OVERRIDE: ignore the 'write a dense, brief article if "
    "material is brief' instruction above -- this compose already ran a "
    "quadrupled research pass specifically so the Digest would be rich. Do "
    "not default to compactness: develop every distinct finding in the "
    "Digest with full narrative treatment (context, mechanics, tradeoffs), "
    "the same depth the special-edition research instructions already asked "
    "for. Only write briefly if the Digest itself is genuinely thin DESPITE "
    "the deep research pass -- that means the topic doesn't support more, "
    "not that compactness is the goal. Root-caused 2026-08-04: a special "
    "edition recompose came out SHORTER than two prior ordinary-tier "
    "versions of the same article -- the universal 'brief when thin' "
    "framing below was winning out over the depth this edition was "
    "supposed to have, once the model reached the writing stage."
)


_OUTLINE_FOLLOW_INSTRUCTION = (
    "\n\nA Narrative Outline is included below (Throughline, planned Sections, "
    "and Contrasts & Tensions To Keep). Use it as your organizing structure — "
    "follow its section order and throughline, and make sure every contrast "
    "or tension it names survives into the prose. It is a plan, not a "
    "constraint on wording: adjust section boundaries if the material reads "
    "better differently, but don't silently drop something it flags."
)


def _build_stage2_user(
    *,
    user: str,
    digest: str,
    is_special_edition: bool = False,
    enumeration: str = "",
    outline: str = "",
) -> str:
    """Stage-2 user prompt: digest-only ground truth (no raw tool trace). ``enumeration``/``outline`` (special-edition only, see _run_special_edition_deepening) are appended as additional structured ground truth and an organizing plan, on top of the digest."""
    narrative_guidance = _NARRATIVE_GUIDANCE + (
        _SPECIAL_EDITION_STAGE2_OVERRIDE if is_special_edition else ""
    )
    extra_blocks = ""
    if enumeration.strip():
        extra_blocks += f"\n\n{enumeration}"
    if outline.strip():
        extra_blocks += f"\n\n{outline}" + _OUTLINE_FOLLOW_INSTRUCTION
    if digest.strip():
        return (
            user + "\n\n## Research Digest (PRIMARY AND ONLY ground truth for external facts):\n"
            f"{digest}"
            + extra_blocks
            + "\n\nWrite the article strictly from this material above. "
            "You cannot call tools or fetch additional pages.\n"
            + narrative_guidance
            + " Write it now."
        )
    return user + extra_blocks + narrative_guidance + " Write it now."


_RESEARCH_DIGEST_SYNTHESIS = (
    "Research phase complete. Synthesize everything you learned into a structured "
    "handoff. Output Markdown ONLY (no JSON), exactly these sections:\n\n"
    "## Research Digest\n\n"
    "### Verified Facts\n"
    "- One bullet per discrete verified fact. Each bullet MUST cite its source "
    "(URL, tool name, or on-chain lookup). No speculation.\n"
    "- The citation must point to a line that actually appears in the raw tool "
    "trace above. A specific fact (a name, price, date, transaction) that you "
    "recognize but that is NOT backed by a line in that trace does not belong "
    "here — recalling something from training is not the same as verifying it "
    "this session, no matter how plausible or confident it feels. Leave it out.\n"
    "- If two tool results give conflicting numbers for the same metric on the same "
    "asset (e.g. two different prices for what should be one token), do not list "
    "both as if they agree — flag the conflict explicitly, or keep only the more "
    "verifiable (on-chain/DEX) figure and drop the other.\n\n"
    "### Verbatim Quotes\n"
    "- Only word-for-word quotes visible in tool results, each with its source. "
    "If none exist, write exactly: None\n\n"
    "### Liveness Signals\n"
    "- Note anything that speaks to whether the subject is currently active: fetch "
    "attempts that errored/timed out (name which URLs), on-chain accounts showing "
    "'Offline' status or dust balances, how recent the newest verifiable content is, "
    "or confirmation that things ARE current/active if that's what you found. Do not "
    "omit this section just because nothing alarming turned up — say so explicitly "
    "('primary site loaded fine, no dormancy signals') so Stage 2 knows the check "
    "was actually done. Report ONLY dates/timestamps a tool result actually returned "
    "— if you found exactly one dated reference, report that ONE date and stop; do "
    "NOT invent a second, third, or 'most recent' date to make the section sound "
    "more thorough than the research actually was. 'Only one dated reference found "
    "(X)' is a complete, honest answer.\n\n"
    "### Numeric Conversions\n"
    "- An ASA's on-chain `total` is a RAW INTEGER, not a token count — the real "
    "supply is `total / 10^decimals`. State the CONVERTED figure (e.g. total=100000000000 "
    "with decimals=2 is 1,000,000,000 tokens, not 100,000,000,000) and show your "
    "division so Stage 2 doesn't have to re-derive it and doesn't repeat the raw "
    "integer as if it were the token count.\n\n"
    "### Chart\n"
    "- If a chart_data tool call in the trace above returned a `markdown_fence` "
    "field (a successful ```chart block), copy that fence VERBATIM into this "
    "section exactly as it appears in the trace — do not re-derive, reformat, "
    "or paraphrase the JSON; Stage 2 has no tools and cannot rebuild it. If "
    "chart_data was never called, or every call returned an error (no "
    "`markdown_fence` anywhere in the trace), write exactly: None\n\n"
    "### Unresolved Gaps\n"
    "- List up to 3 SPECIFIC, answerable questions that materially matter to "
    "this story and that a further tool call could plausibly resolve (e.g. "
    "'a real recent sale/transaction figure for this marketplace', 'the "
    "on-chain app ID for the registry contract', 'whether the project has "
    "posted anything in the last month'). Each gap must name the missing fact "
    "and what kind of tool call might find it — not a vague 'more detail' or "
    "'more sources'. This is your chance to ask for one more look before the "
    "writer has to work with what you found; do not save a gap you could "
    "have just gone and checked. If your research already covers the story "
    "adequately, write exactly: None\n\n"
    "Do not write the article."
)


def _synthesize_research_digest(
    *,
    trace: list[dict],
    research_context: str,
) -> str:
    """Stage 1→2 handoff: model-synthesized digest instead of raw tool JSON."""
    raw_trace = _format_research_digest(trace)
    if not raw_trace.strip():
        return ""
    try:
        from app.core.config import MISTRAL_TEMP_RESEARCH

        digest_client = get_mistral_digest_client()
        digest = digest_client.chat_completion(
            [
                {
                    "role": "user",
                    "content": (
                        f"{research_context}\n\n"
                        "Raw tool trace (reference — synthesize, do not dump verbatim):\n"
                        f"{raw_trace}\n\n{_RESEARCH_DIGEST_SYNTHESIS}"
                    ),
                },
            ],
            json_object=False,
            temperature=MISTRAL_TEMP_RESEARCH,
        )
        text = (digest or "").strip()
        return text if text else raw_trace
    except Exception:
        logger.warning("research digest synthesis failed; using raw trace", exc_info=True)
        return raw_trace


def _extract_unresolved_gaps(digest: str) -> str:
    """Pull the '### Unresolved Gaps' section out of a synthesized digest.

    Empty string when the section is absent or explicitly says None — the
    signal that tells the compose loop whether a bounded gap-fill research
    pass is worth running before handing off to the writer.
    """
    marker = "### Unresolved Gaps"
    idx = digest.find(marker)
    if idx == -1:
        return ""
    section = digest[idx + len(marker) :]
    next_heading = section.find("\n### ")
    if next_heading != -1:
        section = section[:next_heading]
    section = section.strip().strip("-").strip()
    if not section or section.lower().rstrip(".") == "none":
        return ""
    return section


def _gap_fill_nudge(gaps: str) -> str:
    """One bounded extra research pass targeting specific gaps the digest synthesis flagged as unresolved but material — giving the model a real chance to find the missing fact instead of the writer stage inventing (or recalling from training) something to fill it, as happened when a piece invented specific marketplace sales for a page with no real sales data."""
    return (
        "\n\nSTOP — before handing off to the writer, make one real attempt at "
        f"resolving these specific gaps:\n{gaps}\n\n"
        "Call whichever tools could plausibly answer them. If a tool call does "
        "not turn up the answer, that is a legitimate, acceptable outcome — do "
        "NOT guess or recall the answer from memory/training instead. Stop once "
        "you have made a genuine attempt at each gap; a gap that stays "
        "unresolved after an honest attempt should be reported as unresolved "
        "in the digest, not invented."
    )


def _urls_from_result(result: Any) -> list[str]:  # noqa: ANN401 -- arbitrary tool-result shape
    """Best-effort extraction of every URL a tool result carries — a single fetch exposes a top-level "url", while search-style tools nest hits under a list key (search_web's "results", search_bluesky's "posts", etc.)."""
    urls: list[str] = []
    if not isinstance(result, dict):
        return urls
    top = result.get("url")
    if isinstance(top, str) and top:
        urls.append(top)
    for key in ("results", "posts", "matches", "items", "links"):
        items = result.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    u = item.get("url")
                    if isinstance(u, str) and u:
                        urls.append(u)
    return urls


def _research_call_signals(entry: dict) -> set[str]:
    """The distinct source(s) one research call actually touched: the domain(s) in its result, else a domain-like argument, else the tool name. Keying on bare tool identity let the floor be satisfied by several trivial calls that all skim the same one or two domains; keying on domains instead rewards what the floor is meant to enforce — breadth of sources, not call count."""
    tool = entry.get("tool")
    if not tool or tool == "review_draft":
        return set()
    domains = {urlparse(u).netloc.lower() for u in _urls_from_result(entry.get("result")) if u}
    domains.discard("")
    if domains:
        return domains
    args = entry.get("arguments") if isinstance(entry.get("arguments"), dict) else {}
    for key in ("domain", "url", "repo", "protocol", "forum_url", "source"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return {f"{tool}:{value.strip().lower()}"}
    return {tool}


def _distinct_research_calls(trace: list[dict]) -> int:
    """Count distinct research sources touched so far (domains fetched, or a stable per-tool identity for calls with no URL), excluding review_draft self-checks."""
    signals: set[str] = set()
    for entry in trace:
        signals |= _research_call_signals(entry)
    return len(signals)


def _research_floor_nudge(have: int, need: int, digest: str) -> str:
    """A stronger directive to send the model back for a deeper research pass when it stopped too early (the Stage-1 research floor)."""
    return (
        f"\n\nSTOP — you only touched {have} distinct research source(s); this story "
        f"needs at least {need} before writing. You have so far gathered:\n{digest}\n\n"
        "Now dig deeper with DIFFERENT tools and arguments than above — e.g. "
        "search_bluesky for current community sentiment, search_web for recent "
        "integrations/partnerships/comparisons, or a market/on-chain tool for live "
        "metrics. Do NOT repeat calls you already made. Stop calling tools once you "
        "have genuinely gathered more — or, if the tools truly surface nothing new, "
        "stop and synthesis will produce a tight Research Digest handoff."
    )


def _debug_tool_turn(debug: dict | None, name: str, arguments: dict, result: dict) -> None:
    """Record a (synthetic) tool call + result into the debug transcript so the admin Sessions view shows it. Two-stage compose calls the grader directly rather than via the model's tool loop, so these turns aren't captured automatically the way the legacy single-loop's were."""
    import json as _json

    if debug is None or not isinstance(debug.get("messages"), list):
        return
    debug["messages"].append(
        {
            "role": "assistant",
            "tool_calls": [{"function": {"name": name, "arguments": _json.dumps(arguments)}}],
        }
    )
    debug["messages"].append({"role": "tool", "name": name, "content": _json.dumps(result)[:4000]})


def _record_grade(
    trace: list[dict],
    debug: dict | None,
    current: dict,
    review: dict,
    *,
    title: str,
    body: str,
    revise_count: int,
) -> None:
    """Record the grading result in the trace/debug transcript (like a review_draft tool call) and attach it to the current draft, so every return below carries this grade even when no (further) revision is attempted — the caller (publish gate) reads it via MistralArticleFields.heuristic_grade."""
    # The grader reads the FULL title+body; we record only a compact label in
    # the trace (title + word count) to avoid dumping the whole body into it.
    grade_args = {"title": title, "words": len(body.split())}
    if revise_count > 0:
        grade_args["recheck"] = True
    trace.append({"tool": "review_draft", "arguments": grade_args, "result": review})
    _debug_tool_turn(debug, "review_draft", grade_args, review)
    current["_heuristic_grade"] = review


def _draft_score(review: dict, *, needs_revision: bool) -> float:
    """The best-of-N comparison score for one graded pass: the heuristic grade, penalized when the LLM quality rubric also flags a revision."""
    grade_val = review.get("grade")
    score = float(grade_val) if isinstance(grade_val, int | float) else 0.0
    if needs_revision:
        score -= 2.0
    return score


def _grade_current_draft(
    title: str,
    summary: str,
    body: str,
    quality_mistral: MistralClient,
    *,
    is_special_edition: bool = False,
) -> dict:
    """Run the deterministic heuristic grader and the LLM quality rubric, merging the rubric result into the returned review dict under "quality". Either grader's failure degrades to an error marker rather than raising."""
    from app.modules.newspaper.article_grader import grade_article_draft
    from app.modules.newspaper.article_quality_llm import grade_article_quality_llm

    try:
        review = grade_article_draft(
            title=title, summary=summary, body=body, is_special_edition=is_special_edition
        )
    except Exception as exc:
        review = {"error": str(exc)[:200], "grade": None}
    try:
        quality = grade_article_quality_llm(title=title, body=body, client=quality_mistral)
    except Exception as exc:
        quality = {"model": "llm_rubric_error", "error": str(exc)[:200], "issues": []}
    review["quality"] = quality
    return review


def _link_gate_issues(body: str, trace: list[dict], link_check_cache: dict) -> list[str]:
    """Dead-link feedback: an untraced url that doesn't resolve forces a revision pass with the specific url named, so the writer can swap in a working alternative from its research instead of the final gate silently delinking it (which loses the citation entirely)."""
    from app.core.config import LINK_GATE_ENABLED

    if not LINK_GATE_ENABLED:
        return []
    try:
        from app.modules.newspaper.link_gate import dead_untraced_links

        return [
            f"dead link: {_dead_url} is unreachable and never appeared "
            "in your research — replace it with a working URL you "
            "actually researched, or drop the link and keep plain text"
            for _dead_url in dead_untraced_links(body, trace, checked=link_check_cache)
        ]
    except Exception:
        logger.warning("dead-link check failed during revision", exc_info=True)
        return []


def _chain_entity_gate_issues(
    body: str, trace: list[dict], gen_user: str, chain_check_cache: dict
) -> list[str]:
    """Chain-entity feedback: an invalid/untraced/nonexistent ASA id, address, or txid forces a revision pass naming the exact entity — same rationale as dead-link feedback (AlgoGlyph incident 2026-07-17), catching fabricated numbers the numeric gatekeeper can't see because the chain data was never wrong, only the model's arithmetic/attribution on top of it."""
    from app.core.config import CHAIN_ENTITY_GATE_ENABLED

    if not CHAIN_ENTITY_GATE_ENABLED:
        return []
    try:
        from app.modules.newspaper.chain_entity_gate import unverifiable_chain_entities

        return unverifiable_chain_entities(
            body, trace, extra_texts=[gen_user], checked=chain_check_cache
        )
    except Exception:
        logger.warning("chain-entity check failed during revision", exc_info=True)
        return []


def _authority_gate_issues(body: str) -> list[str]:
    """Unattributed-authority feedback: "industry research suggests" / "experts say" constructions force a revision naming the phrase, so the writer can cite its actual trace source or delete the claim — the post-hoc gate can only excise the sentence (2026-07-18: a fabricated "10-100x slower to verify" Falcon benchmark shipped wearing exactly this costume in a pre-release draft)."""
    from app.core.config import AUTHORITY_GATE_ENABLED

    if not AUTHORITY_GATE_ENABLED:
        return []
    try:
        from app.modules.newspaper.authority_gate import authority_revision_issues

        return authority_revision_issues(body)
    except Exception:
        logger.warning("authority-phrase check failed during revision", exc_info=True)
        return []


def _stale_deadline_gate_issues(body: str) -> list[str]:
    """Stale-deadline feedback: a real, accurately-sourced date framed as still open when it has already passed (Meld Gold 2026-08-04) is fed back so the writer rewrites the tense — a mechanical fix, not a factual dispute, so this stays a revision issue rather than a human hold like the defunct-entity gate."""
    from app.core.config import STALE_DEADLINE_GATE_ENABLED

    if not STALE_DEADLINE_GATE_ENABLED:
        return []
    try:
        from app.modules.newspaper.stale_deadline_gate import stale_deadline_issues

        return stale_deadline_issues(body)
    except Exception:
        logger.warning("stale-deadline check failed during revision", exc_info=True)
        return []


def _unsourced_specifics_gate_issues(
    body: str, trace: list[dict], user: str, research_user: str | None
) -> list[str]:
    """Unsourced-specifics feedback: a traction/funding count or named partner that isn't in the research is fed back so the writer removes or corrects it here — better than the post-hoc gate holding the whole draft for a human (GoPlausible 2026-07-20). The hold stays as the backstop for anything that survives revision.

    Grounds against [user, research_user] — the ORIGINAL prompt/source
    material — not gen_user alone. gen_user embeds the stage-1 digest, the
    researcher's own paraphrase of the source; checking a claim against the
    digest that produced it is circular and can rubber-stamp drift the digest
    already introduced (root-caused 2026-07-20: a "25% increase in
    engagement" source fact became "Monthly Active Users +25%" in the draft,
    grounded fine against the digest's own loose phrasing in-loop, then
    correctly flagged by the post-hoc gate — which already checks against
    user/research_user — after the revision loop had no more passes left to
    fix it). Must match the post-hoc call's extra_texts so the model sees the
    SAME verdict it'll be held on.
    """
    from app.core.config import UNSOURCED_SPECIFICS_GATE_ENABLED

    if not UNSOURCED_SPECIFICS_GATE_ENABLED:
        return []
    try:
        from app.modules.newspaper.unsourced_specifics_gate import (
            unsourced_specifics_revision_issues,
        )

        return unsourced_specifics_revision_issues(
            body, trace, extra_texts=[user, research_user or ""]
        )
    except Exception:
        logger.warning("unsourced-specifics check failed during revision", exc_info=True)
        return []


def _collect_fixable_issues(
    review: dict,
    quality: dict,
    *,
    needs_revision: bool,
    body: str,
    trace: list[dict],
    gen_user: str,
    user: str,
    research_user: str | None,
    link_check_cache: dict,
    chain_check_cache: dict,
) -> list[str]:
    """Gather every revision-worthy issue across the schema check, quality rubric, and the four deterministic gates, stashing each gate's own findings onto `review` for the trace/telemetry record."""
    issues = list(review.get("issues") or [])
    # "headline" issues are the structural enforcement of the house headline
    # style: the prompt states the rules, but only this deterministic check +
    # forced revision makes them invariants (prompts drift; regexes don't).
    schema_fixable = [
        i for i in issues if i.startswith(("too long", "structure", "schema", "headline"))
    ]
    quality_fixable: list[str] = list(quality.get("issues") or []) if needs_revision else []

    link_fixable = _link_gate_issues(body, trace, link_check_cache)
    if link_fixable:
        review["dead_links"] = link_fixable
    chain_fixable = _chain_entity_gate_issues(body, trace, gen_user, chain_check_cache)
    if chain_fixable:
        review["chain_entities"] = chain_fixable
    authority_fixable = _authority_gate_issues(body)
    if authority_fixable:
        review["unattributed_authority"] = authority_fixable
    unsourced_fixable = _unsourced_specifics_gate_issues(body, trace, user, research_user)
    if unsourced_fixable:
        review["unsourced_specifics"] = unsourced_fixable
    stale_deadline_fixable = _stale_deadline_gate_issues(body)
    if stale_deadline_fixable:
        review["stale_deadlines"] = stale_deadline_fixable

    return (
        schema_fixable
        + quality_fixable
        + link_fixable
        + chain_fixable
        + authority_fixable
        + unsourced_fixable
        + stale_deadline_fixable
    )


# Root-caused 2026-08-04 (Humanitarian Network recompose #2): the needs_depth
# branch below used to have no length floor at all -- it said "PRESERVE every
# verified fact" but also told the model to "CUT the later restatements," and
# a real revision pass used that license to rewrite a 2,471-word first draft
# (the entity-enumeration/outline pipeline's actual output) down to 1,044
# words, then 1,020, discarding the depth the whole special-edition pipeline
# exists to produce. The `else` (reorganize-only) branch's "stay above 80%
# or it will be rejected" was also a dead threat by this point -- the actual
# enforcement guard in _attempt_revision was removed 2026-08-03 as
# miscalibrated for the old Medium-tier writer, so nothing ever checked it.
#
# Owner directive: remove length limitation/targeting from revision entirely,
# for every article, not just special editions -- no numeric floor or target
# word count anywhere, just "don't shrink it as a side effect of fixing
# something else." Only `too_long` (the one case where shrinking IS the
# correct fix) still asks for trimming.
_REVISION_NO_SHRINK_RULE = (
    "There is NO length limit and NO target word count for this revision. Do "
    "not shorten, condense, tighten, or produce a leaner synthesis of the "
    "piece as a side effect of fixing the issues below. PRESERVE every "
    "verified fact and every section from the draft."
)


def _revision_length_rule(*, too_long: bool, needs_depth: bool) -> str:
    """The scope instruction for a revision prompt: what to fix, and (for everything except too_long) an explicit "don't shrink it" rule instead of a numeric word-count target."""
    if too_long:
        return "Trim padding/filler to bring it under the limit, but keep every real fact."
    if needs_depth:
        return (
            "Improve narrative synthesis, Algorand technical depth, and critical "
            "distance — use verified facts from the Research Digest and, where "
            "sources are thin, your expert knowledge of Algorand layer-1 mechanics "
            "to explain legacy friction vs protocol solutions. Move multi-item data "
            "into a Markdown table (Concept / Real-World Implication). If the subject "
            "has a conflict of interest (a centralized exchange's product, a reward "
            "structure that incentivizes holding the SAME platform's token, an "
            "unaudited protocol), name the actual risk/tradeoff a reader needs "
            "instead of just relaying the subject's own marketing framing. Apply "
            "the NO REPETITION rule from your instructions here too — a VERBATIM "
            "repeat across sections may be cut to its first mention — but that is "
            "not license to rewrite whole passages into a shorter synthesis just "
            "because a point recurs. Do NOT invent quotes, partnerships, or "
            "numbers. " + _REVISION_NO_SHRINK_RULE
        )
    return (
        "PRESERVE every fact — only REORGANIZE the existing prose into section "
        "headings and short paragraphs; move comparative data into a Markdown "
        "table if needed. Do NOT use narrative bullet lists. Do NOT drop "
        "information or summarize away detail. " + _REVISION_NO_SHRINK_RULE
    )


def _build_revision_prompt(
    gen_user: str,
    fixable: list[str],
    already_fixed: list[str],
    *,
    too_long: bool,
    needs_depth: bool,
) -> str:
    issues_block = "\n".join(f"- {i}" for i in fixable[:10])
    carried_block = ""
    if already_fixed:
        carried_block = (
            "\n\nThese issues were already fixed in an earlier revision pass — "
            "do NOT reintroduce them while addressing the list above:\n"
            + "\n".join(f"- {i}" for i in already_fixed[:10])
        )
    length_rule = _revision_length_rule(too_long=too_long, needs_depth=needs_depth)
    return (
        gen_user + f"\n\nA reviewer flagged these problems:\n{issues_block}\n\n"
        f"{length_rule} Do NOT add, invent, or restate facts beyond the research "
        "digest above, and do not pad with new filler. Return the full revised "
        f"article as the same JSON object.{carried_block}"
    )


def _attempt_revision(
    mistral: MistralClient,
    gen_system: str,
    revise_user: str,
    *,
    temperature: float,
    note_failure: Callable[[str], None],
) -> dict | None:
    """Call the reviser. Returns the revised fields, or None — having already called note_failure — if the call failed or came back empty.

    Used to also reject any revision that dropped more than ~25% of the word
    count (unless the draft was flagged too-long) on the theory that a
    structure-only fix must not gut the article. Removed 2026-08-03: that
    guard was sized for Mistral Medium, which wrote too-short drafts by
    default and needed protecting from further shrinkage. Mistral Large
    (the writer tier since) reliably writes enough — the guard's only
    observed effect by then was rejecting a LEGITIMATE fix (a revision that
    correctly merged a grader-flagged repeated section, e.g. HAY tokenomics
    restated in both prose and a table, cut real duplication and tripped the
    same ratio check as a lazy gut job would have) and silently reverting to
    the still-repetitive draft — defeating the revision pass it was
    supposed to be protecting.
    """
    try:
        revised = mistral.chat_json_object(
            [
                {"role": "system", "content": gen_system},
                {"role": "user", "content": revise_user},
            ],
            temperature=temperature,
        )
    except Exception as exc:
        note_failure(f"revision call failed: {type(exc).__name__}: {exc}")
        return None
    if not str(revised.get("body", "") or "").strip():
        note_failure("revision returned an empty body")
        return None
    return revised


def _review_and_revise(
    mistral: MistralClient,
    payload: dict,
    *,
    system: str,
    gen_user: str,
    trace: list[dict],
    debug: dict | None = None,
    user: str = "",
    research_user: str | None = None,
    is_special_edition: bool = False,
) -> dict:
    """Stage 3+4 of two-stage compose: grade the draft, then revise if weak.

    The warm generation pass runs with NO tools, so the model cannot call
    review_draft itself — we run the heuristic grader deterministically here and,
    on a sub-threshold grade or any listed issues, revise with the concrete
    issues fed back, up to WRITER_REVISION_MAX_PASSES times (a pass that comes
    back clean stops the loop early — most drafts never need a second). Every
    grading is recorded in the trace like review_draft tool calls so
    telemetry/insights see them.
    """
    from app.core.config import (
        MISTRAL_TEMP_WRITE,
        WRITER_QUALITY_LLM_MIN_SCORE,
        WRITER_REVIEW_ENABLED,
        WRITER_REVISION_MAX_PASSES,
    )
    from app.modules.newspaper.article_quality_llm import quality_needs_revision

    if not WRITER_REVIEW_ENABLED:
        return payload

    # LLM rubric grading (narrative synthesis/technical depth/critical
    # distance) is a judgment task, not generation — it doesn't need the
    # writer's Large-tier model. Was previously (silently) run on `mistral`
    # itself, the SAME Large client used for Stage 2 generation, despite
    # grade_article_quality_llm's own docstring calling itself a "Fast
    # Small-tier rubric" — that intent only ever applied to its unused
    # default. Use the research-tier client explicitly (cheaper; and as of
    # 2026-07-15, mistral-small-latest gets reasoning_effort="high" for free
    # since MistralClient now knows it actually supports reasoning).
    quality_mistral = get_mistral_research_client()

    def _note_revision_failure(reason: str) -> None:
        # Surface WHY the revision didn't happen instead of silently keeping the
        # weak draft — otherwise a rate-limited/failed revision is invisible and
        # looks like "the grade changed nothing".
        result = {"error": reason[:300]}
        args = {"revision": "failed"}
        trace.append({"tool": "review_draft", "arguments": args, "result": result})
        _debug_tool_turn(debug, "review_draft", args, result)

    current = payload
    max_revisions = max(1, WRITER_REVISION_MAX_PASSES)
    revise_count = 0
    # Best-of-N: a revision pass can trade one fixed issue for a regression on
    # something an EARLIER pass already fixed (observed 2026-07-14 on a CompX
    # recompose — a pass that fixed a headline colon-label re-broke structure
    # a prior pass had already cleaned up, and being the LAST pass, the
    # regressed draft is what got kept). Track the best-scoring pass seen and
    # return that instead of just whatever the loop happened to end on.
    best_current = payload
    best_score = float("-inf")
    # Carry-forward memory: each revision prompt below only sees the CURRENT
    # pass's flagged issues, with no record of what earlier passes already
    # fixed — the model has no way to know a rewrite it's about to do would
    # undo a fix from two passes ago. Accumulate every issue ever raised so a
    # later prompt can be told which ones must NOT come back.
    ever_raised: set[str] = set()
    # Shared live-check cache: link urls rarely change between passes, so one
    # network check per url across the whole revision loop (owner request
    # 2026-07-16: tell the writer a link is dead so it can find an alternative
    # — the post-hoc gate can only delink, the writer can substitute).
    link_check_cache: dict[str, bool] = {}
    # Same rationale, for on-chain entity lookups (asset/address/txid exist
    # checks are network calls too, and rarely change between passes).
    chain_check_cache: dict[tuple[str, str], str] = {}
    while True:
        title = str(current.get("title", "") or "")
        body = str(current.get("body", "") or "")
        summary = str(current.get("summary", "") or "")
        if not body:
            return current

        review = _grade_current_draft(
            title, summary, body, quality_mistral, is_special_edition=is_special_edition
        )
        quality = review["quality"]
        _record_grade(
            trace, debug, current, review, title=title, body=body, revise_count=revise_count
        )

        needs_revision = quality_needs_revision(quality, min_score=WRITER_QUALITY_LLM_MIN_SCORE)
        fixable = _collect_fixable_issues(
            review,
            quality,
            needs_revision=needs_revision,
            body=body,
            trace=trace,
            gen_user=gen_user,
            user=user,
            research_user=research_user,
            link_check_cache=link_check_cache,
            chain_check_cache=chain_check_cache,
        )

        score = _draft_score(review, needs_revision=needs_revision)
        # Ties favor the LATER pass: a revision that plateaus on the same
        # numeric grade (e.g. a stuck quality-rubric score) has still likely
        # addressed whatever was flagged, so prefer the freshest draft over
        # reverting all the way back to the first pass. Only a strictly WORSE
        # score is treated as a regression and passed over.
        if score >= best_score:
            best_score = score
            best_current = current

        if not fixable:
            return best_current
        if revise_count >= max_revisions:
            # Out of revisions — return the BEST pass seen, not necessarily
            # this last one (see best-of-N note above).
            return best_current

        already_fixed = sorted(ever_raised - set(fixable))
        ever_raised.update(fixable)
        too_long = any(i.startswith("too long") for i in fixable)
        needs_depth = needs_revision
        revise_user = _build_revision_prompt(
            gen_user,
            fixable,
            already_fixed,
            too_long=too_long,
            needs_depth=needs_depth,
        )
        gen_system = system + _STAGE2_GENERATION_GUIDANCE

        revised = _attempt_revision(
            mistral,
            gen_system,
            revise_user,
            temperature=MISTRAL_TEMP_WRITE,
            note_failure=_note_revision_failure,
        )
        if revised is None:
            return best_current

        # Default to this pass's grade if the next pass's regrade itself fails,
        # so the floor-gate downstream never sees revision as erasing a known
        # grade. The loop's next iteration overwrites this with the real regrade.
        revised["_heuristic_grade"] = review
        current = revised
        revise_count += 1


# Stable output contract shared by every compose function. Kept in the SYSTEM
# message so it is identical across requests (cacheable prefix) and never mixed
# with the per-request payload that lives in the USER message.
_JSON_ONLY = "Output STRICTLY a single valid JSON object — no text outside the JSON envelope."

# Generic placeholder so the model treats this as a FORMAT example, not a cue to
# always draw an ALGO price chart.
_CHART_EXAMPLE = (
    '{"type": "bar", "title": "<what this chart shows>", '
    '"x": ["<label A>", "<label B>"], '
    '"series": [{"name": "<series name>", "y": [12, 30]}]}'
)

# A real published article that scored well on the deterministic grader, shown
# as a worked example instead of yet another prohibition — a good few-shot
# example reliably buys more format/tone compliance from a small model than
# another negative rule, and lets some narrower rules stay implicit. The TOPIC
# and FACTS are not to be reused, only the shape: problem->solution framing,
# scannable headers, a table for dense data, first-mention links, and an
# honest closing Source list. Trimmed of the citation block reference_block.py
# appends automatically after generation (that's not the model's own output,
# so including it here would teach a duplicate Sources section).
_GOOD_EXAMPLE_ARTICLE = {
    # NOTE: the example title must model the claim-style headline rule — a
    # few-shot example teaches format harder than any prohibition, which is
    # exactly how an earlier colon-label example title ("Nodely: The Global
    # Backbone…") made every published headline a colon label.
    "title": "Nodely’s free tier now carries 115M Algorand API calls a day",
    "summary": (
        "Nodely provides free, low-latency node and indexer infrastructure for "
        "Algorand and AVM chains, serving 115M+ daily requests across 20+ "
        "locations with no vendor lock-in."
    ),
    "tags": ["infrastructure", "api", "node", "indexer", "algorand"],
    "body": (
        "## The Reliability Gap in Blockchain Development\n\n"
        "For decentralized applications, uninterrupted access to chain data is "
        "non-negotiable. Developers building on Algorand face a choice: run "
        "their own nodes—a resource-intensive operation requiring "
        "specialized expertise—or rely on third-party providers that may "
        "introduce latency spikes, downtime, or restrictive terms. The "
        "friction is real: without robust infrastructure, even well-designed "
        "applications can fail at the point of user interaction.\n\n"
        "## A Globally Distributed Solution\n\n"
        "[Nodely](https://nodely.io/) resolves this by operating a globally "
        "distributed network of nodes and indexers. Its infrastructure spans "
        "20+ geographic locations, currently handling 115M+ daily API "
        "requests through 75+ indexers. The platform supports 75+ customers "
        "across 5+ AVM-compatible chains, including Algorand’s mainnet "
        "and testnet, with full archival access to historical data.\n\n"
        "Beyond core node services, Nodely provides additional utilities: an "
        "IPFS Gateway for Algorand ASA CIDs, a BigQuery dataset for "
        "analytics, and public node telemetry dashboards. 24/7 support is "
        "available via Telegram or Discord, with free support extended to "
        "non-commercial projects.\n\n"
        "## Tiered Access for Every Stage\n\n"
        "Nodely’s pricing model scales with project needs, eliminating "
        "surprises with fixed costs:\n\n"
        "| Tier | Price | Throughput | SLO | Notable Features |\n"
        "|------|-------|------------|-----|------------------|\n"
        "| Free | 0 ALGO/forever | 60 req/s per browser | 99.95% | IPFS "
        "Gateway, no keys needed |\n"
        "| Unlimited | $256/month | 6000 req/s per key/site, 500 req/s per "
        "IP | 99.99% | Full API at 25 locations |\n"
        "| Business | $256/month* | 500 req/s per IP, 200K reqs daily | "
        "100% | Fee abstraction, validator node service, 1M TX included |\n"
        "| Enterprise | $1024/month/region | Custom | Custom | Dedicated "
        "infrastructure |\n\n"
        "*Annual subscription only\n\n"
        "The free tier is explicitly production-ready, requiring only that "
        "projects attribute Nodely on their site. For teams ready to "
        "self-host, Nodely offers setup tuning services, applying its "
        "experience in running APIs at scale to optimize custom "
        "configurations.\n\n"
        "## Technical Foundation and Philosophy\n\n"
        "Nodely’s infrastructure is built on vanilla open-source node "
        "and indexer APIs, ensuring no vendor lock-in. Projects can "
        "seamlessly migrate to self-hosted setups when ready. The "
        "team’s background in telecom infrastructure since 2006 "
        "enables bare-metal configurations that reduce latency by orders of "
        "magnitude compared to cloud-based alternatives. Streaming add-ons "
        "leverage projects open-sourced on Nodely’s "
        "[GitHub](https://github.com/algonode), maintaining transparency "
        "and community collaboration.\n\n"
        "## Ecosystem Context\n\n"
        "As of June 29, 2026, Algorand’s mainnet maintains 2,740 "
        "full-time nodes. Nodely’s [status "
        "page](https://algonode.betteruptime.com/) confirms all services "
        "are operational, with the last update at 10:14am UTC. This "
        "reliability is critical as the network continues to grow, "
        "ensuring developers can focus on building rather than maintaining "
        "infrastructure.\n\n"
        "## Team and Evolution\n\n"
        "Originally launched as AlgoNode, Nodely is developed by a team of "
        "site reliability engineers with extensive experience in critical "
        "infrastructure. The [AlgoNode GitHub "
        "organization](https://github.com/algonode) (verified domain: "
        "nodely.io) maintains 15+ open-source repositories supporting the "
        "ecosystem.\n\n"
        "## Source\n"
        "- [Nodely](https://nodely.io/)\n"
        "- [AlgoNode GitHub](https://github.com/algonode)\n"
        "- [Nodely Status](https://algonode.betteruptime.com/)"
    ),
}
_GOOD_EXAMPLE = (
    "\n\nWORKED EXAMPLE (a real published article that scored well — study "
    "its structure, technical translation, data presentation and sourcing; "
    "never reuse its topic, facts or specific phrasing for an unrelated "
    "story):\n"
    f"{json.dumps(_GOOD_EXAMPLE_ARTICLE)}\n"
)

# Article schema + writing/format rules for scraped-source articles. These are
# stable across every call, so they belong in the SYSTEM message rather than
# being interleaved with the source material in the USER message.
_ARTICLE_FORMAT_RULES = (
    "\nFORMAT RULES:\n"
    "Write the article as a single JSON object adhering exactly to this schema:\n"
    '{"title": "string", "summary": "string", "body": "string", "tags": ["string"]}\n\n'
    "- title: a HEADLINE, not a label. State this story's single most newsworthy "
    "concrete development in active voice — someone shipped, launched, hit, broke or "
    "changed something — and prefer a specific verified number or stake from the "
    "story. EXCEPTION: if the most eye-catching number is small or unflattering (a "
    "tiny TVL, a handful of users/holders, a fractional token price), do NOT make "
    "the headline about how small it is — lead with what actually happened (the "
    "launch, feature, or development) instead, and save the honest number for the "
    "body. The headline's SCOPE must match the evidence's scope, for ANY service or "
    "topic: don't extrapolate a narrow, specific change (a few pages, one feature, "
    "one release, one team) into language implying a broader shift for the whole "
    "company/product/site ('X replaces its website', 'X overhauls Y', 'X pivots to "
    "Z') — name the specific thing that changed, not the broadest plausible reading "
    "of it (one instance of this failure, 2026-08-02: 'Algorand Replaces Legacy Pages "
    "with AlgoKit Developer Hub' turned a real, sourced swap of exactly four pages "
    "into a site-wide-pivot headline; 'Algorand.co Drops Four Legacy Pages, Adds "
    "AlgoKit Developer Hub' states the same facts at their true scope). Max 90 chars. "
    "BANNED: the '<Name>: <description>' colon-label template (any headline shaped like 'X: "
    "what X is'), vague marketing verbs (unveils, empowers, elevates, revolutionizes), "
    "and evergreen headlines that would have been equally true a month ago — a good "
    "headline is only true because of what happened in THIS story\n"
    "- summary: A concise deck for feed cards; STRICT MAXIMUM of 280 characters; "
    "describe the story, not the pipeline\n"
    "- body: The full article in Markdown, length scaled strictly to verified substance "
    "— never pad a thin story to hit a length target.\n"
    "  - SCANNABILITY: Break up the text with descriptive Markdown headers (## and ###). "
    "Do NOT write more than three consecutive paragraphs without introducing a new "
    "section header. (Very short updates of only a paragraph or two are exempt.)\n"
    "  - NARRATIVE PROSE: Events, features, partnerships, and updates MUST be woven "
    "into flowing paragraphs. DO NOT use bulleted or numbered lists for narrative "
    "storytelling anywhere in the body (a ## Source link list at the end is the only "
    "exception).\n"
    "  - DATA PRESENTATION: You are strictly prohibited from using bulleted lists. "
    "When presenting multi-item data (e.g., curriculum pillars, comparative metrics, "
    "feature sets), you MUST isolate it into a Markdown table with explicit columns "
    "explaining the 'Concept' and the 'Real-World Implication'. Do not bury "
    "multi-item concepts in standard paragraph prose or comma-separated sentences.\n"
    "  - JSON SAFETY: The body is embedded in a JSON string. Use single quotes "
    "('like this') for direct quotations in the markdown body, or escape double "
    'quotes as \\" — never emit unescaped double quotes inside the body string.\n'
    "  - IGNORE navigation menus, cookie/consent banners, footers, share/subscribe/login "
    "prompts and other page boilerplate in the source — extract the actual story. If the "
    "source carries no real news, write a brief honest note rather than padding\n"
    "  - a chart is OPTIONAL: include ONE only if this article itself has a trend or "
    "comparison worth visualizing, and chart that subject's own data — never ALGO "
    "price/market metrics by default (see the ALGO PRICE/MARKET RULE). Call the "
    "`chart_data` tool to build the fence — it returns `markdown_fence` ready to "
    "paste into the body (do not hand-author chart JSON). Fenced format:\n"
    "    ```chart\n"
    f"    {_CHART_EXAMPLE}\n"
    "    ```\n"
    '    Use "line" for trends over time, "bar" for category comparisons. Only chart '
    "REAL data you fetched via tools (chart_data validates custom series; "
    "algo_price fetches live ALGO history); never invent numbers.\n"
    "  - cite sources clearly within the text where relevant, and end with ## Source "
    "linking the URL\n"
    "  - when you first mention a notable project, protocol, company or person, link its "
    "name to its canonical site with an inline Markdown link [Name](https://...); link "
    "only the FIRST mention and only URLs you are confident about (prefer ones a tool "
    "returned) — do not over-link or invent URLs\n"
    "- tags: 2–5 lowercase topical tags drawn from the content (e.g. defi, governance, "
    "tokenization, wallet, nft, sdk, partnership) — specific, not generic\n"
    "- On-chain context (the round and tx given in the payload) is background only — "
    "do not list it in the body\n"
    f"{_GOOD_EXAMPLE}"
)


def _today_utc() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def _recency_rule(today: str) -> str:
    """Temporal-awareness rule: scraped pages routinely carry stale figures and the model otherwise has no idea what 'today' is, so it restates old numbers as current (e.g. an article written in 2026 quoting 2022 TVL)."""
    return (
        f"- Recency: today is {today} (UTC). Source pages often contain outdated "
        "figures. Never present a number, price, ranking, TVL/volume or a 'latest/"
        "current/now' claim as present-day unless the source clearly dates it to "
        "recently. If a figure looks old or undated, attribute it in-text (e.g. "
        "'as of 2022') or omit it rather than implying it is current. For anything "
        "that should reflect the present (ALGO price, market data, chain stats), "
        "prefer the live tools over numbers found on the page.\n"
        "- Temporal anchoring: if source material is several months old and your "
        "research finds no breaking update for the current month, DO NOT write as if "
        "the announcement just happened — no 'planned for' the current year, no "
        "'coming soon' for a window that has already started. Frame the piece as a "
        "Status Update or Ecosystem Overview and anchor it at the current point in the "
        "year rather than the announcement date, in your own words — do not pad this "
        "with an unrelated live metric just to look current (see the ALGO PRICE/MARKET "
        "RULE for when a fetched metric actually belongs in a story).\n"
    )


def is_static_landing_page(url: str) -> bool:
    """A root domain / shallow marketing page (e.g. https://tinyman.org) is a static profile, not a dated news item. Such pages have no reliable timeline (a persistent 'v2 is live!' banner sits next to an undated roadmap), so the writer must produce an evergreen profile, never breaking news — this is the deterministic fix for chronological context collapse."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if not parsed.netloc:  # not a real absolute URL -> don't assume static
        return False
    return parsed.path.strip("/").lower() in ("", "about", "home", "index.html")


# Evergreen-profile guidance injected for static landing pages: describe WHAT the
# project is in timeless present tense, never WHEN things happened.
_PROFILE_GUIDANCE = (
    "\n\nSTATIC PROFILE MODE: the source is a project's landing page, which has NO "
    "reliable timeline. Write an EVERGREEN PROFILE of what the project is and does, "
    "in timeless present tense — NOT breaking news. NEVER present features, versions, "
    "launches or roadmaps as 'new', 'now live', 'just launched', 'recently', "
    "'upcoming', or tied to any year/quarter unless the source explicitly and "
    "unambiguously dates it. State capabilities as standing facts (e.g. 'X is a "
    "non-custodial wallet that...'), and OMIT undated roadmaps/quarterly milestones "
    "entirely rather than implying a date."
)

# The source material for a web service is a MULTI-PAGE AGGREGATE of that
# service's web presence (a "# SERVICE WATCH:" header, then "## PAGE:" sections
# across the service's domains). The composer must read it as one entity's
# state, not a single article.
_SERVICE_WATCH_NOTE = (
    "\n\nSOURCE SHAPE: the material is an AGGREGATE of the service's web presence — "
    "a '# SERVICE WATCH:' header followed by '## PAGE: <url>' sections spanning the "
    "service's site(s). Treat it as the current state of ONE product/organisation, "
    "synthesising across the sections; never write 'according to page 3' or quote the "
    "'## PAGE:' scaffolding itself."
)

# Evolution-story mode: a content update fires because the service's aggregate
# CHANGED week-over-week. The diff is the news; the aggregate is background.
_FIRST_COVERAGE_GUIDANCE = (
    "\n\nFIRST COVERAGE MODE: we have NEVER published anything about this service "
    "before — readers do not know it exists. Do NOT write a 'what changed' update: "
    "an update on a service nobody was introduced to is meaningless, and a cosmetic "
    "change is not a story at all. Write an INTRODUCTION/PROFILE of the service — "
    "what it is, what problem it solves, who is behind it, how it fits the Algorand "
    "ecosystem — in timeless present tense, using your research tools to verify. "
    "Introduce it at the depth the sources actually document: if its docs are "
    "missing or stubs, say so instead of reconstructing how it must work. "
    "The recent page change may be mentioned as a closing note at most; if the "
    "change itself is the only material and the service is not worth introducing, "
    "keep the piece short and factual rather than inflating it."
)

_EVOLUTION_GUIDANCE = (
    "\n\nEVOLUTION STORY MODE: this is an UPDATE on a service we already track — it "
    "was triggered because its web presence CHANGED since we last looked. The unified "
    "diff below is the STORY: it shows what the team shipped, rebranded, migrated, "
    "priced, or announced. Lead with that change and why it matters to users; use the "
    "rest of the aggregate only as background to explain the change. Do NOT re-introduce "
    "the project from scratch or re-report unchanged, previously-covered material — the "
    "reader already knows what this service is. If the diff is purely cosmetic (copy "
    "tweaks, reordering, boilerplate) with no substantive product change, say so plainly "
    "and keep it brief rather than inflating it into news."
)


def _clip(text: str, limit: int = MISTRAL_MAX_SOURCE_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _source_links_block(source_links: list[dict[str, str]] | None, *, limit: int = 25) -> str:
    """The source page's own outbound links (the research trail), rendered for the composer.

    Links are stripped from `page_text` (they'd pollute the relevance/novelty
    signals), so this is how the writer learns what to `fetch_url` next.
    """
    if not source_links:
        return ""
    lines = []
    for link in source_links[:limit]:
        url = (link.get("url") or "").strip()
        if not url:
            continue
        text = (link.get("text") or "").strip()[:120]
        lines.append(f"- {text} — {url}" if text else f"- {url}")
    if not lines:
        return ""
    body = "\n".join(lines)
    return (
        "\n\nLinks found on the source page (its own references — call `fetch_url` "
        "on the ones most relevant to this story to read the primary source before "
        f"writing; do not list them verbatim):\n{body}\n"
    )


def _price_metrics_block(asset_id: str) -> str:
    block = load_mistral_context(asset_id)
    if not block:
        return ""
    return f"\n\nPrepared price metrics (stored polls + chart reference):\n{_clip(block, 3500)}\n"


def _coerce_markdown(value: Any) -> str:  # noqa: ANN401 -- model-emitted body can be str, dict, or list
    """Flatten a body the model emitted as nested JSON back into markdown.

    In json_object mode the model sometimes returns ``body`` as an object keyed
    by section heading (``{"## Market snapshot": "...table...", ...}``) or a list
    of blocks instead of a single markdown string. ``str(dict)`` would store the
    Python repr verbatim (the "broken JSON structure" on the page), so reconstruct
    real markdown: each key becomes a heading, each value its content.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts: list[str] = []
        for k, v in value.items():
            key = str(k).strip()
            content = _coerce_markdown(v)
            if key and not key.startswith("#"):
                key = f"## {key}"
            parts.append(f"{key}\n{content}".strip() if key else content)
        return "\n\n".join(p for p in parts if p).strip()
    if isinstance(value, list):
        return "\n\n".join(_coerce_markdown(v) for v in value if v is not None).strip()
    return str(value).strip()


def _parse_article_fields(payload: dict[str, Any]) -> MistralArticleFields:
    title = str(payload.get("title") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    body = _coerce_markdown(payload.get("body")).strip()
    if not title or not summary or not body:
        raise MistralError("Mistral JSON missing title, summary, or body")
    raw_tags = payload.get("tags") or []
    tags: list[str] = []
    if isinstance(raw_tags, list):
        for t in raw_tags:
            slug = str(t).strip().lower().replace(" ", "-")
            slug = "".join(ch for ch in slug if ch.isalnum() or ch == "-").strip("-")
            if slug and slug not in tags:
                tags.append(slug)
    return MistralArticleFields(
        title=title,
        summary=summary,
        body=body,
        tags=tuple(tags[:6]),
        heuristic_grade=payload.get("_heuristic_grade"),
        breaking_reason=payload.get("_breaking_reason"),
        confirmed_alert=payload.get("_confirmed_alert"),
        defunct_domains=tuple(payload.get("_defunct_domains") or ()),
        unsourced_hold_reason=str(payload.get("_unsourced_hold_reason") or ""),
    )


def compose_scrape_article_mistral(
    *,
    service_name: str,
    source_url: str,
    page_title: str,
    page_text: str,
    txid: str,
    round_num: int,
    diff: str | None,
    is_first_snapshot: bool,
    enrichment_block: str = "",
    source_links: list[dict[str, str]] | None = None,
    publish_topic: str = "",
    first_coverage: bool = False,
    prior_coverage_block: str = "",
    client: MistralClient | None = None,
) -> MistralArticleFields:
    """Generate newspaper article fields from scrape context via Mistral.

    ``first_coverage``: the service has never had a published article (e.g. its
    one-shot discovery row expired unpublished), so a diff-driven update would
    reference a service readers have never met — write an introduction/profile
    instead, with the recent change as a secondary note.

    ``prior_coverage_block`` (2026-08-02, NFDomains): names this service's own
    most recent article, when one exists, so the writer has the one fact it
    needs to either write a genuine update or call
    abort_article(duplicate_coverage) instead of silently re-writing the same
    introduction with a fresh headline number. Empty when there's no prior
    article (first_coverage) or the lookup failed -- never blocks a compose.
    """
    mistral = client or get_mistral_client()
    today = _today_utc()
    source_domain = (urlparse(source_url).netloc or "").lower()
    links_block = _source_links_block(source_links)
    from app.core.config import DIFF_PROMPT_MAX_CHARS

    diff_block = ""
    if diff:
        diff_block = f"\n\nText diff (unified):\n```\n{_clip(diff, DIFF_PROMPT_MAX_CHARS)}\n```"
    elif not is_first_snapshot:
        diff_block = "\n\n(Content hash changed but no textual diff was produced.)"
    prior_block = f"\n\n{prior_coverage_block}" if prior_coverage_block else ""

    system = _writer_system_prompt(today)
    # Source-type router: a static landing page (root domain) becomes an evergreen
    # profile, not breaking news — prevents chronological context collapse upstream.
    from app.core.config import SOURCE_TYPE_ROUTER_ENABLED

    is_aggregate = page_text.lstrip().startswith("# SERVICE WATCH:")
    if is_aggregate:
        system = system + _SERVICE_WATCH_NOTE
    # An update WITH a diff is an evolution story — the change leads. A first
    # snapshot (or a static landing page) stays intro/profile-shaped instead.
    # first_coverage overrides evolution: never lead with "what changed" on a
    # service the readership has never been introduced to.
    is_evolution = bool(diff) and not is_first_snapshot and not first_coverage
    if first_coverage:
        system = system + _FIRST_COVERAGE_GUIDANCE
    elif is_evolution:
        system = system + _EVOLUTION_GUIDANCE
    elif SOURCE_TYPE_ROUTER_ENABLED and is_static_landing_page(source_url):
        system = system + _PROFILE_GUIDANCE

    def _build_user(source_limit: int) -> str:
        if is_evolution:
            # Foreground the change: the diff is the assignment, the aggregate
            # is context.
            return f"""Write the article now. This is an UPDATE on a service we already
track — report on WHAT CHANGED, using the diff as your primary assignment.

Today (UTC): {today}
Service: {service_name}
Source URL: {source_url}
Source domain: {source_domain}
On-chain context (background only): round {round_num}, tx {txid}

WHAT CHANGED since we last looked (this is the story — unified diff):
```
{_clip(diff, DIFF_PROMPT_MAX_CHARS)}
```
{links_block}
{prior_block}
Full service aggregate (BACKGROUND ONLY — explain the change, don't re-report this):
```
{_clip(page_text, source_limit)}
```
{_clip(enrichment_block, 5000) if enrichment_block else ""}"""
        return f"""Write the article now from the material below.

Today (UTC): {today}
Publisher / monitor: {service_name}
Source URL: {source_url}
Source domain: {source_domain}
Page title from source: {page_title}
First snapshot: {is_first_snapshot}
On-chain context (background only): round {round_num}, tx {txid}
{prior_block}
Source material (may be days or years old — judge figures against today's date):
```
{_clip(page_text, source_limit)}
```
{links_block}
{diff_block}
{_clip(enrichment_block, 5000) if enrichment_block else ""}"""

    from app.core.config import MISTRAL_RESEARCH_SOURCE_CHARS

    user = _build_user(MISTRAL_MAX_SOURCE_CHARS)
    # Research rounds re-send the whole prompt every round — give them a
    # smaller source clip (they decide what to verify, they don't write from
    # it); the full clip rides only in the single stage-2 generation call.
    research_user = (
        _build_user(MISTRAL_RESEARCH_SOURCE_CHARS)
        if len(page_text) > MISTRAL_RESEARCH_SOURCE_CHARS
        else user
    )

    return _call_compose_via_writer_tools(
        system=system,
        user=user,
        research_user=research_user,
        source_url=source_url,
        mistral=mistral,
        topic=publish_topic,
    )


def _call_compose_via_writer_tools(**kwargs: object) -> MistralArticleFields:
    """Invoke the shared compose loop, omitting kwargs older workers may lack.

    Rolling deploys can briefly load a ``compose_scrape_article_mistral`` that
    passes ``research_user`` while the worker process still holds a pre-clip
    ``_compose_via_writer_tools`` definition — filter to the live signature so
    publish drains do not crash mid-deploy.
    """
    allowed = inspect.signature(_compose_via_writer_tools).parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in allowed.values()):
        return _compose_via_writer_tools(**kwargs)
    return _compose_via_writer_tools(**{k: v for k, v in kwargs.items() if k in allowed})


def _compose_via_writer_tools(
    *,
    system: str,
    user: str,
    source_url: str,
    mistral: MistralClient,
    topic: str = "",
    research_user: str | None = None,
    is_special_edition: bool = False,
) -> MistralArticleFields:
    """Shared research -> write -> grade/revise loop behind every writer-tools compose path. Only depends on the system/user prompt pair and a label (``source_url``) used for tool scoping and session/investigation bookkeeping — it doesn't assume the source material was a real scraped page, so callers can feed it a from-scratch topic assignment just as well as a scrape diff.

    ``research_user``: optional slimmer variant of ``user`` (smaller source
    clip) for the stage-1 research rounds, which re-send the whole prompt on
    every tool round; stage-2 generation always uses the full ``user``.
    Defaults to ``user``.

    ``is_special_edition``: quadruples the stage-1 research round budget
    (MISTRAL_MAX_TOOL_ROUNDS) for a genuinely deeper investigation, on top
    of the prompt's own depth instructions.
    """
    from app.modules.newspaper.compose_lock import compose_lock

    with compose_lock(label=source_url):
        return _compose_via_writer_tools_locked(
            system=system,
            user=user,
            source_url=source_url,
            mistral=mistral,
            topic=topic,
            research_user=research_user,
            is_special_edition=is_special_edition,
        )


def _run_research_floor(
    research_mistral: MistralClient,
    system: str,
    stage1_user: str,
    research_schemas: list[dict],
    research_handlers: dict,
    trace: list,
    debug: dict,
    *,
    is_special_edition: bool = False,
) -> None:
    """Research FLOOR: if the cold-research pass stopped too early (the exact failure where it reads an existing profile and quits), send it back to dig deeper — bounded to RESEARCH_FLOOR_MAX_PASSES extra passes.

    ``is_special_edition`` quadruples the distinct-source target, same 4x
    convention as research_max_rounds. Root-caused 2026-08-04: a special
    edition's Stage-1 research loop has no floor of its own (require_tool is
    None for research, so the instant the model stops calling tools the loop
    ends -- the 4x ROUND ceiling is irrelevant if the model never approaches
    it). The one existing safety net used the ordinary 6-source bar, which a
    routine multi-topic sweep clears in round 1 without ever approaching the
    depth a special edition is meant to have -- a real session touched 12+
    domains and stopped at round 4 of a possible 96, floor never engaged.
    """
    from app.core.config import (
        MISTRAL_TEMP_RESEARCH,
        RESEARCH_FLOOR_ENABLED,
        RESEARCH_FLOOR_MAX_PASSES,
        RESEARCH_MIN_TOOL_CALLS,
    )

    if not RESEARCH_FLOOR_ENABLED:
        return
    min_calls = RESEARCH_MIN_TOOL_CALLS * 4 if is_special_edition else RESEARCH_MIN_TOOL_CALLS
    # Root-caused 2026-08-04 (Humanitarian Network recompose): min_calls
    # above was quadrupled for a special edition, but this loop's own
    # budget for CLOSING that gap was not -- a session that plateaued at 8
    # distinct sources (genuinely ran out of easy new domains after two
    # nudges) got waved through 16 short of the 24-source target, writing a
    # 1,050-word piece that read no deeper than an ordinary article. The
    # floor exists specifically so quadrupling the target has teeth.
    max_passes = (
        RESEARCH_FLOOR_MAX_PASSES * 4 if is_special_edition else RESEARCH_FLOOR_MAX_PASSES
    )
    for _ in range(max(0, max_passes)):
        have = _distinct_research_calls(trace)
        if have >= min_calls:
            break
        nudge = _research_floor_nudge(have, min_calls, _format_research_digest(trace))
        research_mistral.chat_with_tools(
            [
                {"role": "system", "content": system + _RESEARCH_PHASE_GUIDANCE},
                {"role": "user", "content": stage1_user + nudge},
            ],
            tools=research_schemas,
            handlers=research_handlers,
            trace=trace,
            debug=debug,
            temperature=MISTRAL_TEMP_RESEARCH,
            require_tool=None,
            finalize_on_exhaustion=False,
        )


def _run_digest_gap_fill(
    research_mistral: MistralClient,
    system: str,
    stage1_user: str,
    research_schemas: list[dict],
    research_handlers: dict,
    trace: list,
    debug: dict,
    digest: str,
) -> str:
    """Gap-fill: the digest may flag specific unresolved-but-material gaps. Give the model ONE bounded extra research pass targeting exactly those before handing off to the tool-less writer, which otherwise either omits the gap (fine) or invents/recalls something to fill it. Re-synthesizes the digest afterward so the writer sees whatever was actually found (or an honest "still unresolved") rather than the pre-gap-fill digest."""
    from app.core.config import (
        DIGEST_GAP_FILL_ENABLED,
        DIGEST_GAP_FILL_MAX_ROUNDS,
        MISTRAL_TEMP_RESEARCH,
    )

    if not DIGEST_GAP_FILL_ENABLED:
        return digest
    gaps = _extract_unresolved_gaps(digest)
    if not gaps:
        return digest
    research_mistral.chat_with_tools(
        [
            {"role": "system", "content": system + _RESEARCH_PHASE_GUIDANCE},
            {"role": "user", "content": stage1_user + _gap_fill_nudge(gaps)},
        ],
        tools=research_schemas,
        handlers=research_handlers,
        trace=trace,
        debug=debug,
        temperature=MISTRAL_TEMP_RESEARCH,
        require_tool=None,
        max_rounds=DIGEST_GAP_FILL_MAX_ROUNDS,
        # The 2026-07-14 gap-fill pass ran out of rounds here and burned a
        # full discarded article write.
        finalize_on_exhaustion=False,
    )
    return _synthesize_research_digest(trace=trace, research_context=stage1_user)


# --- Special-edition-only deepening: enumerate -> targeted gap-fill -> outline
#
# 2026-08-04 (owner request, after a special-edition recompose came out
# shorter than two prior ordinary-tier versions of the same article): a
# prose Research Digest can bury a coverage gap in a paragraph, and its own
# "Unresolved Gaps" section is capped at 3 generic items. An explicit
# structured enumeration forces an accounting of every named person/place/
# date/service/number found so far, surfaces gaps a prose summary would
# miss, and gives a concrete outline for Stage 2 to write FROM instead of
# synthesizing organization cold from a raw digest.

_ENTITY_ENUMERATION_PROMPT = (
    "Research phase producing a structured ACCOUNTING, not the article. From "
    "everything found so far (raw trace + digest below), enumerate every "
    "distinct, named thing worth tracking for this special edition. Output "
    "Markdown ONLY, exactly these sections:\n\n"
    "## Entity Enumeration\n\n"
    "### People\n"
    "- Name — role/affiliation — what they're known for in this story — "
    "source. If none named, write exactly: None\n\n"
    "### Places\n"
    "- Country/region/city — why it matters to this story — source. If none "
    "named, write exactly: None\n\n"
    "### Dates & Events\n"
    "- Date — what happened — source. Order chronologically. If none, write "
    "exactly: None\n\n"
    "### Services, Products & Organizations\n"
    "- Name — what it is/does — its role in this story — source. If none, "
    "write exactly: None\n\n"
    "### Key Numbers\n"
    "- The figure — what it measures — source — whether independently "
    "verified (on-chain/primary document) or only self-reported/secondhand. "
    "If none, write exactly: None\n\n"
    "### Coverage Gaps\n"
    "- For each entity above where something material is missing (a person "
    "with no stated role, a date with no confirmed source, a service with "
    "no verified current numbers, a self-reported figure with no "
    "independent check attempted), name the SPECIFIC missing fact and what "
    "kind of tool call might resolve it. Up to 5. A gap you could have just "
    "gone and checked already does not belong here. If genuinely nothing "
    "material is missing, write exactly: None\n\n"
    "Do not write the article."
)


def _run_entity_enumeration(*, trace: list[dict], digest: str) -> str:
    """Structured People/Places/Dates/Services/Numbers accounting, synthesized from the trace + digest already gathered. Same lightweight digest-tier client as _synthesize_research_digest -- this is synthesis over already-fetched material, not new research. Empty trace or any failure yields "" (caller treats that as no enumeration available, never a hard failure)."""
    raw_trace = _format_research_digest(trace)
    if not raw_trace.strip():
        return ""
    try:
        from app.core.config import MISTRAL_TEMP_RESEARCH

        digest_client = get_mistral_digest_client()
        enumeration = digest_client.chat_completion(
            [
                {
                    "role": "user",
                    "content": (
                        f"Research Digest so far:\n{digest}\n\n"
                        "Raw tool trace (reference — synthesize, do not dump verbatim):\n"
                        f"{raw_trace}\n\n{_ENTITY_ENUMERATION_PROMPT}"
                    ),
                },
            ],
            json_object=False,
            temperature=MISTRAL_TEMP_RESEARCH,
        )
        return (enumeration or "").strip()
    except Exception:
        logger.warning("entity enumeration failed; continuing without it", exc_info=True)
        return ""


def _extract_enumeration_gaps(enumeration: str) -> str:
    """Pull the '### Coverage Gaps' section out of an entity enumeration -- the signal that tells the compose loop whether a second, more targeted gap-fill research pass is worth running. Mirrors _extract_unresolved_gaps' parsing exactly, over a different section name."""
    marker = "### Coverage Gaps"
    idx = enumeration.find(marker)
    if idx == -1:
        return ""
    section = enumeration[idx + len(marker) :]
    next_heading = section.find("\n### ")
    if next_heading != -1:
        section = section[:next_heading]
    section = section.strip().strip("-").strip()
    if not section or section.lower().rstrip(".") == "none":
        return ""
    return section


def _enumeration_gap_fill_nudge(gaps: str) -> str:
    """A second, more targeted research pass beyond the plain digest gap-fill -- these gaps came from an explicit per-entity accounting (a specific person/date/service/number), not a generic 3-item cap on a prose summary."""
    return (
        "\n\nSTOP — the entity enumeration flagged specific coverage gaps; make "
        f"one real attempt at resolving them before moving on:\n{gaps}\n\n"
        "Call whichever tools could plausibly answer them. If a tool call does "
        "not turn up the answer, that is a legitimate, acceptable outcome — do "
        "NOT guess or recall the answer from memory/training instead. Stop "
        "once you have made a genuine attempt at each gap."
    )


def _run_enumeration_gap_fill(
    research_mistral: MistralClient,
    system: str,
    stage1_user: str,
    research_schemas: list[dict],
    research_handlers: dict,
    trace: list,
    debug: dict,
    gaps: str,
) -> None:
    """One bounded extra tool-calling pass targeting the entity enumeration's own Coverage Gaps -- distinct from (and runs after) the plain digest gap-fill, since the enumeration surfaces gaps a prose digest's generic cap can miss entirely."""
    from app.core.config import (
        MISTRAL_TEMP_RESEARCH,
        SPECIAL_EDITION_ENUMERATION_GAP_FILL_MAX_ROUNDS,
    )

    research_mistral.chat_with_tools(
        [
            {"role": "system", "content": system + _RESEARCH_PHASE_GUIDANCE},
            {"role": "user", "content": stage1_user + _enumeration_gap_fill_nudge(gaps)},
        ],
        tools=research_schemas,
        handlers=research_handlers,
        trace=trace,
        debug=debug,
        temperature=MISTRAL_TEMP_RESEARCH,
        require_tool=None,
        max_rounds=SPECIAL_EDITION_ENUMERATION_GAP_FILL_MAX_ROUNDS,
        finalize_on_exhaustion=False,
    )


_NARRATIVE_OUTLINE_PROMPT = (
    "Research, digest, and entity enumeration are complete. Before writing, "
    "produce a narrative OUTLINE — not the article itself. Output Markdown "
    "ONLY, exactly these sections:\n\n"
    "## Narrative Outline\n\n"
    "### Throughline\n"
    "- One or two sentences: the single throughline connecting every section "
    "below — what is this piece actually ABOUT, beyond a list of facts?\n\n"
    "### Sections\n"
    "- For each proposed section, in writing order: a working header, which "
    "specific entities/facts from the enumeration it covers, and the ONE "
    "thing a reader should take from it. Plan only — do not write prose.\n\n"
    "### Contrasts & Tensions To Keep\n"
    "- Any genuine contrast or tension surfaced in research (verified vs. "
    "self-reported, deployed vs. only announced, a criticism alongside a "
    "claim) that must survive into the write, not get smoothed over for a "
    "cleaner narrative. If none, write exactly: None"
)


def _run_narrative_outline(*, digest: str, enumeration: str) -> str:
    """A concrete section-by-section plan for Stage 2 to write from, instead of synthesizing organization cold from a raw digest. Same lightweight digest-tier client as digest synthesis -- this is planning over already-gathered material, not new research. Empty on failure (caller treats a missing outline as "write from the digest alone," never a hard failure)."""
    if not digest.strip() and not enumeration.strip():
        return ""
    try:
        from app.core.config import MISTRAL_TEMP_RESEARCH

        digest_client = get_mistral_digest_client()
        outline = digest_client.chat_completion(
            [
                {
                    "role": "user",
                    "content": (
                        f"Research Digest:\n{digest}\n\nEntity Enumeration:\n{enumeration}\n\n"
                        f"{_NARRATIVE_OUTLINE_PROMPT}"
                    ),
                },
            ],
            json_object=False,
            temperature=MISTRAL_TEMP_RESEARCH,
        )
        return (outline or "").strip()
    except Exception:
        logger.warning("narrative outline synthesis failed; continuing without it", exc_info=True)
        return ""


def _run_special_edition_deepening(
    research_mistral: MistralClient,
    system: str,
    stage1_user: str,
    research_schemas: list[dict],
    research_handlers: dict,
    trace: list,
    debug: dict,
    digest: str,
) -> tuple[str, str, str]:
    """Special-edition-only Stage 1c/1d: enumerate -> targeted gap-fill -> re-synthesize digest -> outline. Returns (digest, enumeration, outline); any disabled/failed step degrades gracefully (empty enumeration/outline, unchanged digest) rather than blocking the compose."""
    from app.core.config import SPECIAL_EDITION_OUTLINE_ENABLED

    if not SPECIAL_EDITION_OUTLINE_ENABLED:
        return digest, "", ""

    enumeration = _run_entity_enumeration(trace=trace, digest=digest)
    gaps = _extract_enumeration_gaps(enumeration) if enumeration else ""
    if gaps:
        _run_enumeration_gap_fill(
            research_mistral,
            system,
            stage1_user,
            research_schemas,
            research_handlers,
            trace,
            debug,
            gaps,
        )
        digest = _synthesize_research_digest(trace=trace, research_context=stage1_user)
    outline = _run_narrative_outline(digest=digest, enumeration=enumeration)
    return digest, enumeration, outline


def _append_stage2_debug_turn(debug: dict, digest: str, payload: dict) -> None:
    """The warm pass runs outside the tool loop, so its turn isn't in the debug transcript — add it so Sessions shows the draft. Store the ACTUAL digest text (not a placeholder): it's the only place to audit whether the research→write handoff (small-model synthesis) preserved or lost/garbled facts from the raw trace — previously this turn was a stub and the digest was never visible anywhere, so a bad handoff was undiagnosable after the fact."""
    if not isinstance(debug.get("messages"), list):
        return
    debug["messages"].append(
        {
            "role": "user",
            "content": (
                "[stage 2 handoff] Research Digest:\n" + digest
                if digest.strip()
                else "[stage 2] generate the article from research findings"
            ),
        }
    )
    debug["messages"].append({"role": "assistant", "content": json.dumps(payload)[:4000]})


def _run_two_stage_compose(
    *,
    research_mistral: MistralClient,
    mistral: MistralClient,
    system: str,
    user: str,
    research_user: str | None,
    tool_schemas: list[dict],
    tool_handlers: dict,
    trace: list,
    debug: dict,
    checkpoint: Callable[[str], None],
    max_rounds: int | None = None,
    is_special_edition: bool = False,
) -> dict:
    """Two-stage compose: cold research (tools, low temp) on the Small research tier, a floor + gap-fill pass if it under-researched, a structured digest handoff, then a warm no-tools generation on the writer tier, and finally deterministic grade/revise."""
    from app.core.config import MISTRAL_MODEL_RESEARCH, MISTRAL_TEMP_RESEARCH, MISTRAL_TEMP_WRITE

    checkpoint("researching")
    debug["research_model"] = MISTRAL_MODEL_RESEARCH
    # Stage 1 — cold research: tools available (minus review_draft, no
    # draft yet), low temp for deterministic tool selection. We keep the
    # trace; the model's prose here is discarded. Research rounds
    # re-send the whole conversation every round, so they get the
    # slimmer research_user when the caller provided one. Runs on the
    # Small research tier — better tool-calling, cheaper per round.
    stage1_user = research_user or user
    research_schemas = [
        s for s in tool_schemas if (s.get("function") or {}).get("name") != "review_draft"
    ]
    research_handlers = {k: v for k, v in tool_handlers.items() if k != "review_draft"}
    research_mistral.chat_with_tools(
        [
            {"role": "system", "content": system + _RESEARCH_PHASE_GUIDANCE},
            {"role": "user", "content": stage1_user},
        ],
        tools=research_schemas,
        handlers=research_handlers,
        trace=trace,
        debug=debug,
        temperature=MISTRAL_TEMP_RESEARCH,
        require_tool=None,
        max_rounds=max_rounds,
        # Research runs for its tool side-effects (the trace); the
        # return value is discarded — never pay for a final
        # article completion on round exhaustion.
        finalize_on_exhaustion=False,
    )
    _run_research_floor(
        research_mistral,
        system,
        stage1_user,
        research_schemas,
        research_handlers,
        trace,
        debug,
        is_special_edition=is_special_edition,
    )
    # Stage 1b — synthesize a structured Research Digest handoff so Stage 2
    # grounds on high-signal facts, not raw tool JSON.
    digest = _synthesize_research_digest(trace=trace, research_context=stage1_user)
    digest = _run_digest_gap_fill(
        research_mistral,
        system,
        stage1_user,
        research_schemas,
        research_handlers,
        trace,
        debug,
        digest,
    )
    # Stage 1c/1d — special-edition-only: enumerate every named entity found
    # (surfaces coverage gaps a prose digest's generic 3-item cap can miss),
    # a second targeted gap-fill pass on exactly those, then a narrative
    # outline so Stage 2 writes FROM a concrete structure instead of
    # synthesizing organization cold from a raw digest.
    enumeration = ""
    outline = ""
    if is_special_edition:
        digest, enumeration, outline = _run_special_edition_deepening(
            research_mistral,
            system,
            stage1_user,
            research_schemas,
            research_handlers,
            trace,
            debug,
            digest,
        )
    checkpoint("writing")  # research (+ gap-fill/deepening) done, now generating
    gen_user = _build_stage2_user(
        user=user,
        digest=digest,
        is_special_edition=is_special_edition,
        enumeration=enumeration,
        outline=outline,
    )
    gen_system = system + _STAGE2_GENERATION_GUIDANCE
    payload = mistral.chat_json_object(
        [
            {"role": "system", "content": gen_system},
            {"role": "user", "content": gen_user},
        ],
        temperature=MISTRAL_TEMP_WRITE,
    )
    _append_stage2_debug_turn(debug, digest, payload)
    # Stage 3+4 — deterministic grade, then one revision if weak.
    return _review_and_revise(
        mistral,
        payload,
        system=system,
        gen_user=gen_user,
        trace=trace,
        debug=debug,
        user=user,
        research_user=research_user,
        is_special_edition=is_special_edition,
    )


def _apply_post_compose_gates(
    payload: dict,
    trace: list,
    *,
    user: str,
    research_user: str | None,
    service_id: str = "",
    glossary_client: MistralClient | None = None,
) -> dict:
    """Sequential deterministic post-compose gates plus writer-declared judgment flags read from the trace. Order matters: the defunct-entity veto must precede the link-gate delinker below, so it still sees the writer's original links."""
    # Stage-2 assembly: append every successfully fetched research URL the
    # body doesn't already cite, so deep links survive into the published
    # article (lifts citation density; preserves existing prose).
    payload = append_reference_block(payload, trace)
    # Defunct-entity veto: if the body links any domain that no longer
    # resolves to a usable address — whether the research fetched it or
    # the writer recommended it blind — hold the whole draft for review.
    # Delinking can't undo a defunct entity recommended in prose (MyAlgo
    # incident 2026-07-19).
    from app.modules.newspaper.defunct_entity_gate import flag_defunct_entities

    payload = flag_defunct_entities(payload, trace)
    # Deterministic link gate: delink body urls the research never
    # surfaced and that don't resolve live (invented-url pattern the
    # numeric gatekeeper can't see — RandGallery incident 2026-07-16).
    from app.modules.newspaper.link_gate import sanitize_untraced_links

    payload = sanitize_untraced_links(payload, trace)
    # Quotation-integrity gate: quotation marks are a verbatim claim —
    # de-quote any 4+-word quotation that isn't word-for-word in the
    # research trace or the compose input (same incident: an invented
    # phrase was attributed to the Goanna Council in quotes).
    from app.modules.newspaper.quote_gate import unquote_ungrounded_quotes

    payload = unquote_ungrounded_quotes(payload, trace, extra_texts=[user, research_user or ""])
    # Chain-entity gate: cited ASA ids / addresses / txids must exist
    # on-chain — verified ones get auto-linked to an explorer,
    # provably-missing ones are delinked (AlgoGlyph incident
    # 2026-07-17: a real asset's holder share was reported as double
    # its true percentage; a clickable explorer link on the cited
    # asset/address makes that class of error checkable by anyone).
    from app.modules.newspaper.chain_entity_gate import link_and_verify_chain_entities

    payload = link_and_verify_chain_entities(
        payload, trace, extra_texts=[user, research_user or ""]
    )
    # Authority backstop: any "experts say"/"industry research
    # suggests" sentence the revision loop didn't fix is excised —
    # unattributable by construction, and the one that shipped
    # (2026-07-18 quantum draft) was a fabricated benchmark.
    from app.modules.newspaper.authority_gate import excise_unattributed_authority

    payload = excise_unattributed_authority(payload)
    # Unsourced-specifics gate (read-only for now): record hard specifics
    # — traction/funding numbers, named partners/backers — that don't
    # trace to a fetched tool result, so we can measure extraction
    # precision before enforcing. Catches the GoPlausible class (fetched
    # zero-counters overwritten with "1,000 issuers / 70+ events /
    # Borderless Capital") that every other gate misses.
    from app.modules.newspaper.unsourced_specifics_gate import flag_unsourced_specifics

    payload = flag_unsourced_specifics(payload, trace, extra_texts=[user, research_user or ""])
    # Stale-deadline backstop (read-only for now): any lapsed-deadline-framed-
    # as-open sentence the revision loop didn't catch (or that never went
    # through a revision loop at all, e.g. the article-edit path) is recorded
    # for visibility (Meld Gold 2026-08-04). See stale_deadline_gate.py.
    from app.modules.newspaper.stale_deadline_gate import stale_deadline_issues

    stale_deadlines = stale_deadline_issues(str(payload.get("body", "")))
    if stale_deadlines:
        payload["_stale_deadlines"] = stale_deadlines
    # Writer-declared breaking news (replaces the deterministic keyword
    # classifier, disabled 2026-07-17): scanned from the trace like the
    # gates above, since mark_breaking_news never mutates the draft —
    # it's a judgment call the publish gate reads afterward.
    from app.modules.ai.breaking_news_tool import breaking_reason_from_trace

    breaking_reason = breaking_reason_from_trace(trace)
    if breaking_reason is not None:
        payload["_breaking_reason"] = breaking_reason
    # Writer-confirmed alert class (confirm_alert_topic tool) — same
    # post-hoc trace scan; the publish gate uses it to decide whether
    # a keyword-routed scam/incident topic earns its reader-facing
    # tag and match-key carve-out.
    from app.modules.ai.alert_topic_tool import confirmed_alert_from_trace

    confirmed_alert = confirmed_alert_from_trace(trace)
    if confirmed_alert is not None:
        payload["_confirmed_alert"] = confirmed_alert
    # Glossary suggestion: a small classification pass over the finished
    # body, queuing draft terms for admin review (see glossary_suggest_gate
    # docstring — replaces the tool-call path, which was only reachable
    # before the article's prose existed). Skipped when the caller has no
    # client to lend it (legacy path with tools disabled, no research
    # client ever constructed).
    if glossary_client is not None:
        from app.modules.newspaper.glossary_suggest_gate import suggest_glossary_terms

        payload = suggest_glossary_terms(payload, client=glossary_client, service_id=service_id)
    return payload


def _record_compose_telemetry(
    source_url: str,
    trace: list,
    raw: str,
    *,
    report_errors_model: str,
    duration_ms: int,
    session_id: UUID,
    created_at: datetime,
    debug: dict,
    usage_so_far: Callable[[], dict[str, int]],
) -> None:
    """Best-effort: store investigation findings and tool-insight telemetry for this compose session. Never raises — a telemetry failure must not fail the compose."""
    from app.core.config import MISTRAL_MODEL_WRITER

    try:
        from app.modules.newspaper.investigation_store import store_investigation_findings

        store_investigation_findings(service_id=source_url, source_url=source_url, trace=trace)
    except Exception:
        logger.warning("failed to store investigation findings for %s", source_url, exc_info=True)
    try:
        from app.modules.ai.tool_insights_store import (
            record_compose_session,
            record_tool_usage_from_trace,
            report_tool_errors_from_trace,
        )

        report_tool_errors_from_trace(
            trace,
            service_id=source_url,
            source_url=source_url,
            model=report_errors_model,
        )
        record_tool_usage_from_trace(trace)
        final_usage = usage_so_far()
        record_compose_session(
            debug=debug,
            trace=trace,
            service_id=source_url,
            source_url=source_url,
            model=MISTRAL_MODEL_WRITER,
            final_output=raw,
            status="ok",
            duration_ms=duration_ms,
            session_id=session_id,
            created_at=created_at,
            prompt_tokens=final_usage["prompt_tokens"],
            completion_tokens=final_usage["completion_tokens"],
            total_tokens=final_usage["total_tokens"],
        )
    except Exception:
        logger.warning("failed to record tool-insights session", exc_info=True)


def _compose_via_writer_tools_locked(
    *,
    system: str,
    user: str,
    source_url: str,
    mistral: MistralClient,
    topic: str = "",
    research_user: str | None = None,
    is_special_edition: bool = False,
) -> MistralArticleFields:
    from app.core.config import WRITER_TOOLS_ENABLED

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if WRITER_TOOLS_ENABLED:
        try:
            from app.core.config import (
                MISTRAL_MAX_TOOL_ROUNDS,
                MISTRAL_MODEL_RESEARCH,
                MISTRAL_MODEL_WRITER,
                MISTRAL_TIMEOUT_SECONDS,
                MISTRAL_TIMEOUT_SPECIAL_EDITION_MULTIPLIER,
                WRITER_TWO_STAGE,
            )
            from app.modules.ai.writer_tools import all_tools

            research_max_rounds = (
                MISTRAL_MAX_TOOL_ROUNDS * 4 if is_special_edition else None
            )
            # A special edition's research chat_with_tools loop resends the
            # whole accumulated trace every round; by round 16+ that prompt
            # is large enough that the plain per-attempt timeout isn't
            # always enough (root-caused 2026-08-04 -- see config.py).
            research_timeout = (
                MISTRAL_TIMEOUT_SECONDS * MISTRAL_TIMEOUT_SPECIAL_EDITION_MULTIPLIER
                if is_special_edition
                else None
            )

            research_mistral = get_mistral_research_client(timeout=research_timeout)
            tool_context = {
                "service_id": source_url,
                "source_url": source_url,
                "model": MISTRAL_MODEL_RESEARCH,
            }
            tool_schemas, tool_handlers = all_tools(context=tool_context, topic=topic)
            trace: list = []
            debug: dict = {}
            import time as _time

            _t0 = _time.monotonic()

            # Progress checkpoints: one stable session row, upserted at each stage,
            # so the admin Sessions view shows live progress (research -> writing
            # -> done) instead of nothing until the very end.
            from app.modules.ai.tool_insights_store import (
                new_session_ref,
                record_compose_session,
            )

            _sid, _screated = new_session_ref()

            def _usage_so_far() -> dict[str, int]:
                """Combined token usage across both clients used in this session (research_mistral for stage 1, mistral for stage 2/revise) — each is a fresh instance per compose, so its counter is this session's total, not a lifetime one."""
                research_usage = research_mistral.usage_totals()
                write_usage = mistral.usage_totals()
                return {
                    key: research_usage[key] + write_usage[key]
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                }

            def _checkpoint(stage_status: str) -> None:
                with contextlib.suppress(Exception):
                    usage = _usage_so_far()
                    record_compose_session(
                        debug=debug,
                        trace=trace,
                        service_id=source_url,
                        source_url=source_url,
                        model=(
                            MISTRAL_MODEL_RESEARCH
                            if stage_status == "researching"
                            else MISTRAL_MODEL_WRITER
                        ),
                        status=stage_status,
                        duration_ms=int((_time.monotonic() - _t0) * 1000),
                        session_id=_sid,
                        created_at=_screated,
                        prompt_tokens=usage["prompt_tokens"],
                        completion_tokens=usage["completion_tokens"],
                        total_tokens=usage["total_tokens"],
                    )

            if WRITER_TWO_STAGE:
                payload = _run_two_stage_compose(
                    research_mistral=research_mistral,
                    mistral=mistral,
                    system=system,
                    user=user,
                    research_user=research_user,
                    tool_schemas=tool_schemas,
                    tool_handlers=tool_handlers,
                    trace=trace,
                    debug=debug,
                    checkpoint=_checkpoint,
                    max_rounds=research_max_rounds,
                    is_special_edition=is_special_edition,
                )
            else:
                # Legacy single agentic loop: tools + final article in one pass.
                raw = mistral.chat_with_tools(
                    [
                        {"role": "system", "content": system + _TOOLS_GUIDANCE},
                        {"role": "user", "content": user},
                    ],
                    tools=tool_schemas,
                    handlers=tool_handlers,
                    trace=trace,
                    debug=debug,
                    require_tool="review_draft",
                    max_rounds=research_max_rounds,
                )
                payload = json.loads(raw)

            payload = _apply_post_compose_gates(
                payload,
                trace,
                user=user,
                research_user=research_user,
                service_id=source_url,
                glossary_client=research_mistral,
            )
            raw = json.dumps(payload)
            _duration_ms = int((_time.monotonic() - _t0) * 1000)
            _record_compose_telemetry(
                source_url,
                trace,
                raw,
                report_errors_model=(
                    MISTRAL_MODEL_RESEARCH if WRITER_TWO_STAGE else MISTRAL_MODEL_WRITER
                ),
                duration_ms=_duration_ms,
                session_id=_sid,
                created_at=_screated,
                debug=debug,
                usage_so_far=_usage_so_far,
            )
            return _parse_article_fields(payload)
        except StorySpikedError as spike:
            # The writer refused the story (abort_article tool) — a judgment,
            # not a failure. MUST be caught before the generic Exception
            # below: falling through would trigger the ungrounded single-shot
            # fallback, i.e. compose exactly the evidence-free article the
            # writer just declined to write. The trace already carries the
            # spike call (mistral_client records it before re-raising), so
            # the Sessions view shows the writer's own reasoning.
            logger.info(
                "writer spiked story for %s (%s): %s",
                source_url,
                spike.category,
                spike.reason,
            )
            with contextlib.suppress(Exception):
                _checkpoint("aborted_by_writer")
            raise
        except MistralCreditError:
            # 401/402 — no retry will help (bad key or credit exhausted), so
            # tag it distinctly from a generic API error: the admin Sessions
            # view and the queue's last_reason should say WHY at a glance
            # instead of a plain "error" that looks the same as any other fault.
            with contextlib.suppress(Exception):
                _checkpoint("credit_insufficient")
            raise
        except MistralError:
            # A real API error (rate limit, etc.) — already retried with backoff
            # inside the client. Don't burn another call on a single-shot retry
            # that will just fail the same way; let the caller fall to template.
            # Finalize the checkpoint row first, else the Sessions view shows it
            # frozen at 'researching'/'writing' forever (looks stuck mid-compose).
            with contextlib.suppress(Exception):
                _checkpoint("error")
            raise
        except Exception:
            # Tool/parse failure (the API worked): fall back to single-shot. Mark
            # the row 'fallback' so it leaves the non-terminal state instead of
            # appearing stuck mid-compose.
            #
            # Root-caused 2026-07-16: this branch swallowed the exception with
            # ZERO logging, so a real crash (dork.fi: 16 real tool calls,
            # including a genuine docs.dork.fi fetch, then silently died before
            # the next assistant turn) was only reconstructable after the fact
            # by manually replaying compose_sessions.messages — the traceback
            # itself was gone forever. logger.exception here costs nothing and
            # makes every future one of these actually diagnosable.
            logger.exception("compose fell back to ungrounded single-shot for %s", source_url)
            with contextlib.suppress(Exception):
                _checkpoint("fallback")

    payload = mistral.chat_json_object(messages)
    return _parse_article_fields(payload)


_SPECIAL_EDITION_DEPTH_INSTRUCTIONS = """

SPECIAL EDITION: this is a deliberate in-depth feature, not a routine news
item — an editor chose this topic for real investigative treatment. Act like
an investigative journalist: dig past the surface-level facts, chase down
primary sources, cross-check claims against on-chain/on-record evidence, and
follow up on anything that looks thin, contradictory, or worth questioning.
Use your full tool budget for genuine multi-source research across this
session rather than a quick single-pass writeup.

There is no target length — let it run as long as the material genuinely
supports, never pad with repetition or filler to hit a count, and never cut
a real finding short to stay brief. The piece should read as a narrative,
not a list of facts: build it around a clear throughline that connects
background/context, the current state (with specifics), comparison or
broader implications, and open questions or what to watch next, so a reader
comes away with a story, not just a summary.

ON-CHAIN VERIFICATION: when the story turns on a specific on-chain figure —
an asset's supply/holder share, a transaction/transfer count, funds moved
through a contract — verify it with an on-chain tool (lookup_asset,
lookup_account, get_asset_holder_share, lookup_application) rather than
relying on a press release or partner's own quoted number for it. A
headline dollar/volume figure repeated from a PR without an independent
on-chain check is exactly the kind of surface-level fact this piece exists
to dig past.

RECENCY WITHIN A SOURCE: a single fetched page can describe several
chronologically distinct events — do not treat an earlier one as the
current state when the same page also describes a later one. Identify
which described event is most recent (by its own dateline, or by explicit
sequencing language like "today," "ahead of," "building on") and lead with
that: describing last year's meeting as the current state of a coalition
when the same source is reporting on this month's follow-up summit is
citing the source you actually read as if you had not read all of it.

DO NOT DROP A FOUND CONTRAST: if research surfaces a genuine contrast worth
including — a deployed, operating program versus one that is only
announced or planned; a claim that is independently verified versus one
that rests on a single self-interested source — that contrast is exactly
the kind of finding an investigative piece exists to surface. Keep it in,
even when it complicates a clean narrative or undercuts the more flattering
half of the story; dropping the less convenient finding is the failure
mode this depth pass exists to prevent, not an edit for concision.

SEARCH WIDE, NOT DEEP-ON-THE-SAME-THING: search_web accepts limit up to 12
(default 6) — pass a high limit (10-12) on your searches instead of the
default, and spend your searches on DIFFERENT angles and topics (each named
initiative, partner, criticism, or follow-up event gets its own query)
rather than re-querying narrower variations of the same one or two stories
you already found. Root-caused 2026-08-04: a special-edition research pass
made 11 search_web calls but surfaced only 8 distinct domains total —
several calls used limit 3-4 (below even the default), and most queries
kept circling back to the same two sources instead of branching out."""


def compose_assignment_article_mistral(
    *,
    brief_title: str,
    brief_body: str,
    keywords: str,
    brief_id: str,
    is_special_edition: bool = False,
    client: MistralClient | None = None,
) -> MistralArticleFields:
    """Generate a from-scratch article for an editor-assigned topic (no scraped source page). Unlike ``compose_scrape_article_mistral``, the brief text is NOT verified fact — the model must substantiate the topic itself via tools before writing, using the same research -> write -> grade/revise loop. ``is_special_edition`` requests a longer, multi-angle in-depth treatment instead of the standard length-scaled-to-substance pass."""
    mistral = client or get_mistral_client()
    today = _today_utc()

    system = _writer_system_prompt(today, assignment=True)
    user = f"""Write the article now on the assigned topic below.

Today (UTC): {today}
Assignment source: editorial brief (not a scraped page — research this from scratch)

Topic / working title: {brief_title}
Focus keywords: {keywords or "(none given)"}

Editorial pointers (a starting brief, NOT verified fact — confirm via tools before
relying on anything here):
```
{_clip(brief_body)}
```

This is a from-scratch research assignment. Use your tools extensively (official
sites, GitHub, on-chain data, market data, etc. as relevant) to gather current,
verifiable facts before writing.{_SPECIAL_EDITION_DEPTH_INSTRUCTIONS if is_special_edition else ""}"""

    return _call_compose_via_writer_tools(
        system=system,
        user=user,
        source_url=f"editorial://brief/{brief_id}",
        mistral=mistral,
        topic="editorial_assignment",
        is_special_edition=is_special_edition,
    )


def compose_recap_from_transcript_mistral(
    *,
    service_name: str,
    source_url: str,
    page_title: str,
    transcript_text: str,
    client: MistralClient | None = None,
) -> MistralArticleFields:
    """Community-call recap from a video transcript (Phase 4).

    Uses the premium model — transcripts are long-form input.
    """
    from app.core.config import MISTRAL_MODEL_PREMIUM

    mistral = client or MistralClient(model=MISTRAL_MODEL_PREMIUM)
    system = (
        "You are a news editor recapping an Algorand community call from its "
        "transcript. Summarize what was discussed and announced for readers who "
        "missed the call. Quote speakers only when the transcript clearly "
        "attributes the words.\n\n"
        "Write the recap as a single JSON object with this schema:\n"
        '{"title": "string", "summary": "string", "body": "string"}\n'
        "- title: headline naming the call, max 120 chars\n"
        "- summary: 1–2 sentences for a feed card, max 280 chars\n"
        "- body: markdown with # headline, ## Key takeaways (bullet list), "
        "## Discussion highlights, and ## Watch the recording linking the URL\n\n"
        f"{_JSON_ONLY}"
    )
    user = f"""Write the community-call recap from the transcript below.

Event host: {service_name}
Video / source URL: {source_url}
Event title: {page_title}

Transcript:
```
{_clip(transcript_text, 12000)}
```"""

    payload = mistral.chat_json_object(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=2048,
    )
    return _parse_article_fields(payload)


def compose_weekly_digest_article_mistral(
    context: WeeklyDigestContext,
    *,
    client: MistralClient | None = None,
) -> MistralArticleFields:
    """Generate full weekly digest (price + feed highlights) via Mistral."""
    from app.core.config import PUBLIC_ARTICLE_BASE_URL

    mistral = client or get_mistral_digest_client()
    snap = context.price
    article_lines = []
    for item in context.articles[:25]:
        url = f"{PUBLIC_ARTICLE_BASE_URL}/{item.article_id}"
        article_lines.append(f"- [{item.title}]({url}) | {item.summary[:200]}")
    feed_block = "\n".join(article_lines) if article_lines else "(no feed articles this week)"

    system = (
        "You are the editor of an Algorand community newspaper weekly digest. "
        "Combine market data and article highlights into one cohesive issue. "
        "Be factual; do not invent articles.\n\n"
        "Write the digest as a single JSON object with keys: title, summary, body.\n"
        "- body: a SINGLE markdown STRING (never a nested object) with sections "
        '"## Market snapshot" (including a metrics table) and "## This week in '
        'the newspaper".\n'
        "- When you mention a highlighted article, link its title to the URL given "
        "for it using markdown [Title](url) so readers can open it. Use only the "
        "URLs provided below; never invent article links.\n\n"
        f"{_JSON_ONLY}"
    )
    user = f"""Write the weekly digest from the data below.

Week: {context.week_key} (label {context.week_label})

Market ({snap.asset_name} / {snap.asset_id}):
- current: ${snap.price_usd} {snap.currency}
- 7d open: ${snap.week_open_usd}
- 7d high: ${snap.week_high_usd}
- 7d low: ${snap.week_low_usd}
- 7d change %: {snap.week_change_pct:+.2f}

Articles published this week ({len(context.articles)}):
{feed_block}{_price_metrics_block(snap.asset_id)}"""

    payload = mistral.chat_json_object(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=2048,
    )
    return _parse_article_fields(payload)


class TranslationAlignmentError(Exception):
    """A translation came back with a different block count than its source."""


def split_markdown_blocks(text: str) -> list[str]:
    """Split markdown into blank-line-separated blocks, never cutting inside a fenced code block.

    Blocks, not sentences, are the translation unit (see
    translate_article_mistral). A markdown table has no blank lines inside it,
    so it survives as one block for free — which matters, because 59% of the
    live corpus contains one. Fenced code needs the explicit guard: a fence
    with a blank line in it would otherwise be split down the middle and both
    halves would render as broken markdown.
    """
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in (text or "").replace("\r\n", "\n").split("\n"):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        if not in_fence and not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


def _aligned_blocks(payload: dict, expected: int) -> list[str] | None:
    """Pull an aligned block list out of a translation payload, or None when it does not line up."""
    raw = payload.get("blocks")
    if not isinstance(raw, list) or len(raw) != expected:
        return None
    out = [_coerce_markdown(b).strip() for b in raw]
    return out if all(out) else None


def translate_article_mistral(
    *,
    english_title: str,
    english_summary: str,
    english_body: str,
    target_language: str,
    client: MistralClient | None = None,
) -> dict[str, str]:
    """Translate an English article to the target language via Mistral, block-aligned to the source.

    Runs on the Small tier (MISTRAL_MODEL_TRANSLATE) — localization needs no
    research or editorial judgment, and this fires once per target language per
    published article.

    The body is translated as a BLOCK-ALIGNED list rather than one opaque
    string. The model still sees the whole article (coherence, pronouns,
    terminology all need document context) and still gets full freedom to
    restructure prose INSIDE a block — that freedom is what fixes the
    word-by-word reading. But block boundaries are hard: it must return exactly
    one entry per source block, so drift is caught at parse time instead of
    never.

    That check is not hypothetical. Auditing the 660 stored translations on
    2026-07-29 found 18.5% with a paragraph count different from their source,
    including a Persian article that collapsed 42 paragraphs into 9 — roughly
    three quarters of the piece gone, live and indexed, undetected. Translation
    is the only content lane with no gate; alignment is the cheapest one that
    works on languages nobody here reads.
    """
    from app.core.article_translation_langs import (
        ARTICLE_TRANSLATION_LANG_NAMES,
        digits_block,
        glossary_block,
    )
    from app.core.config import MISTRAL_MODEL_TRANSLATE

    mistral = client or get_mistral_client(model=MISTRAL_MODEL_TRANSLATE)

    lang_name = ARTICLE_TRANSLATION_LANG_NAMES.get(target_language, target_language)
    blocks = split_markdown_blocks(english_body)
    n = len(blocks)
    if not n:
        return {"title": english_title, "summary": english_summary, "body": english_body}

    # The anti-calque rule below is the whole point of this prompt. The previous
    # version asked only for a tone ("professional, objective, and clear") and
    # said nothing about SENTENCE STRUCTURE, so the model transposed English
    # syntax word by word: grammatical, accurate, and unmistakably machine
    # output. Owner review of the live French and Spanish (2026-07-29) put it
    # as "not incorrect, but very word-by-word, giving weird phrases, hard to
    # read sentences" — the classic literal-MT artefact, and the thing readers
    # notice first. Telling the model to RE-EXPRESS rather than transpose costs
    # nothing and targets exactly that.
    system = (
        f"You are a professional journalist writing an Algorand news article in {lang_name}. "
        "You are not transcribing an English article — you are writing the same story for a "
        f"{lang_name}-speaking reader.\n\n"
        "TRANSLATE THE MEANING, NOT THE WORDS:\n"
        f"- Write natural, idiomatic {lang_name}. A native journalist must not be able to tell "
        "it began as English.\n"
        "- Do NOT mirror English clause order or sentence boundaries. Restructure freely: split "
        "long sentences, merge short ones, move clauses wherever the target language wants them.\n"
        "- Never calque an English idiom or fixed expression — use the equivalent a native "
        "speaker would actually reach for, even when it shares no words with the original.\n"
        "- When you NAME an entity via apposition (\"Algorand's native token, ALGO\" / a brand, "
        "a ticker, a protocol), the named term stays BARE in that slot — do not add an article or "
        "possessive to it there, even if the target language would normally use one. Verified "
        "against live French crypto press: \"OpenSea reveals $SEA, its native token\" style "
        "constructions never article the name in the naming position, only in a later standalone "
        "reference (\"$SEA then rose 10%\" DOES take the article once it is no longer being "
        "named).\n"
        "- Never state a term in the target language AND then restate the English acronym or "
        "abbreviation in parentheses right after — that is a language-mixing tell, not thoroughness. "
        "Decide once whether the term rides in English (as an accepted loan term, e.g. an "
        "abbreviation like APR) or is fully localized, and commit to exactly one.\n"
        "- Preserve every fact, figure, date, name and link exactly. Freedom applies to phrasing, "
        "never to content.\n\n"
        f"BLOCK ALIGNMENT — this is a hard requirement, not a preference. The body arrives as {n} "
        "numbered blocks. Return EXACTLY "
        f"{n} translated blocks, in the same order, one per source block.\n"
        "- Restructure sentences freely INSIDE a block. Never merge two blocks, never split one "
        "block into two, never drop a block, never reorder them.\n"
        "- A block that is a heading stays a heading; a list stays a list with the same number of "
        "items; a table stays a table with the same rows and columns. Translate the cell text, "
        "keep the pipes.\n"
        "- If a block genuinely needs no translation (a bare URL, a code block), return it "
        "unchanged rather than dropping it.\n"
        "- Omit the [n] markers from your output — they mark the input only.\n\n"
        "Do not translate names: 'Algorand', 'ALGO', DeFi protocol and product names, named "
        "events (e.g. a conference or retreat), and the proper name of a specific project, work, "
        "exhibit or installation being covered — keep it exactly as given, do not render it as a "
        "descriptive phrase, and do not drop the event or organization it is attributed to. "
        "Unless there is a universally accepted localized brand name, none of these translate.\n\n"
        "TITLE — same house headline rule as the English original: a claim about what happened, "
        "never the '<Name>: <description>' colon-label template (a headline shaped like "
        "'X: what X is'). If the English title avoids this, the translated title must too — do "
        "not introduce a colon label that was not in the source.\n\n"
        "Keep the tone professional and objective. Keep markdown formatting intact."
        f"{digits_block(target_language)}"
        f"{glossary_block(target_language)}\n\n"
        "Write the translation as a single JSON object adhering exactly to this schema:\n"
        '{"title": "string", "summary": "string", "blocks": ["string", ...]}\n\n'
        f"{_JSON_ONLY}"
    )
    numbered = "\n\n".join(f"[{i + 1}] {b}" for i, b in enumerate(blocks))
    user = f"""Translate this article into {lang_name}.

Title:
{english_title}

Summary:
{english_summary}

Body — {n} blocks, return exactly {n}:
{numbered}"""

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    payload = mistral.chat_json_object(messages)
    translated = _aligned_blocks(payload, n)
    if translated is None:
        # One corrective round. The model usually merges short adjacent blocks;
        # naming the count it returned is what gets it to recount rather than
        # re-emit the same shape.
        got = payload.get("blocks")
        got_n = len(got) if isinstance(got, list) else 0
        logger.warning(
            "translation block misalignment for %s: expected %d, got %d — retrying",
            target_language,
            n,
            got_n,
        )
        messages.append({"role": "assistant", "content": json.dumps(payload)[:2000]})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"That returned {got_n} blocks; the source has {n}. Redo it with EXACTLY "
                    f"{n} blocks, one per numbered source block, same order. Do not merge, "
                    "split, drop or reorder blocks."
                ),
            }
        )
        payload = mistral.chat_json_object(messages)
        translated = _aligned_blocks(payload, n)
    if translated is None:
        # Storing a misaligned translation is how a 42-block article became 9
        # and stayed live. Raise instead: the language simply stays missing, and
        # enqueue_missing_article_translations picks it up again next time.
        raise TranslationAlignmentError(
            f"{target_language}: could not align translation to {n} source blocks"
        )

    return {
        "title": str(payload.get("title") or "").strip() or english_title,
        "summary": str(payload.get("summary") or "").strip() or english_summary,
        "body": "\n\n".join(translated).strip() or english_body,
    }
