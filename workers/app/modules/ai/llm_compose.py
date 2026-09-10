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

from app.core.config import ADMIN_SOURCE_PROMPT_MAX_CHARS, LLM_MAX_SOURCE_CHARS
from app.modules.ai.llm_openai_compatible import MistralProvider, OpenAICompatibleProvider
from app.modules.ai.llm_provider import LLMCreditError, LLMError, LLMProvider
from app.modules.ai.llm_purpose_router import (
    get_llm_digest_client,
    get_llm_research_client,
    get_llm_rubric_client,
    get_llm_translate_client,
    get_llm_writer_client,
)
from app.modules.ai.reference_block import append_reference_block
from app.modules.ai.session_register import SessionRegister, SessionRegisterCassandra
from app.modules.ai.story_spike import StorySpikedError
from app.modules.metrics.price_metrics_store import load_mistral_context
from app.modules.newspaper.admin_source_store import AdminSource
from app.modules.newspaper.weekly_digest import WeeklyDigestContext

logger = logging.getLogger(__name__)

# Bump whenever a compose prompt in this module changes materially (system
# guidelines, _ARTICLE_FORMAT_RULES, recency/profile rules, etc). Stamped onto
# every stored article so analytics can correlate a prompt edit with a shift in
# grades/engagement instead of guessing from deploy timestamps.
PROMPT_VERSION = "2026-09-09.1"

# Same four counters compose_sessions' INSERT columns carry (prompt_tokens/
# completion_tokens/total_tokens/cached_tokens) -- the single vocabulary every
# usage_totals()/accumulator dict in this module uses.
_USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens", "cached_tokens")


def _merge_usage(accumulator: dict[str, int] | None, usage: dict[str, int]) -> None:
    """Add one LLM call's usage_totals() into a running accumulator, in place.

    `accumulator` is optional (None is a no-op) so every ephemeral-client call
    site below (rubric grading, digest synthesis, entity enumeration, the
    narrative outline, raw-mode gap extraction) can report into it
    unconditionally, regardless of whether the caller wired one up. These all
    build their OWN client instance via get_llm_rubric_client()/
    get_llm_digest_client() rather than reusing the compose's research_llm/
    llm pair (2026-08-06, so a compose can route research to one provider and
    grading to another -- see get_llm_rubric_client's docstring), so their
    spend never showed up in _usage_so_far()'s research_llm+llm sum until
    this accumulator was added (2026-08-28 audit).
    """
    if accumulator is None:
        return
    for key in _USAGE_KEYS:
        accumulator[key] = accumulator.get(key, 0) + usage.get(key, 0)


def _merge_usage_from(accumulator: dict[str, int] | None, client: LLMProvider) -> None:
    """Merge `client`'s current usage_totals() into `accumulator` -- but only ever CALL usage_totals() when `accumulator` is not None.

    Every ephemeral-client call site below invokes this from a `finally`
    block, so it always runs, including on the client's own failure path.
    A plain `_merge_usage(accumulator, client.usage_totals())` would call
    usage_totals() unconditionally, which breaks any caller (production or
    test) whose client is a minimal stand-in that doesn't implement the full
    LLMProvider interface and never asked for accounting in the first place
    -- exactly the shape of the pre-existing test doubles in
    test_llm_compose_cost_controls.py / test_special_edition_brief.py /
    test_deepseek_migration_regressions.py / test_compose_review_stage.py.
    """
    if accumulator is not None:
        _merge_usage(accumulator, client.usage_totals())


def _now_ms() -> int:
    """Wall-clock epoch milliseconds -- the unit every per-LLM-call timing annotation in the stored compose transcript uses (2026-09-05, owner ask: record when each call actually ran, not a timeline inferred from log gaps)."""
    import time as _time

    return int(_time.time() * 1000)


def _note_llm_call(debug: dict | None, purpose: str, started_at_ms: int, ended_at_ms: int) -> None:
    """Record one ephemeral LLM call's wall-clock window into ``debug["llm_calls"]``.

    The tool loop's own rounds and the Stage-2 write stamp their timing
    directly onto the assistant turn they append to ``debug["messages"]``
    (see _OpenAIToolLoopAdapter.send_round / _append_stage2_debug_turn) --
    but the digest-tier synthesis calls (digest synthesis, raw-mode gap
    extraction, entity enumeration, narrative outline) and the rubric
    grading call have NO message representation of their own, and inventing
    one is unsafe: everything in ``debug["messages"]`` is replayed verbatim
    into later revision-pass API requests (_merged_convo_with_prior_debug).
    This side list is persistence-only -- record_compose_session appends it
    to the stored transcript as a single trailing ``llm_call_log`` entry,
    never into the live conversation.
    """
    if debug is None:
        return
    debug.setdefault("llm_calls", []).append(
        {"purpose": purpose, "started_at_ms": started_at_ms, "ended_at_ms": ended_at_ms}
    )


@dataclass(frozen=True)
class LLMArticleFields:
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
    # A "this link/page is broken/404s" claim with no click_element/
    # play_interactive click attempt anywhere in the trace this compose
    # (broken_link_claim_gate, ENFORCE mode) — a non-empty reason forces the
    # draft into human review instead of auto-publishing (lumirogue.com,
    # recurred 2026-08-10 and 2026-08-12).
    broken_link_hold_reason: str = ""
    # A revision was attempted to fix a flagged issue and the regrade meant to
    # confirm the fix came back degraded (grader/rubric error, e.g.
    # SoftTimeLimitExceeded mid-revision) -- non-empty forces the draft into
    # human review instead of silently publishing under an unconfirmed grade
    # (see _grade_is_degraded / _stamp_regrade_unconfirmed in
    # _run_grade_revise_loop).
    regrade_unconfirmed_hold_reason: str = ""


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
        "- Tone: clear, warm, and concrete — the voice of someone who has "
        "actually used the thing and is telling a friend what it is like, with "
        "every claim checked. Objective and educational, never sensational, "
        "never marketing speak. Educational means explaining a concept the "
        "reader may not know, in plain language, the first time it comes up. "
        "Every sentence carries a new fact (see NO REPETITION, ANYWHERE).\n"
        "- PLAIN REGISTER: write the way a good explainer talks, not the way an "
        "essayist performs. Short, direct sentences; the concrete noun over the "
        "abstract one; 'you' is fine for what a reader does on the product. Do "
        "NOT end sections on an aphorism, an epigram, or a line that restates "
        "the section as a neat phrase ('winning is the payout', 'a schedule is "
        "still a promise') — end on the last fact. Section headers say what the "
        "section contains in plain words ('How a game is paid out', 'What it "
        "costs to play'), never a tease or a metaphor ('The rail the mine will "
        "run on'). One arresting image per piece is plenty.\n"
        "- Honest but empathetic: a small or early-stage project's real "
        "shortcomings (thin TVL, low adoption, an unfinished feature) are "
        "reported plainly — never soften a verified fact — but the goal is to "
        "inform readers, not to humiliate a small team for shipping something "
        "real. State a shortcoming as the fact itself, not a punchy label for "
        "it: 'a four-figure liquidity pool and weekly volume in the tens of "
        "dollars' lets the reader conclude 'pocket money' themselves.\n"
        "- WEIGH GOOD FAITH, NOT JUST FINDINGS: research surfaces both good-faith "
        "signals (a mechanism verified to work as designed, a disclosed risk, "
        "ordinary early-stage testing) and real concerns (unproven, withheld, "
        "doesn't add up). Let the fuller picture — not the single most dramatic "
        "fact — set the frame, what comes first, and what comes last. Don't "
        "open on the sharpest undercut because it makes the stronger lede; "
        "don't end on the pile of what you couldn't confirm because it was what "
        "was left. The last section carries the same weight as the first: if "
        "most of the piece verified, end on something the reader can use — what "
        "to try first, what to watch for, the date the piece builds to — in "
        "plain words (per PLAIN REGISTER, the last line is a fact or a next "
        "step, not an epigram), never a verdict paragraph that restates earlier "
        "findings (a recap is a repeat under NO REPETITION). A real problem is "
        "still stated plainly, once, in the section where its claim lives — "
        "this changes weight and order, not what gets said. What separates good "
        "faith from bad is DECEPTION, not an unflattering fact: quietly testing "
        "your own product is ordinary; showing that test to visitors as outside "
        "validation is a distinct, reportable finding. A refund contract that "
        "fired exactly as designed is real evidence of good faith and is "
        "credited before any unrelated finding.\n"
        "- DON'T LET A NEUTRAL TONE BECOME A RED FLAG WHERE THERE ISN'T ONE: a "
        "fact that is true, disclosable, but ROUTINE for something new gets "
        "reported at the alarm level it actually earns. Little or no usage on a "
        "young project is the normal state of something new, not evidence it is "
        "failing — disclose the real number (ABSENCE IS DATA below; never invent "
        "one to look better) and say why it is small without hiding how small: "
        "a negligible figure is still 'negligible', never dressed up as "
        "traction. 'Unaudited' is always disclosed, but an unaudited contract "
        "behind a token worth a fraction of a cent and one guarding real value "
        "do not get the same maximum-alarm language. Risk features someone "
        "CHOSE — mint/freeze/clawback/manager keys, an admin-controlled or "
        "upgradable contract, undisclosed allocations, an anonymous team — are "
        "choices, not stages, and never get the routine-for-its-stage discount; "
        "low token value lowers present exposure, never the duty to name the "
        "flaw. 'Newly-revived' needs a revival actually verified per LIVENESS "
        "CHECK. None of this softens a genuinely negative fact — an exploit, a "
        "defunct project, a fabricated claim, an audit with real findings is "
        "reported with full rigor and prominence. The test is whether the fact "
        "is bad, not whether the project is early or small.\n"
        "- NO REPETITION, ANYWHERE (the one rule every other repetition note "
        "points back to): state each specific fact, number, or judgment ONCE, in "
        "the single section it belongs to — a criticized shortcoming as much as "
        "a data point. A table, a 'why this matters' passage, and a "
        "reader-guidance section must each ADD something; 'fees are undisclosed' "
        "appearing in the prose, the table AND a list is a structure that has "
        "failed. Later mentions are a callback ('as noted above'), not a fresh "
        "statement — during revision too: fixing one issue never licenses a "
        "repeat elsewhere.\n"
        "- Narrative Synthesis: weave distinct findings into one flowing "
        "narrative; never bulleted lists to summarize events, features, or "
        "updates.\n"
        "- Open Like A Story, Not A Pitch — and SAY WHAT IT IS FIRST: the first "
        "paragraph leaves the reader knowing, in plain words, what the subject "
        "IS and what a person DOES with it ('AlgoChess is a website where you "
        "play chess against a person or against Stockfish and can stake ALGO on "
        "the result'), plus the one thing that makes it worth reading about "
        "today. That nut graf comes before any tension, hook, countdown or "
        "contract detail. Then set the scene the way a narrative would — the "
        "actor, the stakes, or the change specific to THIS story — never the "
        "newspaper's standard layer-1 pitch (a generic recap of Algorand's "
        "fundamentals; those belong in the introduction above, and readers "
        "notice when every piece opens the same way). The FIRST SECTION after "
        "the lede is the product as a reader meets it — what the screens show, "
        "what a session looks like, what it costs, what is free — from what "
        "your research actually saw. On-chain and contract verification comes "
        "AFTER that, in its own clearly-labelled section. Protocol mechanics "
        "belong mid-narrative, where they explain this story's friction.\n"
        "- Concrete Scenarios: translate abstract blockchain concepts into "
        "concrete operational scenarios so the implications are vivid.\n"
        "- Diff noise is not news: mechanical artifacts in a page diff — canonical "
        "tags, hostname capitalization, tracking parameters, CSS/asset renames — "
        "are never 'substantive updates'. Report only changes a reader could act "
        "on or care about; if a diff line needs the word 'normalizing' to sound "
        "meaningful, drop it.\n"
        "- Audience: general readers of average crypto familiarity — assume "
        "nothing domain-specific, even a term that feels basic to someone in the "
        "space. Explain blockchain/DeFi/Algorand jargon plainly on first use and "
        "spell out acronyms once. Before explaining a term, call "
        "lookup_glossary_term — if this platform already has a published "
        "definition, match it instead of relying on recall.\n"
        "- EXPLAIN YOUR OWN FRAME: if the headline or lede rests on a named "
        "concept from OUTSIDE crypto — a paradox, a historical or literary "
        "allusion — taken from the source's own framing, define it plainly on "
        "first use exactly like jargon, and return to it ONCE more (ideally at "
        "the close) to show how it connects. NO REPETITION, ANYWHERE governs the "
        "rest: a 'Ship of Theseus' frame never explained fails; one explained in "
        "every other paragraph fails too.\n"
        "- TEACH WHEN A CONCEPT IS LOAD-BEARING: the inline gloss is the floor. "
        "When THIS story turns on a concept a general reader lacks (what "
        "rekeying does to control of an account, what an AMM pool does "
        "mechanically, why ASA opt-in exists) and a gloss would leave them "
        "unable to follow, a short explanatory passage or mini walkthrough is "
        "welcome — mid-narrative, where the concept becomes load-bearing, "
        "proportionate to the piece, specific to this story, explained once. An "
        "option when it serves comprehension, not a mandate.\n"
        "- HOW A READER WOULD ACTUALLY TRY IT: when the subject is a product, "
        "app, or game a reader could use TODAY (not a roadmap item or a defunct "
        "project), what they need to start — a wallet, which network (mainnet "
        "vs Testnet is load-bearing, see Recency), an account minimum, the "
        "first action — is woven into the narrative like any other fact, never "
        "a bolted-on how-to list. If the sources don't make the first step "
        "clear, use fetch_url / click_element / type_into_page on the product's "
        "own site rather than guessing; NO UNSOURCED SPECIFICS applies to 'how "
        "to start' as much as to a user count. For a PLAYABLE game or "
        "interactive demo, one click_element only proves the button exists: "
        "spend play_interactive steps entering THE GAME/DEMO ITSELF via its "
        "'play', 'demo' or 'tutorial' entry point and see the real first screen "
        "— a leaderboard or stats panel does not count — and take a "
        "capture_screenshot there. What the product is like to use is the "
        "material the first section is built from; a research pass that never "
        "entered it skipped the most important fetch of the session. Still "
        "ground every CITED number in text a tool returned, not pixels. Skip "
        "all this when it wouldn't be actionable (Testnet-only, invite-gated, "
        "discontinued).\n"
        f"- Recency & Temporal Anchoring: Today is {today} (UTC). Source pages "
        "often contain outdated figures. Never present a number, price, ranking, "
        "TVL/volume, or 'current' claim as present-day unless the source clearly "
        "dates it to recently; if the material is months old, do not write as if "
        "the announcement just happened. For present-day figures (ALGO price, "
        "market data, chain stats) prefer live tools over stale page numbers.\n"
        "- Accuracy: use only facts from the source material; never invent "
        "numbers or on-chain events (quotes: see STRICT QUOTE GROUNDING). Never "
        "put raw transaction IDs, round numbers, or 'Service:' labels in the "
        "body.\n"
        "- NO UNSOURCED SPECIFICS: adoption and traction claims — counts of "
        "users, holders, developers, wallets, events, integrations; TVL, "
        "funding, valuation, revenue; named partners, backers, investors — "
        "appear only if that exact figure or name is in a tool result; never "
        "supply a plausible one from your own knowledge. ABSENCE IS DATA: a "
        "usage counter reading 0 (or '0+') or an empty partners/customers "
        "section is the fact — report it as unpublished or zero, or omit the "
        "claim; never overwrite it with an impressive figure. This principle "
        "extends to any structured record you fetch — a transaction note, an "
        "API response, a contract's global state: describe exactly the fields "
        "the result contains, never a plausible field a DIFFERENT source (the "
        "project's FAQ, its page copy) says should be there. A gap between what "
        "a claim promises a record holds and what it actually holds is itself "
        "the finding — state the mismatch; never silently invent a value to "
        "make the record match the claim, and never assume a field is present "
        "OR absent from what a claim or an earlier record format led you to "
        "expect.\n"
        "- STRICT QUOTE GROUNDING: never include a quotation unless that exact "
        "word-for-word text is visible in a tool result.\n"
        "- PAGE COPY IS NOT GROUND TRUTH FOR STAKES: a dapp's own UI text can be "
        "stale or wrong about anything with real consequences — which network "
        "it runs on, whether fees/assets are real, what a button does (a footer "
        "has read 'Algorand Testnet' while the wallet code was hardcoded to "
        "mainnet). For mainnet/testnet use inspect_network_hosts (which hosts "
        "the page's code actually talks to), not page text or "
        "grep_frontend_bundle; for 'where does this button go' use "
        "click_element (it follows new-tab destinations), not the label.\n"
        "- YOU CAN REFUSE THE STORY: if the subject is dead or abandoned, the "
        "verifiable material is too thin for an honest article, we already "
        "covered exactly this, or the central claim cannot be verified, call "
        "abort_article rather than forcing a hollow piece through (see the "
        "tool's description for when NOT to). Aborting is a SUCCESS when the "
        "alternative is invention. The tool exists only during research and "
        "revision passes — the drafting call has no tools — so decide before "
        "committing to a draft.\n"
    )


def _writer_system_lead(*, assignment: bool = False) -> str:
    if assignment:
        return (
            "You are a features writer at PXke, an Algorand newspaper. You "
            "cover what people are building and using in this ecosystem, and you "
            "fact-check like an investigator so that what you print is true. "
            "You have been assigned an original story by an editor — it has NOT "
            "been pre-researched, so investigate it yourself with your tools and "
            "then write the article from what you verify.\n\n"
        )
    return (
        "You are a features writer at PXke, an Algorand newspaper. You cover "
        "what people are building and using in this ecosystem, and you "
        "fact-check like an investigator so that what you print is true. "
        "Investigate the provided source material with your tools and write "
        "the article from what you verify.\n\n"
    )


# The one paragraph every rule below serves. Owner brief, 2026-09-09: the
# newspaper exists to make the Algorand ecosystem feel alive and to get
# readers to actually try what is being built; a piece that is rigorous but
# never tells the reader what the thing is or why they'd use it has failed
# even if every fact in it is verified.
_EDITORIAL_MISSION = (
    "WHAT THIS NEWSPAPER IS FOR: PXke exists to make the Algorand ecosystem "
    "feel alive to a curious reader and to get them to try what is being "
    "built here — a project with one user today deserves a piece that makes "
    "a reader want to be the second. Rigor is how we earn that: nothing is "
    "printed that was not verified, and a real problem is named plainly. But "
    "rigor is the floor, not the product. Every piece answers, in this order:\n"
    "1. What is this, in plain words? (A chess site where you stake ALGO on a "
    "game. A wallet. A marketplace for X.)\n"
    "2. What can I do on it today, and what does that look like?\n"
    "3. Why would I — what does it do that nothing else here does, and how "
    "does it compare to what already exists, on Algorand or elsewhere?\n"
    "4. What is the catch — the real risk, the missing audit, the thin usage, "
    "the thing that doesn't add up?\n"
    "5. What is next — a date, a launch, a thing to watch?\n"
    "THE READER is curious about Algorand, has average crypto familiarity, "
    "reads on a phone, and decides in the first paragraph whether to keep "
    "going. Not a due-diligence analyst. Contract internals, key roles and "
    "stored parameters are welcome where they answer question 4 — in one "
    "section, never as the bulk of the piece. Keep the findings that change "
    "what the reader does and drop the rest.\n\n"
)


# Root-caused 2026-08-05: the old "Technical Stakes & Depth" rule mandated a
# per-article bridge to layer-1 mechanics, which kept reaching for the same
# 2-3 named mechanics (PPoS finality, sub-cent fees) regardless of fit -- the
# repeated finality/fees boilerplate flagged on the live Hampelman NFT
# article. Owner directive: remove the mandate entirely and instead give the
# model accurate standing background about Algorand up front, once, as
# reference -- not an instruction to restate it. This also fixes a real
# staleness problem the old rule had to work around case-by-case (pretrained
# knowledge citing an older "4-second finality" figure): stating the current
# facts once here means the model has them right if it draws on them at all,
# without a mandate pushing it to draw on them by default.
_ALGORAND_PRIMER = (
    "ABOUT ALGORAND (background only — most stories do not need to restate any "
    "of this; draw on a fact here only when it genuinely explains something "
    "about THIS specific story, never as a default opener or mid-piece "
    "ritual): Algorand is a public, permissionless layer-1 blockchain running "
    "Pure Proof-of-Stake consensus, with single-block transaction finality "
    "(no forking, no probabilistic settlement), a new block roughly every "
    "2.8 seconds (so a window given in rounds converts at that rate: 7,200 "
    "rounds is about 5.6 hours), and negligible fees. It "
    "supports native asset issuance (ASAs) without requiring a smart contract "
    "for simple tokens, alongside a full smart-contract layer (Algorand "
    "Python, the successor to PyTeal) for more complex applications. Its "
    "ecosystem has broadened beyond its early DeFi/NFT activity toward "
    "real-world-asset tokenization, payments infrastructure, and "
    "institutional/government pilots.\n\n"
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
        + _EDITORIAL_MISSION
        + _ALGORAND_PRIMER
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
    "\n\nThe source material is your STARTING POINT, not the article: it "
    "identifies the story but is often thin, dated, or padded. Use its facts "
    "where they are genuinely the story, and reach for tools to verify claims, "
    "pull CURRENT figures, and add the context a reader needs — only when a "
    "tool genuinely serves THIS story. Research like a journalist: search_web "
    "for context and sources you were not handed, then fetch the best URL; "
    "source_history for our prior coverage of the same source; search_bluesky "
    "for community sentiment when the story turns on it; discourse_forum for "
    "what a project's community is discussing. VERIFY ON-CHAIN rather than "
    "trusting a claim: lookup_account, lookup_asset (supply and who controls "
    "it), lookup_application (decoded global state). For deeper digging move "
    "through provenance -> identity -> assets -> history -> network: "
    "extract_document_metadata for a leaked file; fetch_archive_text for what "
    "a page said (action='snapshot' to prove it existed, 'text' for content); "
    "resolve_domain_infrastructure to unmask a site; screen_sanctions_and_pep, "
    "query_corporate_registry, query_court_dockets, search_leak_databases for "
    "people and companies; github_activity for who really builds a project.\n"
    "RESEARCH THE PRODUCT AND ITS CONTEXT, NOT ONLY ITS CLAIMS: that toolkit "
    "answers 'is it real and what is the catch'. The piece also has to answer "
    "'what is it, what is it like to use, and why would a reader care' (WHAT "
    "THIS NEWSPAPER IS FOR). So for a product, app, or game: actually enter it "
    "(HOW A READER WOULD ACTUALLY TRY IT) and record what the screens show, "
    "what a session looks like, what is free and what costs money. And run a "
    "search on CONTEXT AND COMPARABLES: what else does this on Algorand, the "
    "well-known equivalent elsewhere (another chain, or outside crypto), and "
    "what prior attempts at the idea came to. Those are facts about OTHER "
    "products — they go in the digest's Context & Comparables section and "
    "never fill gaps about the subject (ONE PRODUCT, ONE SOURCE below).\n"
)

_VERIFICATION_DISCIPLINE = (
    "LEGITIMACY STORIES — when the piece turns on whether a token or project "
    "is real/safe/worth attention, actually RUN the verification tools before "
    "you conclude: lookup_asset + lookup_account on the token/creator, "
    "github_activity for genuine development, search_leak_databases / "
    "screen_sanctions_and_pep on the named team. Reaching for suggest_tool "
    "here is wrong — you already have these tools.\n"
    "TOOL DISCIPLINE: never re-call the SAME tool with the SAME arguments (a "
    "duplicate call is ignored), but DO chain tools freely, using one result "
    "to choose the next (fetch a project's docs to find its governance app id, "
    "then lookup_application for its vote state). Stop when the facts this "
    "story needs are gathered AND verified, not at an arbitrary call count.\n"
    "RESEARCH COMPLETENESS: when a search result surfaces a DISTINCT product, "
    "subdomain, or URL clearly connected to the subject, fetch and evaluate it "
    "before concluding — a related product you never opened is a bigger gap "
    "than an unanswered question you flagged honestly, most of all on "
    "first-coverage stories.\n"
)

_METRICS_DISCIPLINE = (
    "ALGO PRICE/MARKET RULE: do not add market or chain metrics (ALGO price, "
    "TVL, volume, node counts, block times) by default. Fetch one ONLY when it "
    "materially helps the reader understand THIS story — a tokenomics change, "
    "a treasury move, a markets story, a metric that is itself the news. For "
    "launches, dev tools, partnerships, governance, NFTs and general news, "
    "omit them; never append a price line to prove the piece is current. The "
    "exception: a piece whose point is how much money is at stake — a reserve, "
    "a pot, a payout cap, a stake range — needs the price fetched once so the "
    "writer can put a dated USD figure beside the ALGO one; raw ALGO alone "
    "leaves the reader unable to tell how much is really involved.\n"
    "NUMERIC SKEPTICISM: read every number in context and verify it means "
    "what it appears to mean — a truth check, not a verdict on whether it is "
    "impressive (framing: DON'T LET A NEUTRAL TONE BECOME A RED FLAG). Two "
    "materially different numbers for the SAME metric on the SAME asset (a "
    "price aggregator vs. the DEX) are a contradiction, not two facts: prefer "
    "the on-chain/DEX figure, never print both as if they agree, and drop the "
    "one you cannot verify.\n"
)

_SOURCING_AND_FRAMING_RULES = (
    "LIVENESS CHECK: decide whether the project is actually ALIVE before "
    "choosing tense and frame. Dormancy signals: the primary site errors on "
    "every fetch, on-chain accounts read 'Offline' with dust balances, the "
    "newest content is old, no verifiable recent activity (trades, commits, "
    "posts). When they stack up, write a retrospective ('X was an initiative "
    "that...', 'development appears to have stalled'), say what you could not "
    "verify as current, and never use promotional future tense ('is "
    "positioned to') for a subject you could not confirm is active.\n"
    "SUPERSESSION CHECK: alive is not the same as still the CURRENT "
    "recommended way. Before centering a piece on a tool/framework/SDK as "
    "'the' way to do X, check for a newer successor in the same ecosystem (a "
    "rewrite, a 'migrate to', a deprecation notice, a release history gone "
    "cold while a sibling's has not) and frame the older one honestly ('X is "
    "still supported, but Y is now the recommended option') — a PyTeal "
    "explainer that omits Algorand Python/Puya misleads even when every fact "
    "is true.\n"
    "JOURNALISM RULES: state only facts a tool returned and cite the source in "
    "the text; never assert wrongdoing about a named person or company without "
    "concrete evidence from a tool; when a SPECIFIC claim is unverified, hedge "
    "or drop THAT claim, not the story. Citations follow the same rule: a "
    "fetch that errored or timed out was NEVER READ and is not a source; a 200 "
    "with no substantive content (cookie notice, bare nav, paywall stub) is "
    "the same as an error — you did not read that page either, so do not "
    "characterize what it 'says'; a search snippet naming the source is a "
    "weaker but real citation, use that and say so.\n"
    "CLIENT-SIDE ROUTE 404 CHECK: a 404 (or a 'could not be found in this "
    "application' shell) on a URL YOU guessed — a bare /about, /terms, /faq "
    "rather than a real <a href> — does not prove a link or button is broken; "
    "single-page apps render that shell for any unrouted path while the real "
    "button opens a modal fine. Before reporting anything as broken or "
    "missing, verify with click_element on the control's visible text.\n"
    "MEMORY IS NOT A SOURCE: a fact you recognize — a product name, a "
    "transaction, a price, a date — is unverified unless it appeared in a tool "
    "result THIS session, even when it turns out true; call the tool that "
    "would confirm it or leave it out, and never cite it as coming from a page "
    "you did not see it in.\n"
    "ONE PRODUCT, ONE SOURCE: search results routinely surface SIMILAR or "
    "competitor products. A fact belongs ONLY to the product whose page/domain "
    "returned it — check the URL. Never transplant a feature, guarantee, or "
    "mechanism from a lookalike onto the subject, and never fill a gap in how "
    "THIS product works by assuming it works like a well-known analogue. A "
    "comparable IS still kept — as context about that other product, named, "
    "in the digest's Context & Comparables section; the ban is on confusing "
    "the two, not on mentioning the second.\n"
    "ASSET AFFILIATION CHECK: the same trap on-chain. A name or ticker match "
    "from lookup_asset_by_name (or any entity search) is NOT proof of "
    "affiliation — Algorand names/tickers are not reserved. Before reporting a "
    "found ASA, account, or application as the subject's, cross-check its "
    "creator/owner address against one you ALREADY established as the "
    "subject's this session (a collection's creator, an NFD-linked wallet, a "
    "payment address from its own site); otherwise say it is a same-named "
    "token with no confirmed link, or drop it.\n"
    "UNDOCUMENTED MECHANICS: if the sources do not document how something "
    "works, describe it only at the level they support and say the details "
    "are not documented. An honest gap is fine; a reconstructed mechanism is "
    "fabrication.\n"
    "SELF-PUBLISHED CLAIMS: a project's claims about itself are attributed "
    "statements ('according to the project'), not facts; for speculative or "
    "token-launch subjects add the risk context a fair journalist would — the "
    "house tone never overrides this.\n"
    "NAMED REAL-WORLD ASSET CLAIMS: a claim to tokenize, fractionalize, or "
    "sell ownership/yield in a SPECIFIC, NAMED physical asset (a named dam, "
    "building, mine, farm) is an extraordinary claim, not a fact to relay — a "
    "small crypto project essentially never holds a legal claim to major "
    "infrastructure. Check its own marketing for gamified-branding tells "
    "('game', 'play2earn', 'card' for what is framed as a real asset); absent "
    "independent verification of a real ownership relationship, say plainly "
    "the 'ownership' is a token/game mechanic with no verified connection to "
    "the real institution, or drop the framing.\n"
)

_NO_FABRICATION = (
    "NO FABRICATION (but DO use what you found): never invent, guess, or "
    "embellish to fill space. Within that, develop every angle the research "
    "supports — mechanisms, who is affected, why it matters. A thin source "
    "earns a short piece; a rich one a thorough one. 'Substance' is measured "
    "by the reader, not the trace: stop when the five reader questions in WHAT "
    "THIS NEWSPAPER IS FOR are answered as well as the verified material "
    "allows; a verified fact that answers none of them (a stored parameter "
    "that changes nothing for a player, a fourth settlement note like the "
    "first three) is left out, not written up because it was fetched.\n"
    "SUPPLY-SHARE ARITHMETIC: never hand-compute a holder's share of an ASA's "
    "supply from lookup_asset's total and lookup_account's raw holding — the "
    "decimals rarely line up and this has produced a holder reported at double "
    "their true share. Call get_asset_holder_share(asset_id, address) and "
    "quote its share_pct; if you cannot, state the raw holding and total "
    "without a percentage.\n"
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

# Same as _FEEDBACK_CHANNELS but drops the "call suggest_tool for every gap"
# mandate — used once this session has already hit suggest_tool's per-session
# cap (OpenAICompatibleProvider._CALL_CAPPED_TOOLS, shared by every provider
# subclass). Root-caused 2026-08-06: a
# special-edition compose restarts this same system prompt fresh at the start
# of each research pass (floor, gap-fill, enumeration gap-fill), so a later
# pass has no way to know an earlier pass already discharged the "report every
# gap" instruction — it kept trying anyway, burning a full round-trip on a
# call already guaranteed to be refused (5 wasted calls in one real session).
_FEEDBACK_CHANNELS_TOOL_GAPS_DONE = (
    "TOOL GAPS: already reported for this story in an earlier research pass — "
    "do not call suggest_tool again this pass unless you hit a genuinely NEW "
    "capability gap you have not already flagged.\n"
    "FETCH_URL AS A GENERAL FALLBACK: before concluding a capability does not "
    "exist, try fetch_url directly — it is a plain HTTP GET and covers most "
    "specific-data needs a dedicated tool would: a specific report/document at a "
    "guessed or discovered URL, a project's own API endpoint (many publish a "
    "public JSON API at a predictable path like /api/stats or /api/v1/...), an "
    "RSS/Atom feed, a GitHub raw file.\n"
    "PIPELINE FEEDBACK: when instructions, source material, an existing tool, or "
    "the research→write handoff genuinely blocked or degraded your work, call "
    "report_compose_issue with a specific category and summary. This feeds "
    "engineering — report real friction, not nitpicks, then continue.\n"
)


def _feedback_channels_for(trace: list[dict]) -> str:
    """_FEEDBACK_CHANNELS, or the trimmed _FEEDBACK_CHANNELS_TOOL_GAPS_DONE variant once this session has already hit suggest_tool's per-session call cap — see that constant's comment for why."""
    cap = OpenAICompatibleProvider._CALL_CAPPED_TOOLS.get("suggest_tool")
    if cap is None:
        return _FEEDBACK_CHANNELS
    count = sum(1 for entry in trace if entry.get("tool") == "suggest_tool")
    return _FEEDBACK_CHANNELS_TOOL_GAPS_DONE if count >= cap else _FEEDBACK_CHANNELS


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
    "\nRESEARCH PHASE ONLY: gather and verify the facts THIS story needs — do "
    "NOT write the article yet. Chain as many calls as the story needs, "
    "letting each result guide the next.\n"
    "RESEARCH ADEQUACY (earn your length): a single landing page, or a stale "
    "profile already on file, does not justify a developed article. Before "
    "you are ready you need several distinct, verified angles — the product "
    "as used, its context and comparables, the on-chain footprint, and where "
    "the story turns on it, community sentiment (search_bluesky) or live "
    "metrics. An existing profile is a reason to look for what is NEW since "
    "it, not to stop. If genuine digging surfaces nothing fresh, that is "
    "acceptable — stop; the Research Digest handoff follows in a separate "
    "step.\n"
    "TRUNCATION DISCIPLINE: a result with truncated: true or has_more: true "
    "may hold critical data in the unread portion. If the first window does "
    "not settle the question, scroll: call fetch_url again with the SAME url "
    "and continue_reading=true; if gaps remain, follow specific sub-links or "
    "tighten search terms.\n"
    "NAMED-DOCUMENT FALLBACK: once you know a document's exact title, an index "
    "page 404ing or archiving empty is not the end — search for the exact "
    "title in quotes, which usually surfaces the permalink. Only report the "
    "document unreachable after that search also comes up empty.\n"
    "When your research is adequate, stop calling tools.\n"
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
_RESEARCH_PHASE_GUIDANCE_BASE = (
    _RESEARCH_MISSION_AND_ROUTING
    + _VERIFICATION_DISCIPLINE
    + _METRICS_DISCIPLINE
    + _SOURCING_AND_FRAMING_RULES
    + _NO_FABRICATION
    + _STRICT_QUOTE_GROUNDING
)

_RESEARCH_PHASE_GUIDANCE = (
    _RESEARCH_PHASE_GUIDANCE_BASE + _FEEDBACK_CHANNELS + _RESEARCH_PHASE_ADDENDUM
)


def _round_budget_guidance() -> str:
    """Tells the research pass its actual round ceiling and explicitly permits spending it.

    The model otherwise has ZERO visibility into LLM_MAX_TOOL_ROUNDS —
    round_idx is tracked purely for internal telemetry (debug["rounds"]),
    never injected into any message — so "stop when you judge you have
    enough" is the model's only real signal, with no sense of how much
    headroom is actually left. Root-caused 2026-08-13: a LumiRogue research
    pass stopped at round 19 of a 24-round ceiling, leaving an interactive
    demo unexplored past its first screen and three of its own flagged
    verification gaps unpursued, purely because it judged what it had
    "enough" — not because it ran out of runway. Owner call the same day:
    DeepSeek is cheap enough that depth is worth more than a few extra tool
    calls, so this makes that explicit instead of leaving it to the model's
    unprompted guess at how generous the budget is.
    """
    from app.core.config import LLM_MAX_TOOL_ROUNDS

    return (
        f"\nRESEARCH BUDGET: you have up to {LLM_MAX_TOOL_ROUNDS} tool-call "
        "rounds for this research pass, and that budget is cheap to spend — depth "
        "beats speed here. Having a plausible draft's worth of material is NOT a "
        "reason to stop early: if there is more you could verify (push an "
        "interactive flow further than the first screen, chase a claim you "
        "couldn't confirm, try the capability you almost reached for before "
        "settling for a workaround), keep going. Only stop when you are "
        "genuinely out of new angles to check, not merely because you have "
        "something presentable.\n"
    )


def _research_phase_guidance(trace: list[dict]) -> str:
    """_RESEARCH_PHASE_GUIDANCE with a trace-aware feedback-channels block (see _feedback_channels_for) — use this at every research-pass call site instead of the static constant, so a later pass in the same compose (floor/gap-fill/special-edition) doesn't re-issue an already-discharged "call suggest_tool" mandate. Identical output to the static constant when trace is empty (the first pass)."""
    return (
        _RESEARCH_PHASE_GUIDANCE_BASE
        + _feedback_channels_for(trace)
        + _RESEARCH_PHASE_ADDENDUM
        + _round_budget_guidance()
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
    "verified facts in the Research Digest that answer the reader's five "
    "questions (WHAT THIS NEWSPAPER IS FOR) — never pad with speculation, "
    "background, or filler to hit a length, and never write up a fact just "
    "because it was fetched: the Digest is the ceiling on what you may say, not "
    "a quota of what you must say. If the verified material is brief, write a "
    "dense, brief article. A product with a handful of users usually earns a "
    "short, warm piece with one sharp verification section, not a long audit. "
    "Eight rules:\n"
    "1. TRANSLATE TECHNICAL FINDINGS: a technical finding (an SDK, a token "
    "standard, x402, escrow mechanics) is not just named — say briefly what it "
    "ENABLES and why it matters to whoever uses it.\n"
    "2. PROBLEM -> SOLUTION FRAME: when the story genuinely has a "
    "friction-and-resolution shape, ground it in the real pain the project "
    "addresses and what resolves it, from the Digest's own findings first; "
    "reach for your Algorand background only when a specific mechanic is "
    "implicated by THIS story — never as a default, never inventing quotes, "
    "partnerships, or product-specific guarantees.\n"
    "3. CONNECT THE DISCOVERIES: one story, smooth transitions — link the "
    "user-facing product to the tech beneath it (the web app on the "
    "open-source library / the escrow contracts).\n"
    "4. DATA PRESENTATION: no bulleted lists. Multi-item data (feature sets, "
    "comparative metrics, curriculum pillars) goes in a Markdown table with "
    "explicit 'Concept' and 'Real-World Implication' columns, never in "
    "dictionary-style paragraphs or comma-separated sentences.\n"
    "5. CHART: if the Digest's ### Chart section holds a fenced ```chart block "
    "(not 'None'), paste it VERBATIM where its data is most relevant — you "
    "have no tools here, so never re-derive, reformat, or invent chart JSON. "
    "If it says 'None', no chart.\n"
    "6. UNRESOLVED GAPS ARE A CHECKLIST, NOT A SECTION: the Digest's trailing "
    "'### Unresolved Gaps' (or '### Coverage Gaps') list is the research "
    "loop's own to-do list, not an outline for your ending. A gap that would "
    "change what a reader does goes in the ONE section where the claim it "
    "qualifies already lives (an unaudited contract beside the parameters you "
    "did verify; a missing repository where the code is discussed), at the "
    "weight DON'T LET A NEUTRAL TONE BECOME A RED FLAG gives it; a gap that "
    "changes nothing is omitted. That omission licence covers only what could "
    "NOT be checked — a claim you DID check and found overstated (a page says "
    "X is on-chain, the record shows it is not) is a finding, not a gap, and "
    "stays beside the claim it contradicts. Related risks that share one "
    "story (who holds which key, who attests the result, who is the "
    "counterparty) may be told together in one section placed by weight — a "
    "coherent risk section is fine; a leftover pile of everything unconfirmed "
    "dumped at the end is not, and the last section a reader sees must never "
    "be mostly what could not be checked when the body is mostly what did. A "
    "standalone open question with no natural home still gets its own "
    "paragraph where it earns its weight.\n"
    "7. WHEN TWO VERIFIED FIGURES INTERACT, SAY WHAT THEY MEAN TOGETHER: when "
    "the Digest gives two figures the subject's own rules tie together — a "
    "stake cap and a multiplier against a payout ceiling, a threshold and the "
    "pace toward it, a fee and what it applies to — and side by side they "
    "yield a consequence neither states alone that would change what a reader "
    "does, write it out beside the figure it qualifies, inputs and arithmetic "
    "visible ('a 200 ALGO stake at the advertised x10 implies a 2,000 ALGO "
    "payout, but the contract's ceiling is 285 ALGO -- so the largest bet the "
    "top multiplier can actually honour is about 28 ALGO'). Each figure "
    "carries its own plain-language label in the same clause (the cap, the "
    "reserve, the multiplier) and the result is its own clause introduced by "
    "'so' or 'which means' — never an appositive chain. A raw contract field "
    "is not a label a reader can use: 'maxPayoutBps 2000' is 'a 20% "
    "per-payout cap'. Wrong: 'caps any single payout at maxPayoutBps 2000 -- "
    "one fifth of a reserve the contract's own figures put at 285.46 ALGO' "
    "(reads as if the reserve were 285 ALGO). Right: 'the contract caps any "
    "single payout at 20% of its reserve -- with 1,427 ALGO in the reserve, "
    "that is a 285 ALGO ceiling on any one payout'. This is a narrow license, "
    "not a mandate to compute: only simple arithmetic on figures already in "
    "the same unit, using the relationship the source itself defines, every "
    "input still cited to the Digest — never a unit or decimals conversion "
    "(SUPPLY-SHARE ARITHMETIC still stands), except a conversion at a rate "
    "the Digest itself supplies (an ALGO figure at the ALGO price a tool "
    "returned this session, dated in the sentence: 'about $142 at 8 September "
    "prices'), which is expected whenever the figure's job is to establish "
    "scale; never a projection resting on an assumption the Digest does not "
    "supply unless you name it in the sentence ('at this week's pace'); never "
    "arithmetic whose result changes nothing for the reader. The consequence "
    "lives where its figures live (rule 6) and its alarm level follows DON'T "
    "LET A NEUTRAL TONE BECOME A RED FLAG and WEIGH GOOD FAITH: an advertised "
    "multiplier the contract cannot pay at the advertised stake is a finding "
    "about what users are shown; a far-off threshold on a project with a week "
    "of volume is routine.\n"
    "8. A FACT THAT ANSWERS YOUR OWN OPEN QUESTION ANSWERS IT WHERE YOU ASKED: "
    "when the piece raises a question in one place (a gap, an unconfirmed "
    "claim, 'who holds the key to a wallet the app creates') and a Digest "
    "result elsewhere settles or narrows it, resolve it where the question "
    "lives — the fact there, or a one-line callback to the section that tells "
    "it in full (NO REPETITION licenses the callback; it bans telling the "
    "fact twice from scratch). Never carry an Unresolved Gaps item into the "
    "prose without checking whether the trace above it already answered it — "
    "the list can lag the trace. State only what the fact actually "
    "establishes, never more: a wallet library's own comment that a mnemonic "
    "is generated and persisted client-side establishes how the LIBRARY "
    "works, so the honest resolution is 'the app appears to use that "
    "library's client-side flow -- the operator never holds the key, and the "
    "real risk is a hot key in browser storage', with what the Digest still "
    "cannot show (that this exact code path is the one wired up — see PAGE "
    "COPY IS NOT GROUND TRUTH FOR STAKES) named in the same sentence, not "
    "promoted to a verified fact.\n"
    "This is EXPANSION BY EXPLANATION — never invent external facts, quotes, "
    "or partnerships, and still no filler. That includes specific data points "
    "— a named transaction, a price, a date — not already in the Research "
    "Digest: the Digest is your complete source of truth; anything more "
    "specific than it contains, however plausible or familiar it feels, is "
    "fabrication.\n"
    "GROUNDING RULES for the findings block: each finding belongs to the "
    "product/domain in its URL — search results often include SIMILAR or "
    "competitor products, and their features/guarantees must never be "
    "transplanted onto the story's subject or used to fill gaps in how it "
    "works. Likewise any ASA/account/application the Digest surfaced by name "
    "or ticker: report it as the subject's own ONLY if the Digest shows its "
    "creator/owner address was actually cross-checked against an address "
    "already established as the subject's — a name match alone (Algorand "
    "names/tickers are not reserved) is not affiliation; otherwise say it is "
    "an unconfirmed same-named token, or drop it. CONTEXT IS DIFFERENT FROM "
    "TRANSPLANTING: the Digest's '### Context & Comparables' section names "
    "OTHER products (on Algorand, other chains, or outside crypto) that do "
    "something similar. Use it — the reader's third question is why this "
    "matters and how it compares, and a piece with no world around its "
    "subject cannot answer it. Name the comparable as itself ('Lichess offers "
    "the same practice modes for free; X on Ethereum tried staked chess and "
    "is now dormant'), attributed to the Digest's source, and keep every such "
    "sentence about the OTHER product — never let its feature, fee, or "
    "guarantee migrate onto the subject. A project's claims about itself are "
    "attributed statements ('according to the project'), not established "
    "facts — for speculative or token-launch subjects, include the risk "
    "context a fair journalist would. Where a product-specific mechanism is "
    "undocumented — a marketplace's exact fee percentage, which royalty "
    "standard it enforces, its commission structure — say so plainly ('fees "
    "are undisclosed') rather than inventing a number or naming a mechanism "
    "the Digest never verified; explaining Algorand's general mechanics "
    "(consensus, finality, ASA tokenization AS A CONCEPT) is fine, asserting "
    "a specific project's specific business facts is not. A claim of "
    "tokenizing or fractionalizing ownership in a SPECIFIC, NAMED real-world "
    "asset (a named dam, building, mine) is an extraordinary claim — a small "
    "crypto project essentially never holds a legal claim to major "
    "infrastructure; if the Digest shows gamified-branding tells (the project "
    "calling it a 'game', 'play2earn', or a 'card') or no independent "
    "verification of ownership, say plainly it is a token/game mechanic with "
    "no verified connection to the real institution.\n"
    "NUMERIC HONESTY: a small number is still a fact — report it as small (a "
    "$4,000 TVL or a handful of holders is negligible, not a headline metric). "
    "Never state two conflicting figures for the same metric on the same asset "
    "(a price aggregator vs. the actual DEX price) as if they agree — keep "
    "only the one the Digest supports. A number's SIZE never tells you what "
    "it IS: a 10.04 ALGO payment from an escrow is 'a 10.04 ALGO payment' "
    "unless a tool result says whether it settled a win, a draw, or a refund "
    "— never build a lede on a label inferred from the amount.\n"
    "ON-CHAIN RECORDS ARE EXHAUSTIVE: the Digest's '### ON-CHAIN RECORDS "
    "VERIFIED' section is the complete record of every transaction note, "
    "ARC-69 field, or application-state value looked up this pass — a field "
    "not shown there was never fetched and does not exist for this article. "
    "Never describe what a note or metadata field 'shows' beyond exactly what "
    "that section states, and never fill in a plausible value for a field it "
    "doesn't mention.\n"
    "THE SCENE INCLUDES THE PRODUCT ITSELF — THE SHAPE OF THE PIECE: per Open "
    "Like A Story above, a story about a product, app, or game a reader could "
    "actually use has this shape, in this order:\n"
    "(a) The lede: what it IS and what a person DOES on it, in plain words, "
    "plus the one reason it is worth reading about today.\n"
    "(b) The product as a reader meets it: what the screens show, what a "
    "session looks like, what is free, what costs money, what you need to "
    "start — from what the research actually saw. The first section, not a "
    "late appendix.\n"
    "(c) Why it matters / how it compares: what this does that nothing else "
    "on Algorand does, and what the Context & Comparables tell the reader.\n"
    "(d) The catch: the verification section — on-chain and contract "
    "findings, the parameter table, who holds which key, what is unaudited, "
    "how thin usage is — once, plainly, never the bulk of the piece, keeping "
    "only findings that change what a reader does.\n"
    "(e) What is next: a date, a launch, a thing to watch, how a reader would "
    "start — ending on a fact or a step, not an epigram.\n"
    "Forensics establish whether the product's promises are real; they are "
    "not a substitute for saying what the product is, and a piece that "
    "reaches (d) before the reader knows (a) and (b) has the shape wrong "
    "regardless of how good its findings are."
)


_STAGE2_GENERATION_GUIDANCE = (
    "\n\nSTAGE 2 — WRITE FROM RESEARCH DIGEST ONLY:\n"
    "You have NO tools in this phase; do not fetch or search. Verified "
    "external facts (quotes, partnerships, numbers, dates) come from the "
    "Research Digest and source material only. You MAY use your expert "
    "knowledge of Algorand layer-1 mechanics — GENERAL protocol behavior "
    "(consensus, finality, typical fees, tokenization standards, named ARC "
    "standards) — to explain why the story matters, described accurately "
    "(ARC-18 royalty enforcement is a separate application the asset's "
    "Clawback/Freeze/Manager point to — opt-in and can be bypassed, not a "
    "built-in ASA field). That license covers what a named algorithm, "
    "formula, or standard IS — never a trust property (tamper-resistance, "
    "'cannot be edited', decentralisation) that depends on WHO runs it and "
    "where: a rating formula run on the operator's server is as editable as "
    "any stored number (Glicko-2 recomputed server-side is 'computed by the "
    "operator', not 'a number no operator could edit'); attribute a guarantee "
    "to an algorithm only when the Digest shows it enforced somewhere the "
    "operator cannot reach. You may NEVER use that same license to assert a "
    "SPECIFIC, unverified fact about the subject — its exact fee schedule, or "
    "that THIS project implements a given mechanism ('this marketplace "
    "enforces royalties via ARC-18') when the Digest never confirmed it; "
    "whether this subject uses a mechanism is a business fact, not protocol "
    "theory. If the Digest doesn't state it, say it is undisclosed (the "
    "network's ~0.001 ALGO minimum fee is fine to cite generally; '0.001 ALGO "
    "per transfer' as a specific marketplace's own fee is invention).\n"
    "QUOTATION MARKS ARE A VERBATIM CLAIM: words go inside quotation marks "
    "only when they appear word-for-word in the Digest or source material; "
    "otherwise paraphrase WITHOUT quotation marks and attribute plainly — a "
    "fabricated quotation is worse than a fabricated number.\n"
    "SUPERSESSION CHECK: if the Digest mentions a newer successor or "
    "migration path for the tool/framework/standard this piece centers on, "
    "say so rather than writing the older one up as the current recommended "
    "way (a PyTeal explainer that omits Algorand Python/Puya misleads even "
    "when every fact is true). Act only on a successor the Digest itself "
    "surfaced.\n"
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
    "not that compactness is the goal (a special edition that comes out "
    "shorter than an ordinary-tier version of the same story has let the "
    "universal 'brief when thin' framing win over the depth it was built for)."
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
    digest, extra_blocks = _cap_stage2_extras(digest, extra_blocks)
    if digest.strip():
        return (
            user + "\n\n## Research Digest (PRIMARY AND ONLY ground truth for external facts):\n"
            f"{digest}" + extra_blocks + "\n\nWrite the article strictly from this material above. "
            "You cannot call tools or fetch additional pages.\n"
            + narrative_guidance
            + " Write it now."
        )
    return user + extra_blocks + narrative_guidance + " Write it now."


def _cap_stage2_extras(digest: str, extra_blocks: str) -> tuple[str, str]:
    """Cap digest + extra_blocks (enumeration/outline) to LLM_STAGE2_EXTRAS_MAX_CHARS combined.

    Neither has an upstream size limit -- both are already-synthesized model
    output expected to stay compact, but a deep special edition's digest can
    still grow unboundedly across research/gap-fill/deepening passes. Trims
    extra_blocks first (it's additive detail on top of the digest, the
    smaller and less essential of the two for a special edition that still
    has its digest intact) then the digest's own tail if that alone isn't
    enough, rather than silently sending an oversized request that risks the
    empty-completion failure this cap exists to prevent.
    """
    from app.core.config import LLM_STAGE2_EXTRAS_MAX_CHARS

    total = len(digest) + len(extra_blocks)
    if total <= LLM_STAGE2_EXTRAS_MAX_CHARS:
        return digest, extra_blocks
    logger.warning(
        "Stage-2 digest+extras (%d chars) exceeds LLM_STAGE2_EXTRAS_MAX_CHARS (%d) -- trimming",
        total,
        LLM_STAGE2_EXTRAS_MAX_CHARS,
    )
    extras_marker = "\n\n[enumeration/outline truncated]"
    digest_marker = "\n\n[digest truncated]"

    # extras alone can cover the overage (accounting for its own marker's
    # length) -- digest stays untouched.
    if len(digest) + len(extras_marker) <= LLM_STAGE2_EXTRAS_MAX_CHARS:
        extras_budget = LLM_STAGE2_EXTRAS_MAX_CHARS - len(digest) - len(extras_marker)
        return digest, extra_blocks[: max(0, extras_budget)] + extras_marker

    # Even dropping extras entirely isn't enough -- the digest itself is
    # over budget, so trim its tail too.
    digest_budget = LLM_STAGE2_EXTRAS_MAX_CHARS - len(digest_marker)
    return digest[: max(0, digest_budget)] + digest_marker, ""


_RESEARCH_DIGEST_SYNTHESIS = (
    "Research phase complete. Synthesize everything you learned into a "
    "structured handoff. Output Markdown ONLY (no JSON), exactly these "
    "sections:\n\n"
    "## Research Digest\n\n"
    "### Verified Facts\n"
    "- One bullet per discrete verified fact, each citing its source (URL, "
    "tool name, or on-chain lookup) — a line that actually appears in the raw "
    "trace above. A fact you recognize but that no trace line backs does not "
    "belong here, however confident it feels.\n"
    "- Conflicting numbers for the same metric on the same asset are flagged "
    "as a conflict, or only the more verifiable (on-chain/DEX) figure is "
    "kept.\n"
    "- COMPLETE LISTS, NOT SUBSETS: when a result enumerates named items, name "
    "every one. Stage 2 never sees the raw trace; an item dropped here does "
    "not exist for it.\n"
    "- WHAT IT IS LIKE TO USE, VERBATIM: everything the trace shows about the "
    "product as a person meets it — what a screen displays, the steps of a "
    "flow actually clicked through, what is free versus paid, what a "
    "screenshot showed, the first thing a new user is asked to do — is a "
    "verified fact and carries over in full, with the same care as a contract "
    "parameter. Stage 2 writes the product section from these bullets.\n"
    "- NAMES AND URLS, VERBATIM: an asset's own `name` field and any URL a "
    "tool returned FOR that asset are verified facts — carry them over "
    "exactly, never shortened or dropped (a 'Memento Mori' digested as 'Mori "
    "coin' with its url dropped made Stage 2 call a self-evident name "
    "'undisclosed').\n\n"
    "### Verbatim Quotes\n"
    "- Only word-for-word quotes visible in tool results, each with its "
    "source. If none, write exactly: None\n\n"
    "### Context & Comparables\n"
    "- OTHER products, projects, or prior attempts that do something similar "
    "— on Algorand, another chain, or outside crypto — that a tool result "
    "surfaced. One bullet each: name, what it does in a line, what became of "
    "it if known, source. Label each unmistakably as a DIFFERENT product; a "
    "fact here is never a fact about the subject. This is what lets the "
    "writer answer 'why does this matter and how does it compare'. If none, "
    "write exactly: None\n\n"
    "### Liveness Signals\n"
    "- Whatever speaks to whether the subject is currently active: fetches "
    "that errored (name the URLs), accounts reading 'Offline' or dust, how "
    "recent the newest verifiable content is — or explicit confirmation that "
    "things ARE current ('primary site loaded fine, no dormancy signals') so "
    "Stage 2 knows the check was done. Report ONLY dates a tool actually "
    "returned; 'only one dated reference found (X)' is a complete answer — "
    "never invent a second or 'most recent' date.\n\n"
    "### Numeric Conversions\n"
    "- An ASA's on-chain `total` is a RAW INTEGER; real supply is "
    "`total / 10^decimals`. State the CONVERTED figure (total=100000000000 "
    "with decimals=2 is 1,000,000,000 tokens) and show the division so Stage "
    "2 never repeats the raw integer as a token count.\n\n"
    "### Chart\n"
    "- If a chart_data call in the trace returned a `markdown_fence`, copy "
    "that fence VERBATIM here — never re-derive or reformat it; Stage 2 has no "
    "tools. If chart_data was never called or every call errored, write "
    "exactly: None\n\n"
    "### Unresolved Gaps\n"
    "- Up to 3 SPECIFIC, answerable questions that materially matter and that "
    "a further tool call could plausibly resolve ('a real recent sale figure "
    "for this marketplace', 'the on-chain app ID for the registry contract'), "
    "each naming the missing fact and the kind of call that might find it — "
    "not 'more detail'. Do not list a gap you could have just gone and "
    "checked. A question a LATER tool result in the trace already answers, "
    "even partially, is not a gap — read the whole trace first, and if a "
    "result narrows a question without closing it, list only what remains, "
    "naming that result. If the research already covers the story, write "
    "exactly: None\n\n"
    "Do not write the article."
)


def _asset_candidates_from_result(tool: str, result: object) -> list[dict]:
    """The list of raw asset dicts a lookup_asset/lookup_asset_by_name result contains, regardless of which of the two shapes it is (a single asset dict vs a {"results": [...]} list)."""
    if not isinstance(result, dict):
        return []
    candidates = result.get("results") if tool == "lookup_asset_by_name" else [result]
    return candidates if isinstance(candidates, list) else []


def _collect_asset_facts(trace: list[dict]) -> dict[Any, dict[str, str]]:
    """{asset_id: {"name"/"unit_name"/"url": value}} for every asset seen across all lookup_asset/lookup_asset_by_name results in the trace."""
    facts: dict[Any, dict[str, str]] = {}
    for entry in trace:
        tool = str(entry.get("tool", ""))
        if tool not in ("lookup_asset", "lookup_asset_by_name"):
            continue
        for a in _asset_candidates_from_result(tool, entry.get("result")):
            if not isinstance(a, dict) or a.get("asset_id") is None:
                continue
            record = facts.setdefault(a["asset_id"], {})
            for field in ("name", "unit_name", "url"):
                value = a.get(field)
                if value:
                    record[field] = value
    return facts


def _format_asset_fact_line(asset_id: int | str, record: dict[str, str]) -> str:
    parts = [f"asset {asset_id}"]
    if record.get("name"):
        parts.append(f'name="{record["name"]}"')
    if record.get("unit_name"):
        parts.append(f'unit_name="{record["unit_name"]}"')
    if record.get("url"):
        parts.append(f"url={record['url']}")
    return "- " + ", ".join(parts)


def _extract_asset_facts(trace: list[dict]) -> str:
    """Every on-chain asset name/unit-name/url seen in lookup_asset* tool results, extracted mechanically (not by an LLM) — a hard safety net against digest-synthesis paraphrasing away or dropping one, since code can't lose a fact the way a summarization pass can. Root-caused 2026-08-05: a compose's raw trace had an asset's on-chain name "Memento Mori" and its own metadata url, but the LLM-synthesized digest shortened it to "Mori coin" and dropped the url — Stage 2, which only ever sees the digest, then called that same on-the-nose name "undisclosed"."""
    facts = _collect_asset_facts(trace)
    if not facts:
        return ""
    lines = ["### On-Chain Asset Names & URLs (verbatim — do not shorten, genericize, or drop)"]
    lines.extend(_format_asset_fact_line(asset_id, record) for asset_id, record in facts.items())
    return "\n".join(lines)


_NFD_VERIFIED_FIELDS = (
    "verified_discord",
    "verified_github",
    "verified_x",
    "verified_bluesky_did",
    "verified_telegram",
)


def _collect_identity_facts(trace: list[dict]) -> dict[str, dict[str, Any]]:
    """{nfd_name: {owner/nfd_profile_url/verified_*/linked_algorand_addresses}} for every search_nfd_directory result in the trace that resolved AND carries at least one cryptographically-verified social or linked-address field — a bare name/owner match with nothing verified isn't the independent identity corroboration this appendix exists for (still visible in the raw trace either way, just not force-surfaced)."""
    facts: dict[str, dict[str, Any]] = {}
    for entry in trace:
        if str(entry.get("tool", "")) != "search_nfd_directory":
            continue
        result = entry.get("result")
        if not isinstance(result, dict) or not result.get("found"):
            continue
        name = result.get("name")
        if not name:
            continue
        has_verified = any(result.get(f) for f in _NFD_VERIFIED_FIELDS) or result.get(
            "linked_algorand_addresses"
        )
        if not has_verified:
            continue
        record = facts.setdefault(name, {})
        for field in (
            "owner",
            "nfd_profile_url",
            *_NFD_VERIFIED_FIELDS,
            "linked_algorand_addresses",
        ):
            value = result.get(field)
            if value:
                record[field] = value
    return facts


def _format_identity_fact_line(name: str, record: dict[str, Any]) -> str:
    parts = [f'nfd "{name}"']
    if record.get("owner"):
        parts.append(f"owner={record['owner']}")
    for field in _NFD_VERIFIED_FIELDS:
        value = record.get(field)
        if value:
            parts.append(f"{field}={value}")
    linked = record.get("linked_algorand_addresses")
    if linked:
        parts.append(f"linked_addresses={','.join(linked)}")
    if record.get("nfd_profile_url"):
        parts.append(f"url={record['nfd_profile_url']}")
    return "- " + ", ".join(parts)


def _extract_identity_facts(trace: list[dict]) -> str:
    """Every NFD identity with at least one cryptographically-verified social/address link seen in search_nfd_directory results, extracted mechanically (not by an LLM) — the same hard safety net _extract_asset_facts provides for on-chain asset names, for verified identity cross-references specifically. Root-caused 2026-09-08 (AlgoChess recompose): search_nfd_directory returned a domain owner cryptographically verified to an X handle, and that SAME address was the admin/treasury field on the story's own contracts -- present in the raw trace, even referenced in the model's own gap-analysis reasoning, but never reached the published article. Raw-mode digests (provider == deepseek) skip the synthesized digest's own anti-dropping rules (COMPLETE LISTS, NOT SUBSETS / NAMES AND URLS, VERBATIM), so a real identity link a tool was built specifically to surface had no guarantee of surviving into Stage 2's actual prompt otherwise -- same failure class _extract_asset_facts already root-caused for asset names on 2026-08-05, same fix shape."""
    facts = _collect_identity_facts(trace)
    if not facts:
        return ""
    lines = [
        "### Verified Identity Links (verbatim — do not drop; cross-reference against "
        "any address/admin/owner/treasury field mentioned elsewhere in this material)"
    ]
    lines.extend(_format_identity_fact_line(name, record) for name, record in facts.items())
    return "\n".join(lines)


# Tools whose result reads on-chain (or testnet) data directly -- the
# highest fabrication-risk category (see unsourced_specifics_gate.py's own,
# narrower _RECORD_TOOLS for the companion POST-HOC claim check -- that set
# is scoped to which tools count as verifying a record-attributed claim
# specifically; this one is broader, every chain_tools.py handler, purely
# for grouping the WRITER'S digest so this category gets the best
# "attention real estate": placed first, kept closest to full length, and
# never silently absent).
_CHAIN_RECORD_TOOLS = frozenset(
    {
        "lookup_account",
        "lookup_asset",
        "lookup_application",
        "lookup_application_account",
        "lookup_asset_by_name",
        "lookup_first_funding",
        "lookup_account_transactions",
        "lookup_transaction_note",
        "lookup_arc69_metadata",
        "lookup_asset_transactions",
        "get_asset_transaction_volume",
        "nft_collection_distribution_timeline",
        "get_asset_holder_share",
        "lookup_asset_holders",
        "trace_asset_creator",
        "get_consensus_stats",
        "testnet_lookup",
        "application_boxes",
        "round_to_date",
    }
)
# Tools that actively EXPLORE a product/interactive experience -- a real
# game screen, a clicked-through flow, a captured screenshot -- as opposed
# to a passive page scrape. Deliberate exploration deserves the same
# "attention real estate" as an on-chain record (its own section, a
# generous budget), and lumping it with generic page dumps (as this file
# did until 2026-09-09) directly undercut THE SCENE INCLUDES THE PRODUCT
# ITSELF (_NARRATIVE_GUIDANCE): the writer can't weight product-experience
# material the digest itself just cut down to 2000 chars.
_INTERACTIVE_TOOLS = frozenset(
    {
        "play_interactive",
        "capture_screenshot",
        "click_element",
        "type_into_page",
    }
)
# Tools whose result is fetched/scraped web-page content -- the least
# information-dense category (mostly boilerplate/navigation/markup around a
# few real sentences), so it gets the smallest per-result char budget below.
_WEB_SOURCE_TOOLS = frozenset(
    {
        "fetch_url",
        "search_crawled_pages",
        "search_web",
        "extract_pdf_from_page",
        "fetch_google_doc",
        "discourse_forum",
        "grep_frontend_bundle",
        "inspect_network_hosts",
        "medium_api_article_list",
    }
)

# Per-result character budgets by category -- NOT one flat cap for every
# tool. A record lookup is a few hundred chars and information-dense; a
# scraped page is mostly chrome. Args stay small everywhere -- they're the
# model's own request, never evidence.
_ARGS_MAX_CHARS = 500
_RECORD_RESULT_MAX_CHARS = 6000
_INTERACTIVE_RESULT_MAX_CHARS = 6000
_WEB_RESULT_MAX_CHARS = 2000
_OTHER_RESULT_MAX_CHARS = 4000
_FAILED_RESULT_MAX_CHARS = 300


def _dedup_and_filter_trace(trace: list[dict]) -> list[dict]:
    """Drop trace entries carrying no tool name, and collapse exact reruns to their first occurrence. Presentation-only: never touches the raw trace list itself -- the deterministic gates' own grounding corpus still reads the ORIGINAL, unfiltered trace (see fact_align.grounding_entries), this is only what the WRITER'S digest shows.

    Keeps ``{"error": ...}`` entries (routed to their own FAILED FETCHES
    section by _format_full_research_trace, not dropped -- see that
    function's docstring for why: a fetch_url DNS failure carries a "hint"
    field with the actionable DEFUNCT-domain instruction, the MyAlgo
    incident's fix, and dropping the whole entry silently dropped that
    hint from what the writer sees). A genuine zero/empty finding (e.g.
    "0K+ Credentials issued") was never an error shape to begin with and
    is unaffected either way: that is real negative evidence a writer
    needs to debunk a fabricated positive claim (the GoPlausible incident).

    The dedup key includes whether the result was an error (found via a
    test written alongside this fix, 2026-09-09): a failed fetch_url call
    retried with the SAME url/args, succeeding the second time, must keep
    BOTH entries -- (tool, args) alone would treat the successful retry as
    a "duplicate" of the failure and silently drop the one result that
    actually matters.
    """
    seen: set[tuple[str, str, bool]] = set()
    out: list[dict] = []
    for entry in trace:
        tool = str(entry.get("tool", ""))
        if not tool:
            continue
        try:
            args_key = json.dumps(entry.get("arguments", {}), sort_keys=True, default=str)
        except (TypeError, ValueError):
            args_key = str(entry.get("arguments", ""))
        result = entry.get("result")
        is_error = isinstance(result, dict) and "error" in result
        key = (tool, args_key, is_error)
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def _format_trace_entry(entry: dict, *, result_max_chars: int) -> str:
    tool = str(entry.get("tool", ""))
    try:
        args_s = json.dumps(entry.get("arguments", {}), separators=(",", ":"))[:_ARGS_MAX_CHARS]
        result_s = json.dumps(entry.get("result", {}), separators=(",", ":"))[:result_max_chars]
    except (TypeError, ValueError):
        args_s = str(entry.get("arguments", ""))[:_ARGS_MAX_CHARS]
        result_s = str(entry.get("result", ""))[:result_max_chars]
    return f"- {tool}({args_s}) -> {result_s}"


def _format_failed_entry(entry: dict) -> str:
    """Compact one-line format for a failed tool call -- the error plus any actionable ``hint`` (e.g. the DEFUNCT-domain instruction a DNS failure carries, see research_tools._fetch_failure_hint), never the full raw error payload, which is mostly noise once the fact of failure is recorded."""
    tool = str(entry.get("tool", ""))
    result = entry.get("result") if isinstance(entry.get("result"), dict) else {}
    try:
        args_s = json.dumps(entry.get("arguments", {}), separators=(",", ":"))[:_ARGS_MAX_CHARS]
    except (TypeError, ValueError):
        args_s = str(entry.get("arguments", ""))[:_ARGS_MAX_CHARS]
    error = str(result.get("error", ""))[:_FAILED_RESULT_MAX_CHARS]
    hint = str(result.get("hint", ""))[:_FAILED_RESULT_MAX_CHARS]
    line = f"- {tool}({args_s}) -> ERROR: {error}"
    if hint:
        line += f" | {hint}"
    return line


def _trace_section(title: str, lines: list[str], empty_note: str) -> str:
    body = "\n".join(lines) if lines else f"({empty_note})"
    return f"### {title}\n{body}"


def _format_full_research_trace(trace: list[dict]) -> str:
    """Categorized, deduped trace text — the RESEARCH_DIGEST_MODE=raw alternative to an LLM-synthesized digest (see _synthesize_research_digest).

    Root-caused 2026-09-09 (AlgoChess incident, digest-attention followup):
    the prior version was one flat chronological list of every entry, one
    8000-char cap for every tool regardless of value. A fabricated
    on-chain-note claim slipped through partly because the one real record
    lookup that mattered sat at line 143 of 188 undifferentiated lines —
    exactly the "lost in the middle" shape attention research describes for
    long, undifferentiated contexts.

    Now: entries are deduped first (_dedup_and_filter_trace), then grouped
    into five sections ordered by fabrication risk — ON-CHAIN RECORDS
    (chain_tools.py lookups) first and least-truncated, PRODUCT/INTERACTIVE
    EXPERIENCE (play_interactive/capture_screenshot/click_element/
    type_into_page — deliberate exploration, same generous budget as
    records; see _INTERACTIVE_TOOLS' own comment for why this used to be
    lumped with passive page dumps and cut down before the writer ever saw
    it), WEB SOURCES (passive scraped/fetched pages, most-truncated —
    least information-dense), OTHER FINDINGS (search/market/social/misc),
    and FAILED FETCHES last (errors, compact one-liners — kept, not
    dropped: a fetch_url DNS failure carries an actionable "hint" field,
    e.g. the MyAlgo incident's DEFUNCT-domain instruction, that the writer
    must still see even though the fetch itself produced no content).
    Every section always renders, even empty — an explicit "no on-chain
    records were looked up this pass" is itself useful negative evidence
    right before a writer about to claim something about a transaction or
    metadata field. Returns "" only when there is nothing usable at all
    (empty trace) — same short-circuit the caller already relies on.
    """
    entries = _dedup_and_filter_trace(trace)
    if not entries:
        return ""
    chain: list[str] = []
    interactive: list[str] = []
    web: list[str] = []
    other: list[str] = []
    failed: list[str] = []
    for entry in entries:
        tool = str(entry.get("tool", ""))
        result = entry.get("result")
        if isinstance(result, dict) and "error" in result:
            failed.append(_format_failed_entry(entry))
        elif tool in _CHAIN_RECORD_TOOLS:
            chain.append(_format_trace_entry(entry, result_max_chars=_RECORD_RESULT_MAX_CHARS))
        elif tool in _INTERACTIVE_TOOLS:
            interactive.append(
                _format_trace_entry(entry, result_max_chars=_INTERACTIVE_RESULT_MAX_CHARS)
            )
        elif tool in _WEB_SOURCE_TOOLS:
            web.append(_format_trace_entry(entry, result_max_chars=_WEB_RESULT_MAX_CHARS))
        else:
            other.append(_format_trace_entry(entry, result_max_chars=_OTHER_RESULT_MAX_CHARS))

    return "\n\n".join(
        [
            _trace_section(
                "ON-CHAIN RECORDS VERIFIED",
                chain,
                "no on-chain record lookups were made this research pass -- do not "
                "describe what a transaction note, metadata field, or application "
                "state contains",
            ),
            _trace_section(
                "PRODUCT/INTERACTIVE EXPERIENCE EXPLORED",
                interactive,
                "no interactive exploration (play_interactive/capture_screenshot/"
                "click_element/type_into_page) was done this research pass",
            ),
            _trace_section(
                "WEB SOURCES EXPLORED", web, "no web pages were fetched this research pass"
            ),
            _trace_section(
                "OTHER RESEARCH FINDINGS",
                other,
                "no other tool calls were made this research pass",
            ),
            _trace_section("FAILED FETCHES", failed, "no tool call failed this research pass"),
        ]
    )


_GAP_EXTRACTION_PROMPT = (
    "From the raw tool trace below, identify ONLY unresolved research gaps "
    "-- you are NOT writing the article or any other digest section. Output "
    "Markdown ONLY, exactly this one section:\n\n"
    "### Unresolved Gaps\n"
    "- List up to 3 SPECIFIC, answerable questions that materially matter to "
    "this story and that a further tool call could plausibly resolve (e.g. "
    "'a real recent sale/transaction figure for this marketplace', 'the "
    "on-chain app ID for the registry contract'). Each gap must name the "
    "missing fact and what kind of tool call might find it -- not a vague "
    "'more detail' or 'more sources'. A question a LATER tool result in this "
    "same trace already answers, even partially, is not a gap -- read the "
    "whole trace before listing one, and if a result narrows a question "
    "without closing it, list only what remains, naming the result that "
    "narrowed it. If the research already covers the "
    "story adequately, write exactly: None\n\n"
    "Do not write anything else."
)


def _extract_gaps_from_raw_trace(
    full_trace: str,
    research_context: str,
    *,
    extra_usage: dict[str, int] | None = None,
    debug: dict | None = None,
) -> str:
    """A cheap, single-purpose LLM call standing in for the '### Unresolved Gaps' section a synthesized digest would otherwise carry.

    Raw-trace mode (RESEARCH_DIGEST_MODE=raw, or any deepseek research) skips
    the full _RESEARCH_DIGEST_SYNTHESIS pass entirely, so the digest it hands
    back never has its own Unresolved Gaps section -- _extract_unresolved_gaps
    always found nothing on it, so _run_digest_gap_fill's bounded second-look
    pass silently never ran for a large-context provider (root-caused
    2026-08-16). This is deliberately NOT the full synthesis prompt -- raw
    mode exists specifically to skip that -- just the one section gap-fill
    actually needs to decide whether to run at all.

    Empty string on any failure (a bad response, a client error): the caller
    then simply skips gap-fill, same as a digest that honestly reported no
    gaps -- never worse than today's (also gap-fill-less) raw-mode behavior.
    `extra_usage`, if given, gets this call's real spend merged in even on
    failure -- this builds its own ephemeral digest-tier client rather than
    reusing the compose's research_llm/llm pair, so nothing else accounts
    for it (2026-08-28 audit).
    """
    digest_client = get_llm_digest_client()
    started_at_ms = _now_ms()
    try:
        from app.core.config import LLM_TEMP_RESEARCH

        text = digest_client.chat_completion(
            [
                {
                    "role": "user",
                    "content": (
                        f"{research_context}\n\nRaw tool trace:\n{full_trace}\n\n"
                        f"{_GAP_EXTRACTION_PROMPT}"
                    ),
                },
            ],
            json_object=False,
            temperature=LLM_TEMP_RESEARCH,
        )
        return (text or "").strip()
    except Exception:
        logger.warning("raw-mode gap extraction failed; skipping gap-fill", exc_info=True)
        return ""
    finally:
        _merge_usage_from(extra_usage, digest_client)
        _note_llm_call(debug, "raw_gap_extraction", started_at_ms, _now_ms())


def _synthesize_research_digest(
    *,
    trace: list[dict],
    research_context: str,
    provider: str = "",
    extra_usage: dict[str, int] | None = None,
    debug: dict | None = None,
) -> str:
    """Stage 1→2 handoff: model-synthesized digest instead of raw tool JSON, unless RESEARCH_DIGEST_MODE=raw (see config.py) or the research provider is deepseek — deepseek's context window is large enough to read the raw trace directly (owner call, 2026-08-06), so it always skips synthesis regardless of the config value; RESEARCH_DIGEST_MODE=raw remains a manual override for forcing raw mode on Mistral too. Deterministic asset-facts appendix is still added either way, since that's free regardless of mode. Raw mode also runs a cheap, separate gap-extraction call (see _extract_gaps_from_raw_trace) so _run_digest_gap_fill's safety net still has something to find -- a full raw trace has no '### Unresolved Gaps' section of its own.

    `extra_usage`, if given, receives every ephemeral digest-tier client this
    call spends (this call's own synthesis pass, plus the raw-mode gap
    extraction it may delegate to) -- see _merge_usage's docstring for why
    that accounting doesn't happen anywhere else.
    """
    from app.core.config import DIGEST_GAP_FILL_ENABLED, RESEARCH_DIGEST_MODE

    asset_facts = _extract_asset_facts(trace)
    identity_facts = _extract_identity_facts(trace)
    extra_facts = "\n\n".join(f for f in (asset_facts, identity_facts) if f)
    if RESEARCH_DIGEST_MODE == "raw" or provider == "deepseek":
        full_trace = _format_full_research_trace(trace)
        if not full_trace.strip():
            return ""
        digest = f"{full_trace}\n\n{extra_facts}" if extra_facts else full_trace
        if DIGEST_GAP_FILL_ENABLED:
            gaps_section = _extract_gaps_from_raw_trace(
                full_trace, research_context, extra_usage=extra_usage, debug=debug
            )
            if gaps_section:
                digest = f"{digest}\n\n{gaps_section}"
        return digest

    raw_trace = _format_research_digest(trace)
    if not raw_trace.strip():
        return ""
    digest_client = get_llm_digest_client()
    started_at_ms = _now_ms()
    try:
        from app.core.config import LLM_TEMP_RESEARCH

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
            temperature=LLM_TEMP_RESEARCH,
        )
        text = (digest or "").strip()
        if not text:
            return raw_trace
        return f"{text}\n\n{extra_facts}" if extra_facts else text
    except Exception:
        logger.warning("research digest synthesis failed; using raw trace", exc_info=True)
        return raw_trace
    finally:
        _merge_usage_from(extra_usage, digest_client)
        _note_llm_call(debug, "digest_synthesis", started_at_ms, _now_ms())


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
    """Record a (synthetic) tool call + result into the debug transcript so the admin Sessions view shows it. Two-stage compose calls the grader directly rather than via the model's tool loop, so these turns aren't captured automatically the way the legacy single-loop's were.

    The synthetic pair needs its OWN id/tool_call_id linkage, generated here
    and shared between both messages -- exactly like a real API response's
    tool_calls[].id and _run_tool_call's paired tool-role message. Root-
    caused 2026-08-15: this previously built the assistant tool_calls entry
    with no `id` at all and the tool-role message with no `tool_call_id` at
    all, two SEPARATE gaps that neither matched each other nor got backfilled
    together -- `_ensure_tool_call_ids` (llm_openai_compatible.py) only ever patches
    the assistant side, so once this synthetic pair is later merged into a
    revision-pass request (`_merged_convo_with_prior_debug`) and replayed
    through a stricter provider, the id backfill on the assistant side made
    the tool-role message's still-missing tool_call_id impossible to
    reconcile after the fact ("messages with role 'tool' must have a
    'tool_call_id'", GPT-5.6-luna, confirmed live). Assigning a real id up
    front for both sides closes the gap at its source instead of patching
    around it downstream again.
    """
    import json as _json
    import uuid as _uuid

    if debug is None or not isinstance(debug.get("messages"), list):
        return
    call_id = f"call_{_uuid.uuid4().hex[:24]}"
    debug["messages"].append(
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": _json.dumps(arguments)},
                }
            ],
        }
    )
    debug["messages"].append(
        {
            "role": "tool",
            "name": name,
            "tool_call_id": call_id,
            "content": _json.dumps(result)[:4000],
        }
    )


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
    """Record the grading result in the trace/debug transcript (like a review_draft tool call) and attach it to the current draft, so every return below carries this grade even when no (further) revision is attempted — the caller (publish gate) reads it via LLMArticleFields.heuristic_grade."""
    # The grader reads the FULL title+body; we record only a compact label in
    # the trace (title + word count) to avoid dumping the whole body into it.
    grade_args = {"title": title, "words": len(body.split())}
    if revise_count > 0:
        grade_args["recheck"] = True
    trace.append({"tool": "review_draft", "arguments": grade_args, "result": review})
    _debug_tool_turn(debug, "review_draft", grade_args, review)
    current["_heuristic_grade"] = review


def _draft_score(review: dict, *, needs_revision: bool) -> float:
    """The best-of-N comparison score for one graded pass: the heuristic grade, penalized when the LLM quality rubric OR the factcheck audit flags a revision.

    Root-caused via design review before shipping (2026-09-09): this used to
    only read ``needs_revision`` (the style-rubric-only trigger) -- a pass
    with a WRONG factcheck verdict but a marginally higher heuristic grade
    than the very next (corrected) pass would win best-of-N and get
    returned, silently discarding the forced revision that fixed it. A
    factcheck-forced revision is exactly as real a defect as a style-forced
    one and must be penalized the same way here, not just in the
    ``fixable`` list that drives whether another pass runs.
    """
    from app.modules.newspaper.article_quality_llm import factcheck_needs_revision

    grade_val = review.get("grade")
    score = float(grade_val) if isinstance(grade_val, int | float) else 0.0
    if needs_revision or factcheck_needs_revision(review.get("factcheck") or {}):
        score -= 2.0
    return score


def _grade_current_draft(
    title: str,
    summary: str,
    body: str,
    quality_llm: MistralProvider,
    *,
    is_special_edition: bool = False,
    debug: dict | None = None,
) -> dict:
    """Run the deterministic heuristic grader, the LLM quality rubric, and the LLM factcheck audit, merging the rubric result into the returned review dict under "quality" and the factcheck result under "factcheck". Each grader's failure degrades to an error marker rather than raising. Each LLM call gets its own wall-clock window recorded via _note_llm_call (the deterministic grader is not an LLM call and is not timed)."""
    from app.modules.newspaper.article_grader import fuse_quality_into_grade, grade_article_draft
    from app.modules.newspaper.article_quality_llm import (
        check_factual_claims,
        grade_article_quality_llm,
    )

    try:
        review = grade_article_draft(
            title=title, summary=summary, body=body, is_special_edition=is_special_edition
        )
    except Exception as exc:
        review = {"error": str(exc)[:200], "grade": None}
    rubric_started_at_ms = _now_ms()
    try:
        quality = grade_article_quality_llm(title=title, body=body, client=quality_llm)
    except Exception as exc:
        quality = {"model": "llm_rubric_error", "error": str(exc)[:200], "issues": []}
    finally:
        _note_llm_call(debug, "quality_rubric", rubric_started_at_ms, _now_ms())
    factcheck_started_at_ms = _now_ms()
    try:
        factcheck = check_factual_claims(title=title, body=body, client=quality_llm)
    except Exception as exc:
        factcheck = {"model": "llm_factcheck_error", "error": str(exc)[:200], "claims": None}
    finally:
        _note_llm_call(debug, "factcheck", factcheck_started_at_ms, _now_ms())
    review["quality"] = quality
    review["factcheck"] = factcheck
    return fuse_quality_into_grade(review, quality)


def _grade_is_degraded(review: dict) -> bool:
    """True when this pass's grade carries no real signal to judge by — the deterministic grader errored (no numeric grade), the LLM quality rubric errored (``llm_rubric_error``, no rubric scores), or the factcheck audit errored (``claims: None`` — see check_factual_claims' own docstring for why that's distinct from a confirmed-clean ``claims: []``). ``quality_needs_revision``/``factcheck_needs_revision`` both read an unavailable signal as "nothing flagged", so a degraded grade must never be trusted as a confirmed-clean re-evaluation of a draft that was JUST revised to fix a flagged issue — see the ``regrade_unconfirmed`` use in ``_run_grade_revise_loop``. Root-caused via design review before shipping (2026-09-09): a regrade whose factcheck call happened to fail right after a factual-only surgical revision would have read as "confirmed clean" instead of "unconfirmed" without this."""
    if review.get("grade") is None:
        return True
    quality = review.get("quality") or {}
    if bool(quality.get("error")) or quality.get("model") == "llm_rubric_error":
        return True
    factcheck = review.get("factcheck") or {}
    # "disabled"/"skipped" are intentional, stable no-signal states -- same
    # non-degraded treatment the quality rubric already gives them above.
    # Only a genuine failure (check_factual_claims' own retry-then-fail-closed
    # path, or _grade_current_draft's except clause, both land on this exact
    # model string) counts as degraded here.
    return factcheck.get("model") == "llm_factcheck_error"


def _regrade_is_unconfirmed(revise_count: int, review: dict) -> bool:
    """True when this pass is a REGRADE (a revision already ran this loop) that came back degraded -- see _grade_is_degraded's docstring for why that can't be trusted as a confirmed-clean re-evaluation."""
    return revise_count > 0 and _grade_is_degraded(review)


def _maybe_stamp_regrade_unconfirmed(
    draft: dict, review: dict, flagged: set[str], *, unconfirmed: bool
) -> None:
    """A revision was attempted specifically because something was flagged, and the regrade meant to CONFIRM the fix came back degraded (grader or rubric error) -- we have no real signal on whether the flagged issue is actually resolved. Publishing this as if the regrade had come back clean would silently reuse an unconfirmed grade exactly like a stale one (CLAUDE.md invariant 1) -- stamp a hold reason so the publish gate routes this to human review instead (mirrors unsourced_hold_reason/broken_link_hold_reason's existing wiring through LLMArticleFields -> ArticleComposeResult -> publish_tasks). A no-op when ``unconfirmed`` is False."""
    if not unconfirmed:
        return
    detail = (
        review.get("error")
        or (review.get("quality") or {}).get("error")
        or "grader/rubric returned no usable score"
    )
    draft["_regrade_unconfirmed_hold_reason"] = (
        f"revision regrade could not confirm the fix ({str(detail)[:200]}); "
        f"previously flagged: {', '.join(sorted(flagged))[:300] or '(unknown)'}"
    )


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


def _broken_link_claim_gate_issues(body: str, trace: list[dict]) -> list[str]:
    """Broken-link-claim feedback: an unverified "this link is broken/404s" claim is fed back so the writer clicks the actual control (or softens the claim) here — better than the post-hoc gate holding the whole draft for a human (lumirogue.com, recurred 2026-08-10 and 2026-08-12)."""
    from app.core.config import BROKEN_LINK_CLAIM_GATE_ENABLED

    if not BROKEN_LINK_CLAIM_GATE_ENABLED:
        return []
    try:
        from app.modules.newspaper.broken_link_claim_gate import broken_link_claim_revision_issues

        return broken_link_claim_revision_issues(body, trace)
    except Exception:
        logger.warning("broken-link-claim check failed during revision", exc_info=True)
        return []


def _completeness_gate_issues(
    title: str, body: str, trace: list[dict], *, enabled: bool = False
) -> list[str]:
    """Completeness feedback: a mandatory due-diligence tool call (sanctions/PEP screen, corporate registry) the source material calls for but the trace never made is fed back so the writer — which has tool access during revision — can call it here, before the post-hoc gate holds the whole draft for a human (root-caused 2026-09-09: `_fresh_auto_approve_passes` already correctly blocks auto-approve and diverts to review on a completeness failure, but nothing ever fed that finding back to the writer; a held draft just sat there unfixed).

    ``enabled`` (the caller's own `check_completeness_gate`) must be True for
    this to run at all — only FRESH content sets it True; recompose
    deliberately excludes completeness entirely (owner-confirmed 2026-07-12:
    it fires on ~every Tier-2 source, decoupled from actual quality).
    ``check_completeness`` does substring matching on the trace, so a tool
    call that itself errors (e.g. no API key configured) still satisfies the
    rule once made — this is a "was due diligence attempted" check, not a
    "did it succeed" check, same as every other completeness rule.
    """
    from app.core.config import COMPLETENESS_REVISION_ENABLED

    if not enabled or not COMPLETENESS_REVISION_ENABLED:
        return []
    try:
        from app.modules.gatekeeper.completeness import (
            check_completeness,
            named_persons_unscreened,
        )

        article_text = f"{title}\n{body}"
        trace_str = json.dumps(trace or [])
        comp = check_completeness(article_text, trace_str)
        if comp.passed:
            return []
        issues: list[str] = []
        for rule_name in comp.failed_rules:
            if rule_name == "human_identity":
                unscreened = named_persons_unscreened(article_text, trace_str)
                who = f" for: {', '.join(unscreened)}" if unscreened else ""
                issues.append(
                    "missing due-diligence check: the piece names a founder/CEO/"
                    "president/chairman but never called the sanctions/PEP "
                    f"screening tool{who} — call screen_sanctions_and_pep now, "
                    "before this can be published."
                )
            elif rule_name == "company_backing":
                issues.append(
                    "missing due-diligence check: the piece names an incorporated "
                    "company (Inc./LLC/Ltd./GmbH/a registered company) but never "
                    "called the corporate-registry lookup tool — call "
                    "query_corporate_registry now, before this can be published."
                )
            else:
                issues.append(f"missing mandatory check: {comp.detail.get(rule_name, rule_name)}")
        return issues
    except Exception:
        logger.warning("completeness gate check failed during revision", exc_info=True)
        return []


def _collect_fixable_issues(
    review: dict,
    quality: dict,
    *,
    needs_revision: bool,
    title: str,
    body: str,
    trace: list[dict],
    gen_user: str,
    user: str,
    research_user: str | None,
    link_check_cache: dict,
    chain_check_cache: dict,
    factcheck: dict | None = None,
    check_completeness_gate: bool = False,
) -> tuple[list[str], list[str]]:
    """Gather every revision-worthy issue across the schema check, quality rubric, the factcheck audit, and the deterministic gates, stashing each gate's own findings onto `review` for the trace/telemetry record. Returns ``(fixable, localized_fixable)`` -- the full flat list (unchanged shape/order from before), and the subset of it that names one specific claim/passage rather than a whole-piece pattern (see ``_partition_fixable_by_scope``'s module comment for the full list of which category is which).

    ``check_completeness_gate`` (2026-09-09): opt-in, set only for fresh
    content (see `_run_grade_revise_loop`'s own docstring) — the completeness
    gate deliberately does not run for recompose.
    """
    from app.modules.newspaper.article_quality_llm import factcheck_forcing_issues, factcheck_issues

    issues = list(review.get("issues") or [])
    # "headline" issues are the structural enforcement of the house headline
    # style: the prompt states the rules, but only this deterministic check +
    # forced revision makes them invariants (prompts drift; regexes don't).
    schema_fixable = [
        i for i in issues if i.startswith(("too long", "structure", "schema", "headline"))
    ]
    quality_fixable: list[str] = list(quality.get("issues") or []) if needs_revision else []

    # factcheck (2026-09-09): every overstated/wrong protocol-mechanics claim
    # is persisted for a human reviewer regardless of severity, but only a
    # WRONG verdict is placed FIRST in the fixable list -- ahead of every
    # other category -- so it survives _build_revision_prompt's fixable[:10]
    # truncation and the reviser sees it before anything else. "overstated"
    # claims never force a revision pass on their own (factcheck_forcing_issues
    # excludes them); see factcheck_needs_revision's own docstring.
    factual_fixable: list[str] = []
    if factcheck:
        all_concerns = factcheck_issues(factcheck)
        if all_concerns:
            review["factual_concerns"] = all_concerns
        factual_fixable = factcheck_forcing_issues(factcheck)

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
    broken_link_fixable = _broken_link_claim_gate_issues(body, trace)
    if broken_link_fixable:
        review["broken_link_claims"] = broken_link_fixable
    stale_deadline_fixable = _stale_deadline_gate_issues(body)
    if stale_deadline_fixable:
        review["stale_deadlines"] = stale_deadline_fixable
    completeness_fixable = _completeness_gate_issues(
        title, body, trace, enabled=check_completeness_gate
    )
    if completeness_fixable:
        review["completeness_gap"] = completeness_fixable

    # localized: each of these names ONE specific claim/passage/quote that
    # lives in one place in the draft -- see _partition_fixable_by_scope's
    # module comment for why these (not schema/quality) get the
    # single-paragraph revision instruction.
    localized_fixable = (
        factual_fixable
        + completeness_fixable
        + link_fixable
        + chain_fixable
        + authority_fixable
        + unsourced_fixable
        + broken_link_fixable
        + stale_deadline_fixable
    )
    fixable = (
        factual_fixable
        # completeness_fixable placed right after factual_fixable, same
        # reasoning as that list's own comment above: a mandatory
        # due-diligence gap must survive _build_revision_prompt's
        # fixable[:10] truncation, not get crowded out by lower-stakes issues.
        + completeness_fixable
        + schema_fixable
        + quality_fixable
        + link_fixable
        + chain_fixable
        + authority_fixable
        + unsourced_fixable
        + broken_link_fixable
        + stale_deadline_fixable
    )
    return fixable, localized_fixable


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
    "verified fact and every section from the draft. (Moving a fact into the "
    "section it belongs to, or merging two sections, is not shrinking — "
    "preservation is about facts, not section count or position.)"
)


def _revision_length_rule(
    *, too_long: bool, needs_depth: bool, needs_factual_fix: bool = False
) -> str:
    """The scope instruction for a revision prompt: what to fix, and (for everything except too_long) an explicit "don't shrink it" rule instead of a numeric word-count target.

    ``needs_factual_fix`` (2026-09-09): a factcheck-flagged WRONG claim gets a
    NARROW, surgical instruction distinct from ``needs_depth``'s broad
    "improve narrative synthesis/technical depth/critical distance" rewrite
    -- a factual-only trigger on an otherwise-5/5 draft must not license a
    whole-piece restructure it never asked for (root-caused via review before
    shipping: a factual-only trigger folded into the broad needs_depth branch
    would send a correctly-scored draft into an unnecessarily wide rewrite,
    exactly the kind of regression risk the best-of-N scoring exists to
    catch, but is cheaper to avoid in the first place). Only reached when
    needs_depth is False -- a style-triggered revision already licenses
    fixing everything flagged, factual concerns included.
    """
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
            "instead of just relaying the subject's own marketing framing. Put "
            "each named risk in the section where the claim it qualifies already "
            "lives — never satisfy this by appending a new caveats block at the "
            "end, and never leave the piece's last section mostly about what is "
            "unverified when the rest of the draft is mostly about what verified. "
            "Apply "
            "the NO REPETITION rule from your instructions here too — a VERBATIM "
            "repeat across sections may be cut to its first mention — but that is "
            "not license to rewrite whole passages into a shorter synthesis just "
            "because a point recurs. Do NOT invent quotes, partnerships, or "
            "numbers. " + _REVISION_NO_SHRINK_RULE
        )
    if needs_factual_fix:
        return (
            "Correct or hedge EXACTLY the specific claim(s) named in the factual "
            "concern(s) above — verify what your own knowledge (or a fresh tool "
            "lookup, if one could settle it) actually supports, then either fix "
            "the claim to state it correctly or soften it to what's actually "
            "verifiable (e.g. 'whether X depends on Y' instead of asserting X as "
            "settled). Change NOTHING else in the piece — this is a narrow, "
            "surgical correction, not a pass to improve narrative synthesis, "
            "technical depth, or add new material. Do NOT invent quotes, "
            "partnerships, or numbers. " + _REVISION_NO_SHRINK_RULE
        )
    return (
        "PRESERVE every fact — only REORGANIZE the existing prose into section "
        "headings and short paragraphs; move comparative data into a Markdown "
        "table if needed. Do NOT use narrative bullet lists. Do NOT drop "
        "information or summarize away detail. " + _REVISION_NO_SHRINK_RULE
    )


# Root-caused 2026-09-10 (Derova mempool piece, real compose): a single
# blanket "touch only what's flagged" instruction fights a DIFFERENT class
# of issue the same revision pass often has to fix at once. That piece's
# own grade_detail flagged BOTH at once: one wrong fee-mechanics claim (one
# sentence, one paragraph) AND a repetition issue -- "the core judgment...
# is re-argued from scratch four times (intro, the 2021 forum caveat, the
# architecture section, the closing) -- state it once and reference it
# afterward" -- which by its nature spans four non-contiguous sections.
# Fixing the repetition issue genuinely requires touching multiple
# paragraphs; a rigid single-paragraph rule would fight that. The two issue
# kinds need different scopes in the SAME prompt:
#   - localized: factcheck-wrong claims, unsourced-specifics findings,
#     broken-link-claims, stale-deadline flags, dead links, unattributed-
#     authority, chain-entity mismatches, completeness gaps -- each names
#     ONE specific claim/passage/quote that lives in one place in the
#     draft. These get "find the paragraph containing this, redo only
#     that paragraph."
#   - document-wide: schema/structure issues (Formatting Deserts, Buried
#     Metrics, Citation Density) and quality-rubric issues (repetition,
#     narrative synthesis, technical depth, critical distance) -- these
#     are whole-piece patterns by construction. These keep the existing,
#     broader license to restructure/rewrite across sections.
def _partition_fixable_by_scope(
    fixable: list[str], localized_fixable: list[str]
) -> tuple[list[str], list[str]]:
    """Split ``fixable`` (already truncated to the top 10 by caller) into (localized, document_wide) using membership in ``localized_fixable`` -- the caller already knows each issue's kind (it built the category lists in ``_collect_fixable_issues``), this just re-applies that same classification after the two have been flattened together for truncation/already-fixed tracking."""
    localized_set = set(localized_fixable)
    localized = [i for i in fixable if i in localized_set]
    document_wide = [i for i in fixable if i not in localized_set]
    return localized, document_wide


def _revision_issues_block(fixable: list[str], localized_fixable: list[str]) -> str:
    """The "here's what a reviewer flagged" section of the revision prompt, scope-labeled per ``_partition_fixable_by_scope``. Truncates to the top 10 of ``fixable`` FIRST (preserving the existing priority order -- factual/completeness concerns are placed first by ``_collect_fixable_issues`` specifically so they survive this truncation), then classifies what survives -- never the other way around, or a low-priority document-wide issue could bump a high-priority localized one out entirely."""
    localized, document_wide = _partition_fixable_by_scope(fixable[:10], localized_fixable)
    blocks = ["A reviewer flagged these problems:"]
    if localized:
        localized_text = "\n".join(f"- {i}" for i in localized)
        blocks.append(
            f"These specific claims need fixing:\n{localized_text}\n\n"
            "For EACH one, find the paragraph in the current draft that "
            "contains it and revise ONLY that paragraph to fix it. Leave "
            "every other paragraph in the piece exactly as it is, copied "
            "verbatim — a paragraph you don't need to touch cannot "
            "introduce a new error, but one you rewrite \"while you're at "
            'it" can (a real incident: fixing one wrong fee-mechanics '
            "claim by also rewriting its surrounding paragraph introduced "
            "two MORE wrong claims about the same topic on the very next "
            "revision pass)."
        )
    if document_wide:
        document_wide_text = "\n".join(f"- {i}" for i in document_wide)
        blocks.append(
            f"These broader issues need fixing:\n{document_wide_text}\n\n"
            "These are whole-piece patterns (the same point restated "
            "across sections, structure, narrative depth) that genuinely "
            "can't be fixed by touching one paragraph — restructure or "
            "rewrite across as many sections as the issue actually "
            "requires."
        )
    return "\n\n".join(blocks)


def _build_revision_prompt(
    gen_user: str,
    fixable: list[str],
    already_fixed: list[str],
    *,
    too_long: bool,
    needs_depth: bool,
    needs_factual_fix: bool = False,
    localized_fixable: list[str] | None = None,
) -> str:
    """Build the revision-pass prompt: the flagged issues (scoped per issue -- see ``_partition_fixable_by_scope``), the length/depth rule for this pass, and the grounding/tool-access rules every revision shares.

    ``localized_fixable`` (2026-09-10, root-caused on the Derova mempool
    piece): the subset of ``fixable`` that names one specific claim/passage
    (a factcheck-wrong claim, an unsourced-specifics finding, a dead link, a
    completeness gap, ...) rather than a whole-piece pattern (repetition,
    structure, narrative depth). These get a narrower "find the paragraph
    that contains this and redo only that paragraph" instruction instead of
    open license to touch the whole piece -- see
    ``_partition_fixable_by_scope``'s own docstring for why a single
    blanket instruction doesn't work here (repetition genuinely needs
    cross-paragraph edits; a wrong fee-mechanics claim doesn't).
    """
    issues_block = _revision_issues_block(fixable, localized_fixable or [])
    carried_block = ""
    if already_fixed:
        carried_block = (
            "\n\nThese issues were already fixed in an earlier revision pass — "
            "do NOT reintroduce them while addressing the list above:\n"
            + "\n".join(f"- {i}" for i in already_fixed[:10])
        )
    length_rule = _revision_length_rule(
        too_long=too_long, needs_depth=needs_depth, needs_factual_fix=needs_factual_fix
    )
    return (
        gen_user + f"\n\n{issues_block}\n\n"
        f"{length_rule} You have tool access again for this revision, specifically "
        "to fix the flagged problems above — call a tool when a flagged issue is "
        "something a fresh lookup could actually resolve (an unverified or stale "
        "figure, a dead link that has a real live replacement, an asset/claim that "
        "needs re-checking). review_draft is NOT among the tools available this "
        "pass — do not call it, even though you used it earlier; grading happens "
        "automatically once you return the revised article below, so there is no "
        "self-check step here (root-caused live 2026-08-17: the model kept trying "
        "review_draft here out of habit from stage 2 and got an unknown-tool error "
        "every time). Do NOT invent facts, and do not use tools to go "
        "research a new angle unrelated to the flagged issues or pad with new "
        "filler — every new fact you add must come from an actual tool result in "
        "this pass, the same STRICT QUOTE GROUNDING / NO UNSOURCED SPECIFICS rules "
        "from your instructions still apply. Return the full revised article as "
        f"the same JSON object.{carried_block}"
    )


# The exact note_failure() reason strings for _attempt_revision's two
# TECHNICAL failure modes -- distinguished from "revision call failed: <exc>"
# (a real API/network error, possibly a sustained outage/rate limit already
# exhausted upstream, not obviously a one-off). _attempt_revision_with_retry
# matches these to decide whether a failed attempt is worth one retry
# (2026-08-28 audit): the tool loop can burn its whole
# WRITER_REVISION_TOOL_MAX_ROUNDS budget deep in research and never emit a
# final JSON answer, or return a structurally-valid-but-blank completion --
# both read as one-off completion glitches, not evidence revision won't work
# here, unlike a real API problem.
_REVISION_PARSE_FAILURE_REASON = "revision (tool-enabled) did not return a valid JSON object"
_REVISION_EMPTY_BODY_REASON = "revision returned an empty body"
_RETRYABLE_REVISION_FAILURES = frozenset(
    {_REVISION_PARSE_FAILURE_REASON, _REVISION_EMPTY_BODY_REASON}
)


def _compact_revision_prior(current: dict) -> list[dict[str, Any]]:
    """The bounded prior-context turn for the tool-enabled revision pass's chat_with_tools call (2026-09-06 cost fix, operator-approved).

    Before this, the revision pass's outgoing request carried the FULL
    accumulated transcript (every Stage-1 research round, digest synthesis,
    gap-fill, the Stage-2 write) via chat_with_tools' default
    `_merged_convo_with_prior_debug` merge — measured on a real compose
    tonight at ~65% of the whole compose's real (uncached) token spend.
    `revise_user` (`_build_revision_prompt`, built from `gen_user` =
    `_build_stage2_user`) already restates the digest and the specific
    flagged issues; the one thing it does NOT restate is the article the
    earlier stage(s) actually produced, which the reviser previously only
    ever saw via the raw transcript's own last assistant turn. This
    reconstructs that turn directly from `current` instead, so the reviser
    keeps everything it needs to actually revise (digest + draft + flagged
    issues) without paying to resend every research tool call that produced
    the digest — the reviser keeps its own tool access to re-fetch anything
    a flagged issue needs fresh.

    Internal bookkeeping keys (leading "_", e.g. `_heuristic_grade`,
    stashed onto the dict by `_run_grade_revise_loop` AFTER the fact) are
    dropped — they were never part of the model's own completion, so
    echoing them back would be fabricated history, not a faithful stand-in
    for the real prior turn.
    """
    draft = {k: v for k, v in current.items() if not k.startswith("_")}
    return [{"role": "assistant", "content": json.dumps(draft)}]


def _attempt_revision(
    llm: MistralProvider,
    gen_system: str,
    revise_user: str,
    *,
    temperature: float,
    note_failure: Callable[[str, str], None],
    tool_schemas: list[dict] | None = None,
    tool_handlers: dict | None = None,
    trace: list[dict] | None = None,
    debug: dict | None = None,
    on_round: Callable[[], None] | None = None,
    prior_override: list[dict] | None = None,
) -> dict | None:
    """Call the reviser. Returns the revised fields, or None — having already called note_failure — if the call failed or came back empty.

    ``on_round``, when the tool-enabled path fires, checkpoints compose_sessions
    live the same way the research stage does (see chat_with_tools' own
    docstring) -- without it, a revision pass that spends its whole
    WRITER_REVISION_TOOL_MAX_ROUNDS budget looks identical to a hang from the
    admin Sessions view (root-caused 2026-09-05: the grade/revise loop had NO
    checkpoints at all, so a revision-heavy compose could sit past
    COMPOSE_SESSION_STALE_MINUTES with its status frozen at "writing" and get
    incorrectly reaped as stale mid-run even after reap_stale_compose_sessions
    started keying off the last checkpoint instead of created_at).

    This builds only its OWN short turn (gen_system + revise_user). When
    `prior_override` is given (the normal case now, see
    `_compact_revision_prior`), chat_with_tools seeds its OUTGOING request
    with just that bounded prior instead of the full accumulated
    `debug["messages"]` transcript (2026-09-06 cost fix — see
    `_merged_convo_with_prior_debug`'s docstring); `debug["messages"]`
    itself still accumulates this pass's real turns in full for the admin
    Sessions view. `prior_override=None` falls back to the old full-merge
    behavior (every earlier stage of this same compose still uses it
    unchanged), provided `debug` is the same dict object threaded through
    the whole compose (true for every real caller — see
    `_compose_via_writer_tools_locked`).

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

    tool_schemas/handlers (2026-08-11): when given (and non-empty), the
    reviser gets a bounded WRITER_REVISION_TOOL_MAX_ROUNDS tool-call budget
    instead of a single tool-less completion — a flagged issue like "this
    figure looks stale" or "dead link, no replacement in the digest" is
    exactly the kind a fresh fetch_url/search_web/chain-tool call can
    actually fix, not just reword. Falls back to the old tool-less
    chat_json_object path when no tools are given.
    """
    from app.modules.ai.llm_openai_compatible import _parse_json_object

    messages = [
        {"role": "system", "content": gen_system},
        {"role": "user", "content": revise_user},
    ]
    try:
        if tool_schemas and tool_handlers:
            from app.core.config import WRITER_REVISION_TOOL_MAX_ROUNDS

            raw = llm.chat_with_tools(
                messages,
                tools=tool_schemas,
                handlers=tool_handlers,
                trace=trace,
                debug=debug,
                temperature=temperature,
                require_tool=None,
                max_rounds=WRITER_REVISION_TOOL_MAX_ROUNDS,
                on_round=on_round,
                prior_override=prior_override,
            )
            revised = _parse_json_object(raw)
            if revised is None:
                # Preserve what the model actually returned, not just the fixed
                # reason string -- previously discarded entirely, leaving no way
                # to diagnose WHY a parse failed after the fact (2026-08-28
                # audit finding: the broken output itself was never saved).
                note_failure(_REVISION_PARSE_FAILURE_REASON, raw)
                return None
        else:
            revised = llm.chat_json_object(messages, temperature=temperature)
    except Exception as exc:
        note_failure(f"revision call failed: {type(exc).__name__}: {exc}", "")
        return None
    if not str(revised.get("body", "") or "").strip():
        note_failure(_REVISION_EMPTY_BODY_REASON, "")
        return None
    return revised


def _attempt_revision_with_retry(
    llm: MistralProvider,
    gen_system: str,
    revise_user: str,
    *,
    temperature: float,
    note_failure: Callable[[str, str], None],
    tool_schemas: list[dict] | None = None,
    tool_handlers: dict | None = None,
    trace: list[dict] | None = None,
    debug: dict | None = None,
    on_round: Callable[[], None] | None = None,
    prior_override: list[dict] | None = None,
) -> dict | None:
    """_attempt_revision, with one immediate retry on a TECHNICAL failure.

    The retry fires only when the only reason the call failed is one of
    _RETRYABLE_REVISION_FAILURES (2026-08-28 audit): the tool-enabled
    reviser's final output not parsing as JSON (the tool loop can burn its
    whole WRITER_REVISION_TOOL_MAX_ROUNDS budget deep in research and never
    emit a final JSON answer) or an empty body (a structurally valid
    completion with nothing usable in it) -- both read as one-off completion
    glitches, not evidence revision won't work here. A real "revision call
    failed: <exc>" (API/network error -- may well be a sustained outage/rate
    limit already exhausted upstream, not a one-off) is left as-is, same as
    before this retry was added.

    Both the original failure and, if it also fails, the retry are recorded
    via note_failure — nothing here suppresses that visibility. The caller's
    WRITER_REVISION_MAX_PASSES bookkeeping is untouched: this whole function
    is one call from that budget's point of view, whether it took one attempt
    or two.
    """
    seen_reasons: list[str] = []

    def _capture(reason: str, raw: str) -> None:
        seen_reasons.append(reason)
        note_failure(reason, raw)

    revised = _attempt_revision(
        llm,
        gen_system,
        revise_user,
        temperature=temperature,
        note_failure=_capture,
        tool_schemas=tool_schemas,
        tool_handlers=tool_handlers,
        trace=trace,
        debug=debug,
        on_round=on_round,
        prior_override=prior_override,
    )
    if revised is None and seen_reasons and seen_reasons[-1] in _RETRYABLE_REVISION_FAILURES:
        revised = _attempt_revision(
            llm,
            gen_system,
            revise_user,
            temperature=temperature,
            note_failure=_capture,
            tool_schemas=tool_schemas,
            tool_handlers=tool_handlers,
            trace=trace,
            debug=debug,
            on_round=on_round,
            prior_override=prior_override,
        )
    return revised


def _run_grade_revise_loop(
    llm: MistralProvider,
    payload: dict,
    quality_llm: MistralProvider,
    *,
    system: str,
    gen_user: str,
    trace: list[dict],
    debug: dict | None,
    user: str,
    research_user: str | None,
    is_special_edition: bool,
    max_revisions: int,
    revision_tool_schemas: list[dict] | None,
    revision_tool_handlers: dict | None,
    checkpoint: Callable[..., None] | None = None,
    check_completeness_gate: bool = False,
) -> dict:
    """The grade -> (maybe revise) -> re-grade loop itself, factored out of _review_and_revise (which just builds quality_llm, runs this, and merges quality_llm's usage) so that caller stays under the 150-line budget -- see _review_and_revise's own docstring for the algorithm this implements unchanged.

    ``check_completeness_gate`` (2026-09-09): opt-in, forwarded to
    `_collect_fixable_issues` -- see that function's own docstring. Only set
    True for fresh content; recompose deliberately excludes completeness.

    ``checkpoint``, when given, is called with status="writing" once per
    grade/revise pass (plus once per round of the revision tool loop itself,
    via _attempt_revision_with_retry's ``on_round``). Before this
    (root-caused 2026-09-05), the ONLY "writing" checkpoint fired once, right
    before Stage 2 generation started -- the entire grade/revise loop after
    that (up to WRITER_REVISION_MAX_PASSES full revision passes, each with
    its own grading call and a bounded tool-calling budget) ran with zero
    further checkpoints, so compose_sessions.duration_ms/status went stale
    the moment generation finished, and a long revision looked identical to
    a hung compose from the admin Sessions view -- the exact "the status
    flag is bad" complaint this addresses.
    """
    from app.core.config import LLM_TEMP_WRITE, WRITER_QUALITY_LLM_MIN_SCORE
    from app.modules.newspaper.article_quality_llm import (
        factcheck_needs_revision,
        quality_needs_revision,
    )

    def _note_revision_failure(reason: str, raw: str = "") -> None:
        # Surface WHY the revision didn't happen instead of silently keeping the
        # weak draft — otherwise a rate-limited/failed revision is invisible and
        # looks like "the grade changed nothing".
        result: dict = {"error": reason[:300]}
        if raw:
            # The model's actual (broken) output, not just the fact that it
            # didn't parse -- previously discarded outright (2026-08-28 audit).
            result["raw_output"] = raw[:2000]
        args = {"revision": "failed"}
        trace.append({"tool": "review_draft", "arguments": args, "result": result})
        _debug_tool_turn(debug, "review_draft", args, result)

    current = payload
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

        if checkpoint is not None:
            checkpoint(
                "writing",
                detail=(
                    f"grade/revise pass {revise_count} of {max_revisions}"
                    if revise_count
                    else "grading initial draft"
                ),
            )

        review = _grade_current_draft(
            title, summary, body, quality_llm, is_special_edition=is_special_edition, debug=debug
        )
        quality = review["quality"]
        factcheck = review["factcheck"]
        _record_grade(
            trace, debug, current, review, title=title, body=body, revise_count=revise_count
        )

        needs_revision = quality_needs_revision(quality, min_score=WRITER_QUALITY_LLM_MIN_SCORE)
        fixable, localized_fixable = _collect_fixable_issues(
            review,
            quality,
            needs_revision=needs_revision,
            title=title,
            body=body,
            trace=trace,
            gen_user=gen_user,
            user=user,
            research_user=research_user,
            link_check_cache=link_check_cache,
            chain_check_cache=chain_check_cache,
            factcheck=factcheck,
            check_completeness_gate=check_completeness_gate,
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

        # ever_raised still holds whatever was flagged BEFORE this pass (this
        # pass's own `fixable` hasn't been folded in yet below), i.e. exactly
        # the issue(s) the just-attempted revision was supposed to fix.
        regrade_unconfirmed = _regrade_is_unconfirmed(revise_count, review)

        if not fixable:
            _maybe_stamp_regrade_unconfirmed(
                best_current, review, ever_raised, unconfirmed=regrade_unconfirmed
            )
            return best_current
        if revise_count >= max_revisions:
            # Out of revisions — return the BEST pass seen, not necessarily
            # this last one (see best-of-N note above).
            _maybe_stamp_regrade_unconfirmed(
                best_current,
                review,
                ever_raised | set(fixable),
                unconfirmed=regrade_unconfirmed,
            )
            return best_current

        already_fixed = sorted(ever_raised - set(fixable))
        ever_raised.update(fixable)
        too_long = any(i.startswith("too long") for i in fixable)
        needs_depth = needs_revision
        # A WRONG factcheck verdict gets the narrow surgical-correction
        # instruction ONLY when it's the sole trigger -- if the style rubric
        # ALSO flagged this pass (needs_depth True), that broader instruction
        # already covers fixing everything flagged, factual concerns included
        # (see _revision_length_rule's own docstring).
        needs_factual_fix = factcheck_needs_revision(factcheck) and not needs_depth
        revise_user = _build_revision_prompt(
            gen_user,
            fixable,
            already_fixed,
            too_long=too_long,
            needs_depth=needs_depth,
            needs_factual_fix=needs_factual_fix,
            localized_fixable=localized_fixable,
        )
        gen_system = system + _STAGE2_GENERATION_GUIDANCE

        # Bounded prior context (2026-09-06 cost fix): the reviser's own
        # outgoing request gets just the current draft standing in for the
        # full accumulated research/generation transcript -- revise_user
        # already restates the digest and the flagged issues above, so the
        # draft is the only missing piece. See _compact_revision_prior.
        revision_prior = _compact_revision_prior(current)

        # _attempt_revision_with_retry gives one immediate retry of this exact
        # call on a technical failure (JSON-parse or empty-body) -- see its
        # docstring. That retry doesn't touch revise_count below: it's
        # recovering from a failed ATTEMPT, not spending one of the real
        # WRITER_REVISION_MAX_PASSES passes, so a later genuinely-new
        # revision pass still gets its own full budget.
        revised = _attempt_revision_with_retry(
            llm,
            gen_system,
            revise_user,
            temperature=LLM_TEMP_WRITE,
            note_failure=_note_revision_failure,
            tool_schemas=revision_tool_schemas,
            tool_handlers=revision_tool_handlers,
            trace=trace,
            debug=debug,
            on_round=(lambda: checkpoint("writing")) if checkpoint is not None else None,
            prior_override=revision_prior,
        )
        if revised is None:
            return best_current

        # Default to this pass's grade if the next pass's regrade itself fails,
        # so the floor-gate downstream never sees revision as erasing a known
        # grade. The loop's next iteration overwrites this with the real regrade.
        revised["_heuristic_grade"] = review
        current = revised
        revise_count += 1


def _review_and_revise(
    llm: MistralProvider,
    payload: dict,
    *,
    system: str,
    gen_user: str,
    trace: list[dict],
    debug: dict | None = None,
    user: str = "",
    research_user: str | None = None,
    is_special_edition: bool = False,
    revision_tool_schemas: list[dict] | None = None,
    revision_tool_handlers: dict | None = None,
    extra_usage: dict[str, int] | None = None,
    checkpoint: Callable[..., None] | None = None,
    check_completeness_gate: bool = False,
) -> dict:
    """Stage 3+4 of two-stage compose: grade the draft, then revise if weak.

    ``check_completeness_gate`` (2026-09-09): forwarded to
    `_run_grade_revise_loop` -- opt-in, only set True for fresh content.

    The warm generation pass runs with NO tools, so the model cannot call
    review_draft itself — we run the heuristic grader deterministically here and,
    on a sub-threshold grade or any listed issues, revise with the concrete
    issues fed back, up to WRITER_REVISION_MAX_PASSES times (a pass that comes
    back clean stops the loop early — most drafts never need a second). Every
    grading is recorded in the trace like review_draft tool calls so
    telemetry/insights see them. The loop itself lives in
    _run_grade_revise_loop; this function's own job is building quality_llm,
    running that loop, and merging quality_llm's usage when it's done.

    revision_tool_schemas/handlers (2026-08-11, owner request): the revision
    call itself DOES get tool access (same research toolset as stage 1,
    minus review_draft) — a flagged issue is often exactly the kind a fresh
    tool call could resolve (an unverified claim, a stale figure, a dead
    link with a findable replacement), not just a reorganize-the-prose job.
    None/empty falls back to the old tool-less behavior (e.g. a caller that
    hasn't wired tools through, or WRITER_TOOLS_ENABLED off upstream).

    `extra_usage`, if given, receives quality_llm's cumulative usage_totals()
    once the loop is done -- one instance grades every pass (including the
    final re-grade after the last revision), so a single merge at the end
    captures the whole loop's rubric spend, which otherwise never reaches the
    compose's own accounting (2026-08-28 audit; see _merge_usage).

    `checkpoint`, if given, is forwarded to _run_grade_revise_loop -- see
    that function's docstring for why this loop needed its own checkpoints
    (2026-09-05) rather than relying on the single "writing" checkpoint fired
    once before Stage 2 generation.
    """
    from app.core.config import WRITER_REVIEW_ENABLED, WRITER_REVISION_MAX_PASSES

    if not WRITER_REVIEW_ENABLED:
        return payload

    # LLM rubric grading (narrative synthesis/technical depth/critical
    # distance) is a judgment task, not generation — it doesn't need the
    # writer's Large-tier model, and (2026-08-06) it's its own routing
    # purpose so a compose can send its research tool loop to one provider
    # while grading with another (e.g. DeepSeek research, Mistral rubric).
    quality_llm = get_llm_rubric_client()
    try:
        return _run_grade_revise_loop(
            llm,
            payload,
            quality_llm,
            system=system,
            gen_user=gen_user,
            trace=trace,
            debug=debug,
            user=user,
            research_user=research_user,
            is_special_edition=is_special_edition,
            max_revisions=max(1, WRITER_REVISION_MAX_PASSES),
            revision_tool_schemas=revision_tool_schemas,
            revision_tool_handlers=revision_tool_handlers,
            checkpoint=checkpoint,
            check_completeness_gate=check_completeness_gate,
        )
    finally:
        # Cumulative across every pass the loop ran (including the final
        # re-grade after the last revision) -- quality_llm is the SAME
        # instance for the whole loop, so one merge here is correct and
        # cannot double-count (see _merge_usage's docstring).
        _merge_usage_from(extra_usage, quality_llm)


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
#
# Deliberately SHORT (2026-08-05, owner request) — a full-length worked
# example teaches its own structure just as hard as its intended format, and
# that's not hypothetical: an earlier version of this example used a
# colon-label headline and it "made every published headline a colon label"
# (see the NOTE below). The same full 6-section arc this example used to have
# (problem -> solution -> pricing table -> "Technical Foundation and
# Philosophy" -> "Ecosystem Context" -> team) is suspected of the SAME
# mechanism showing up differently: a live article on an unrelated topic
# (Hampelman NFTs) grew its own "Algorand's Infrastructure: The Technical
# Backbone" section, echoing this example's technical-section beat rather
# than responding to what that story actually needed. Trimmed to the minimum
# that still demonstrates the shape -- one flowing opening (not a separate
# header per beat), one small table, one first-mention link, one closing
# Source list -- so there's less rigid structure left to imitate wholesale.
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
        "## Free, Global Node Access — No Vendor Lock-In\n\n"
        "Developers building on Algorand have long faced a tradeoff: run a "
        "resource-intensive node themselves, or rely on a third-party "
        "provider that risks latency spikes or restrictive terms. "
        "[Nodely](https://nodely.io/) closes that gap with a globally "
        "distributed network of nodes and indexers, now handling 115M+ "
        "daily API requests across 20+ locations — with a production-ready "
        "free tier, not just a trial.\n\n"
        "## What The Free Tier Actually Includes\n\n"
        "| Tier | Price | Throughput | Notable Features |\n"
        "|------|-------|------------|-------------------|\n"
        "| Free | 0 ALGO/forever | 60 req/s per browser | IPFS Gateway, no "
        "keys needed |\n"
        "| Unlimited | $256/month | 6000 req/s per key/site | Full API at "
        "25 locations |\n\n"
        "Built on vanilla open-source node and indexer APIs, the stack "
        "avoids vendor lock-in — projects can migrate to self-hosting "
        "whenever they’re ready.\n\n"
        "## Source\n"
        "- [Nodely](https://nodely.io/)\n"
        "- [AlgoNode GitHub](https://github.com/algonode)"
    ),
}
_GOOD_EXAMPLE = (
    "\n\nWORKED EXAMPLE (a real published article, trimmed for length — "
    "study its structure, data presentation and sourcing; never reuse its "
    "topic, facts, specific phrasing, or exact section count/arc for an "
    "unrelated story):\n"
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
    "of it ('Algorand Replaces Legacy Pages "
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
    "  - SCANNABILITY: Break up the text with descriptive Markdown headers (## and ###) "
    "that say what the section contains in plain words (per PLAIN REGISTER — "
    "a header is a label, not a tease), so a reader scanning on a phone can "
    "find the part they want. (A very short update needs none.)\n"
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
    "\n\nFIRST COVERAGE MODE: readers should come away with a complete, standalone "
    "picture of this service, not an assumption they already know the basics. Do "
    "NOT write a 'what changed' update centered on one recent detail: a narrow "
    "update is meaningless without the fuller context around it, and a cosmetic "
    "change is not a story at all. Write an INTRODUCTION/PROFILE of the service — "
    "what it is, what problem it solves, who is behind it, how it fits the Algorand "
    "ecosystem — in timeless present tense, using your research tools to verify. "
    "Introduce it at the depth the sources actually document: if its docs are "
    "missing or stubs, say so instead of reconstructing how it must work. "
    "A single recent change/feature may be mentioned as a closing note at most, "
    "never as the whole piece's frame (a marketplace with auctions, raffles, "
    "staking, swaps and listings in the research is not a piece about its one "
    "newly-previewed feature). If the change itself is the only material and the service is not "
    "worth introducing, keep the piece short and factual rather than inflating it."
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


def _clip(text: str, limit: int = LLM_MAX_SOURCE_CHARS) -> str:
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


def _parse_article_fields(payload: dict[str, Any]) -> LLMArticleFields:
    title = str(payload.get("title") or "").strip()
    summary = str(payload.get("summary") or "").strip()
    body = _coerce_markdown(payload.get("body")).strip()
    if not title or not summary or not body:
        raise LLMError("LLM JSON missing title, summary, or body")
    raw_tags = payload.get("tags") or []
    tags: list[str] = []
    if isinstance(raw_tags, list):
        for t in raw_tags:
            slug = str(t).strip().lower().replace(" ", "-")
            slug = "".join(ch for ch in slug if ch.isalnum() or ch == "-").strip("-")
            if slug and slug not in tags:
                tags.append(slug)
    return LLMArticleFields(
        title=title,
        summary=summary,
        body=body,
        tags=tuple(tags[:6]),
        heuristic_grade=payload.get("_heuristic_grade"),
        confirmed_alert=payload.get("_confirmed_alert"),
        defunct_domains=tuple(payload.get("_defunct_domains") or ()),
        unsourced_hold_reason=str(payload.get("_unsourced_hold_reason") or ""),
        broken_link_hold_reason=str(payload.get("_broken_link_hold_reason") or ""),
        regrade_unconfirmed_hold_reason=str(payload.get("_regrade_unconfirmed_hold_reason") or ""),
    )


def _admin_source_prompt_block(admin_sources: list[AdminSource] | None) -> str:
    """The "## OWNER-SUPPLIED SOURCE MATERIAL" block appended after scraped source material (design doc section 3). Clipped as a WHOLE block to ADMIN_SOURCE_PROMPT_MAX_CHARS -- separate from LLM_MAX_SOURCE_CHARS, so owner material never competes with the scrape for space. Empty string when there are no active sources (the normal, silent no-sources case)."""
    if not admin_sources:
        return ""
    sections = "\n\n".join(
        f"### {s.label} (added {s.added_at.date().isoformat() if s.added_at else 'unknown date'})\n"
        f"{s.content}"
        for s in admin_sources
    )
    block = (
        "\n\n## OWNER-SUPPLIED SOURCE MATERIAL (exclusive to this newspaper)\n"
        "The publisher personally obtained the material below; it is not on the "
        "public web. Treat it as a primary source. Attribute claims drawn from it "
        'to its stated provenance (e.g. "told PXke Algorand in an interview"), '
        "never to the service's website. Judge its age against today's date.\n\n"
        f"{sections}"
    )
    return _clip(block, ADMIN_SOURCE_PROMPT_MAX_CHARS)


def _seed_admin_source_trace(trace: list[dict], admin_sources: list[AdminSource] | None) -> None:
    """Append one synthetic ``admin_supplied_source`` trace entry per source, BEFORE the research loop starts, chunked at INVESTIGATION_RESULT_MAX_CHARS with ``part N/M`` markers in ``arguments`` (design doc section 3).

    This is what lets a figure the writer takes from the interview become a
    grounded anchor for ``numeric_entailment_score`` instead of being flagged
    as fabricated (design doc section 0b) -- seeding the live session
    ``trace`` list gets this stored via the normal
    ``_record_compose_telemetry`` -> ``store_investigation_findings`` path
    for free, with zero gatekeeper changes.

    The tool name ``admin_supplied_source`` is reserved and must never be a
    real writer tool name: no completeness rule
    (``gatekeeper/completeness.py``) accepts it, on purpose (design doc
    section 0a) -- this is honestly-labeled owner-pasted text, never a forged
    stand-in for a verification tool actually running.
    """
    if not admin_sources:
        return
    from app.core.config import INVESTIGATION_RESULT_MAX_CHARS

    for source in admin_sources:
        content = source.content or ""
        chunks = [
            content[i : i + INVESTIGATION_RESULT_MAX_CHARS]
            for i in range(0, len(content), INVESTIGATION_RESULT_MAX_CHARS)
        ] or [""]
        total = len(chunks)
        for i, chunk in enumerate(chunks, start=1):
            trace.append(
                {
                    "tool": "admin_supplied_source",
                    "arguments": {
                        "label": source.label,
                        "added_at": source.added_at.isoformat() if source.added_at else "",
                        "attribution_url": source.attribution_url,
                        "part": f"{i}/{total}",
                    },
                    "result": {"text": chunk},
                }
            )


def compose_scrape_article(
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
    admin_sources: list[AdminSource] | None = None,
    client: LLMProvider | None = None,
    research_client: LLMProvider | None = None,
    session_register: SessionRegister | None = None,
    check_completeness_gate: bool = False,
) -> LLMArticleFields:
    """Generate newspaper article fields from scrape context via the writer's research -> compose -> grade/revise loop.

    ``check_completeness_gate`` (2026-09-09): set True only by callers
    composing genuinely fresh, zero-prior-vetting content -- feeds the
    gatekeeper completeness rules (human_identity/company_backing) into the
    in-loop revision pass so a missing due-diligence tool call gets one
    chance at self-correction before the post-hoc hold. Never set True for a
    recompose call site -- completeness is deliberately excluded there (see
    `_grade_and_gate`'s docstring in publish_tasks.py, owner-confirmed
    2026-07-12).

    ``admin_sources`` (2026-09-02, owner-supplied article sources -- see
    docs/newspaper-article-sources-design.md): owner-attached evidence for
    THIS article (an exclusive interview transcript, extra data the public
    web can't provide). When present, two things happen TOGETHER, always
    (design doc section 3's own invariant): a labeled "OWNER-SUPPLIED SOURCE
    MATERIAL" block is appended to the user prompt after the scraped source
    material (clipped as a whole to ``ADMIN_SOURCE_PROMPT_MAX_CHARS``, kept
    separate from ``LLM_MAX_SOURCE_CHARS`` so owner material never competes
    with the scrape for space), AND the session trace is seeded with one
    ``admin_supplied_source`` entry per source (chunked at
    ``INVESTIGATION_RESULT_MAX_CHARS``) before the research loop starts, so a
    figure the writer quotes from the interview has a real trace anchor for
    the gatekeeper's ``numeric_entailment_score`` instead of being flagged as
    fabricated. Defaults to ``None``, so every existing call site (fresh
    publishes, briefs, benchmarks) is unaffected.

    ``research_client``/``session_register`` (2026-08-14): override the
    stage-1 research client and/or the compose-session transcript sink used
    for this one call. Both default to None, which resolves to today's exact
    production behavior (``get_llm_research_client()`` /
    ``SessionRegisterCassandra()``) -- added so a standalone benchmark
    caller (compose_runner.py) can plug in a different provider and a local
    file-backed register without a queue/Celery/publish coupling, while
    every existing production call site (which never passes these) is
    unaffected.

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
    llm = client or get_llm_writer_client()
    today = _today_utc()
    source_domain = (urlparse(source_url).netloc or "").lower()
    links_block = _source_links_block(source_links)
    admin_block = _admin_source_prompt_block(admin_sources)
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
{_clip(enrichment_block, 5000) if enrichment_block else ""}{admin_block}"""
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
{_clip(enrichment_block, 5000) if enrichment_block else ""}{admin_block}"""

    from app.core.config import LLM_RESEARCH_SOURCE_CHARS

    user = _build_user(LLM_MAX_SOURCE_CHARS)
    # Research rounds re-send the whole prompt every round — give them a
    # smaller source clip (they decide what to verify, they don't write from
    # it); the full clip rides only in the single stage-2 generation call.
    research_user = (
        _build_user(LLM_RESEARCH_SOURCE_CHARS)
        if len(page_text) > LLM_RESEARCH_SOURCE_CHARS
        else user
    )

    return _call_compose_via_writer_tools(
        system=system,
        user=user,
        research_user=research_user,
        source_url=source_url,
        llm=llm,
        topic=publish_topic,
        research_client=research_client,
        session_register=session_register,
        admin_sources=admin_sources,
        check_completeness_gate=check_completeness_gate,
    )


def _call_compose_via_writer_tools(**kwargs: object) -> LLMArticleFields:
    """Invoke the shared compose loop, omitting kwargs older workers may lack.

    Rolling deploys can briefly load a ``compose_scrape_article`` that
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
    llm: LLMProvider,
    research_user: str | None = None,
    is_special_edition: bool = False,
    research_client: LLMProvider | None = None,
    session_register: SessionRegister | None = None,
    admin_sources: list[AdminSource] | None = None,
    check_completeness_gate: bool = False,
) -> LLMArticleFields:
    """Shared research -> write -> grade/revise loop behind every writer-tools compose path. Only depends on the system/user prompt pair and a label (``source_url``) used for tool scoping and session/investigation bookkeeping — it doesn't assume the source material was a real scraped page, so callers can feed it a from-scratch topic assignment just as well as a scrape diff.

    ``research_user``: optional slimmer variant of ``user`` (smaller source
    clip) for the stage-1 research rounds, which re-send the whole prompt on
    every tool round; stage-2 generation always uses the full ``user``.
    Defaults to ``user``.

    ``is_special_edition``: quadruples the stage-1 research round budget
    (LLM_MAX_TOOL_ROUNDS) for a genuinely deeper investigation, on top
    of the prompt's own depth instructions.

    ``research_client``/``session_register``/``admin_sources``: see compose_scrape_article's docstring.

    ``check_completeness_gate`` (2026-09-09): forwarded to the grade/revise
    loop -- opt-in, only set True by callers composing genuinely fresh
    content (see compose_scrape_article/compose_assignment_article).
    """
    from app.modules.newspaper.compose_lock import compose_lock

    with compose_lock(label=source_url):
        return _compose_via_writer_tools_locked(
            system=system,
            user=user,
            source_url=source_url,
            llm=llm,
            research_user=research_user,
            is_special_edition=is_special_edition,
            research_client=research_client,
            session_register=session_register,
            admin_sources=admin_sources,
            check_completeness_gate=check_completeness_gate,
        )


def _run_research_floor(
    research_llm: MistralProvider,
    system: str,
    stage1_user: str,
    research_schemas: list[dict],
    research_handlers: dict,
    trace: list,
    debug: dict,
    *,
    is_special_edition: bool = False,
    checkpoint: Callable[[str], None] | None = None,
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
        LLM_TEMP_RESEARCH,
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
    max_passes = RESEARCH_FLOOR_MAX_PASSES * 4 if is_special_edition else RESEARCH_FLOOR_MAX_PASSES
    for _ in range(max(0, max_passes)):
        have = _distinct_research_calls(trace)
        if have >= min_calls:
            break
        nudge = _research_floor_nudge(have, min_calls, _format_research_digest(trace))
        research_llm.chat_with_tools(
            [
                {"role": "system", "content": system + _research_phase_guidance(trace)},
                {"role": "user", "content": stage1_user + nudge},
            ],
            tools=research_schemas,
            handlers=research_handlers,
            trace=trace,
            debug=debug,
            temperature=LLM_TEMP_RESEARCH,
            require_tool=None,
            finalize_on_exhaustion=False,
            on_round=(lambda: checkpoint("researching")) if checkpoint else None,
            show_round_budget=True,
        )


def _run_digest_gap_fill(
    research_llm: MistralProvider,
    system: str,
    stage1_user: str,
    research_schemas: list[dict],
    research_handlers: dict,
    trace: list,
    debug: dict,
    digest: str,
    *,
    checkpoint: Callable[[str], None] | None = None,
    extra_usage: dict[str, int] | None = None,
) -> str:
    """Gap-fill: the digest may flag specific unresolved-but-material gaps. Give the model ONE bounded extra research pass targeting exactly those before handing off to the tool-less writer, which otherwise either omits the gap (fine) or invents/recalls something to fill it. Re-synthesizes the digest afterward so the writer sees whatever was actually found (or an honest "still unresolved") rather than the pre-gap-fill digest. `extra_usage` is forwarded to that re-synthesis call -- see _merge_usage."""
    from app.core.config import (
        DIGEST_GAP_FILL_ENABLED,
        DIGEST_GAP_FILL_MAX_ROUNDS,
        LLM_TEMP_RESEARCH,
    )

    if not DIGEST_GAP_FILL_ENABLED:
        return digest
    gaps = _extract_unresolved_gaps(digest)
    if not gaps:
        return digest
    research_llm.chat_with_tools(
        [
            {"role": "system", "content": system + _research_phase_guidance(trace)},
            {"role": "user", "content": stage1_user + _gap_fill_nudge(gaps)},
        ],
        tools=research_schemas,
        handlers=research_handlers,
        trace=trace,
        debug=debug,
        temperature=LLM_TEMP_RESEARCH,
        require_tool=None,
        max_rounds=DIGEST_GAP_FILL_MAX_ROUNDS,
        # The 2026-07-14 gap-fill pass ran out of rounds here and burned a
        # full discarded article write.
        finalize_on_exhaustion=False,
        on_round=(lambda: checkpoint("researching")) if checkpoint else None,
        show_round_budget=True,
    )
    return _synthesize_research_digest(
        trace=trace,
        research_context=stage1_user,
        provider=research_llm.provider,
        extra_usage=extra_usage,
        debug=debug,
    )


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


def _run_entity_enumeration(
    *,
    trace: list[dict],
    digest: str,
    extra_usage: dict[str, int] | None = None,
    debug: dict | None = None,
) -> str:
    """Structured People/Places/Dates/Services/Numbers accounting, synthesized from the trace + digest already gathered. Same lightweight digest-tier client as _synthesize_research_digest -- this is synthesis over already-fetched material, not new research. Empty trace or any failure yields "" (caller treats that as no enumeration available, never a hard failure). `extra_usage`, if given, gets this ephemeral client's real spend merged in even on failure -- see _merge_usage."""
    raw_trace = _format_research_digest(trace)
    if not raw_trace.strip():
        return ""
    digest_client = get_llm_digest_client()
    started_at_ms = _now_ms()
    try:
        from app.core.config import LLM_TEMP_RESEARCH

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
            temperature=LLM_TEMP_RESEARCH,
        )
        return (enumeration or "").strip()
    except Exception:
        logger.warning("entity enumeration failed; continuing without it", exc_info=True)
        return ""
    finally:
        _merge_usage_from(extra_usage, digest_client)
        _note_llm_call(debug, "entity_enumeration", started_at_ms, _now_ms())


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
    research_llm: MistralProvider,
    system: str,
    stage1_user: str,
    research_schemas: list[dict],
    research_handlers: dict,
    trace: list,
    debug: dict,
    gaps: str,
    *,
    checkpoint: Callable[[str], None] | None = None,
) -> None:
    """One bounded extra tool-calling pass targeting the entity enumeration's own Coverage Gaps -- distinct from (and runs after) the plain digest gap-fill, since the enumeration surfaces gaps a prose digest's generic cap can miss entirely."""
    from app.core.config import (
        LLM_TEMP_RESEARCH,
        SPECIAL_EDITION_ENUMERATION_GAP_FILL_MAX_ROUNDS,
    )

    research_llm.chat_with_tools(
        [
            {"role": "system", "content": system + _research_phase_guidance(trace)},
            {"role": "user", "content": stage1_user + _enumeration_gap_fill_nudge(gaps)},
        ],
        tools=research_schemas,
        handlers=research_handlers,
        trace=trace,
        debug=debug,
        temperature=LLM_TEMP_RESEARCH,
        require_tool=None,
        max_rounds=SPECIAL_EDITION_ENUMERATION_GAP_FILL_MAX_ROUNDS,
        finalize_on_exhaustion=False,
        on_round=(lambda: checkpoint("researching")) if checkpoint else None,
        show_round_budget=True,
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


def _run_narrative_outline(
    *,
    digest: str,
    enumeration: str,
    extra_usage: dict[str, int] | None = None,
    debug: dict | None = None,
) -> str:
    """A concrete section-by-section plan for Stage 2 to write from, instead of synthesizing organization cold from a raw digest. Same lightweight digest-tier client as digest synthesis -- this is planning over already-gathered material, not new research. Empty on failure (caller treats a missing outline as "write from the digest alone," never a hard failure). `extra_usage`, if given, gets this ephemeral client's real spend merged in even on failure -- see _merge_usage."""
    if not digest.strip() and not enumeration.strip():
        return ""
    digest_client = get_llm_digest_client()
    started_at_ms = _now_ms()
    try:
        from app.core.config import LLM_TEMP_RESEARCH

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
            temperature=LLM_TEMP_RESEARCH,
        )
        return (outline or "").strip()
    except Exception:
        logger.warning("narrative outline synthesis failed; continuing without it", exc_info=True)
        return ""
    finally:
        _merge_usage_from(extra_usage, digest_client)
        _note_llm_call(debug, "narrative_outline", started_at_ms, _now_ms())


def _run_special_edition_deepening(
    research_llm: MistralProvider,
    system: str,
    stage1_user: str,
    research_schemas: list[dict],
    research_handlers: dict,
    trace: list,
    debug: dict,
    digest: str,
    *,
    checkpoint: Callable[[str], None] | None = None,
    extra_usage: dict[str, int] | None = None,
) -> tuple[str, str, str]:
    """Special-edition-only Stage 1c/1d: enumerate -> targeted gap-fill -> re-synthesize digest -> outline. Returns (digest, enumeration, outline); any disabled/failed step degrades gracefully (empty enumeration/outline, unchanged digest) rather than blocking the compose. `extra_usage` is forwarded to every ephemeral digest-tier call this makes (enumeration, re-synthesis, outline) -- see _merge_usage."""
    from app.core.config import SPECIAL_EDITION_OUTLINE_ENABLED

    if not SPECIAL_EDITION_OUTLINE_ENABLED:
        return digest, "", ""

    enumeration = _run_entity_enumeration(
        trace=trace, digest=digest, extra_usage=extra_usage, debug=debug
    )
    gaps = _extract_enumeration_gaps(enumeration) if enumeration else ""
    if gaps:
        _run_enumeration_gap_fill(
            research_llm,
            system,
            stage1_user,
            research_schemas,
            research_handlers,
            trace,
            debug,
            gaps,
            checkpoint=checkpoint,
        )
        digest = _synthesize_research_digest(
            trace=trace,
            research_context=stage1_user,
            provider=research_llm.provider,
            extra_usage=extra_usage,
            debug=debug,
        )
    outline = _run_narrative_outline(
        digest=digest, enumeration=enumeration, extra_usage=extra_usage, debug=debug
    )
    return digest, enumeration, outline


def _append_stage2_debug_turn(
    debug: dict,
    digest: str,
    payload: dict,
    *,
    started_at_ms: int | None = None,
    ended_at_ms: int | None = None,
) -> None:
    """The warm pass runs outside the tool loop, so its turn isn't in the debug transcript — add it so Sessions shows the draft. Store the ACTUAL digest text (not a placeholder): it's the only place to audit whether the research→write handoff (small-model synthesis) preserved or lost/garbled facts from the raw trace — previously this turn was a stub and the digest was never visible anywhere, so a bad handoff was undiagnosable after the fact.

    ``started_at_ms``/``ended_at_ms``: the write call's wall-clock window
    (measured around ``chat_json_object``, so it includes that call's own
    corrective retry when one fires), stamped onto the assistant turn the
    same way the tool loop stamps its rounds. The provider request path
    strips these local keys before any replay (_strip_local_annotations).
    """
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
    assistant_turn: dict = {"role": "assistant", "content": json.dumps(payload)[:4000]}
    if started_at_ms is not None:
        assistant_turn["started_at_ms"] = started_at_ms
    if ended_at_ms is not None:
        assistant_turn["ended_at_ms"] = ended_at_ms
    debug["messages"].append(assistant_turn)


def _run_stage1_cold_research(
    research_llm: MistralProvider,
    system: str,
    stage1_user: str,
    research_schemas: list[dict],
    research_handlers: dict,
    trace: list,
    debug: dict,
    *,
    max_rounds: int | None,
    checkpoint: Callable[[str], None],
) -> None:
    """Stage 1's cold research tool-calling pass, factored out of _run_two_stage_compose to keep that function under the 150-line budget.

    Tools available (minus review_draft, no draft yet), low temp for
    deterministic tool selection. Runs for its tool side-effects (the
    trace); the model's prose here is discarded — the return value is
    never used.
    """
    from app.core.config import LLM_TEMP_RESEARCH

    research_llm.chat_with_tools(
        [
            {"role": "system", "content": system + _research_phase_guidance(trace)},
            {"role": "user", "content": stage1_user},
        ],
        tools=research_schemas,
        handlers=research_handlers,
        trace=trace,
        debug=debug,
        temperature=LLM_TEMP_RESEARCH,
        require_tool=None,
        max_rounds=max_rounds,
        # Research runs for its tool side-effects (the trace); the
        # return value is discarded — never pay for a final
        # article completion on round exhaustion.
        finalize_on_exhaustion=False,
        on_round=lambda: checkpoint("researching"),
        show_round_budget=True,
    )


def _run_two_stage_compose(
    *,
    research_llm: MistralProvider,
    llm: MistralProvider,
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
    extra_usage: dict[str, int] | None = None,
    check_completeness_gate: bool = False,
) -> dict:
    """Two-stage compose: cold research (tools, low temp) on the Small research tier, a floor + gap-fill pass if it under-researched, a structured digest handoff, then a warm no-tools generation on the writer tier, and finally deterministic grade/revise.

    `extra_usage`, if given, accumulates every ephemeral rubric/digest-tier
    client's spend across this whole compose (digest synthesis, gap-fill
    re-synthesis, special-edition enumeration/outline, and the grade/revise
    loop's rubric grading) -- none of that reuses research_llm/llm, so
    nothing else accounts for it (2026-08-28 audit; see _merge_usage).

    ``check_completeness_gate`` (2026-09-09): forwarded to
    `_review_and_revise` -- opt-in, only set True for fresh content.
    """
    from app.core.config import LLM_TEMP_WRITE

    checkpoint("researching")
    # The model actually serving this session's research calls -- not the
    # Mistral-only config constant, which stays wrong the moment
    # LLM_PROVIDER_RESEARCH (or a canary roll) routes this session to
    # DeepSeek instead.
    debug["research_model"] = research_llm.model
    # Stage 1 — cold research. Research rounds re-send the whole conversation
    # every round, so they get the slimmer research_user when the caller
    # provided one. Runs on the Small research tier — better tool-calling,
    # cheaper per round.
    stage1_user = research_user or user
    research_schemas = [
        s for s in tool_schemas if (s.get("function") or {}).get("name") != "review_draft"
    ]
    research_handlers = {k: v for k, v in tool_handlers.items() if k != "review_draft"}
    _run_stage1_cold_research(
        research_llm,
        system,
        stage1_user,
        research_schemas,
        research_handlers,
        trace,
        debug,
        max_rounds=max_rounds,
        checkpoint=checkpoint,
    )
    _run_research_floor(
        research_llm,
        system,
        stage1_user,
        research_schemas,
        research_handlers,
        trace,
        debug,
        is_special_edition=is_special_edition,
        checkpoint=checkpoint,
    )
    # Stage 1b — synthesize a structured Research Digest handoff so Stage 2
    # grounds on high-signal facts, not raw tool JSON.
    digest = _synthesize_research_digest(
        trace=trace,
        research_context=stage1_user,
        provider=research_llm.provider,
        extra_usage=extra_usage,
        debug=debug,
    )
    digest = _run_digest_gap_fill(
        research_llm,
        system,
        stage1_user,
        research_schemas,
        research_handlers,
        trace,
        debug,
        digest,
        checkpoint=checkpoint,
        extra_usage=extra_usage,
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
            research_llm,
            system,
            stage1_user,
            research_schemas,
            research_handlers,
            trace,
            debug,
            digest,
            checkpoint=checkpoint,
            extra_usage=extra_usage,
        )
    checkpoint("writing", digest=digest)  # research (+ gap-fill/deepening) done, now generating
    gen_user = _build_stage2_user(
        user=user,
        digest=digest,
        is_special_edition=is_special_edition,
        enumeration=enumeration,
        outline=outline,
    )
    gen_system = system + _STAGE2_GENERATION_GUIDANCE
    write_started_at_ms = _now_ms()
    payload = llm.chat_json_object(
        [
            {"role": "system", "content": gen_system},
            {"role": "user", "content": gen_user},
        ],
        temperature=LLM_TEMP_WRITE,
    )
    _append_stage2_debug_turn(
        debug,
        digest,
        payload,
        started_at_ms=write_started_at_ms,
        ended_at_ms=_now_ms(),
    )
    # Stage 3+4 — deterministic grade, then one revision if weak. Revision
    # gets the same tool_schemas/handlers as research (minus review_draft,
    # same as stage 1) so a flagged issue that needs fresh data (an
    # unverified claim, a stale figure, a dead link with a findable
    # replacement) can actually be fixed, not just reworded from what
    # stage 1 already gathered.
    return _review_and_revise(
        llm,
        payload,
        system=system,
        gen_user=gen_user,
        trace=trace,
        debug=debug,
        user=user,
        research_user=research_user,
        is_special_edition=is_special_edition,
        revision_tool_schemas=research_schemas,
        revision_tool_handlers=research_handlers,
        extra_usage=extra_usage,
        checkpoint=checkpoint,
        check_completeness_gate=check_completeness_gate,
    )


def _apply_post_compose_gates(
    payload: dict,
    trace: list,
    *,
    user: str,
    research_user: str | None,
    service_id: str = "",
    glossary_client: MistralProvider | None = None,
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
    # Broken-link-claim gate (read-only for now): a body claim that a link/
    # page/feature is broken/404/doesn't work must be backed by an actual
    # click_element/play_interactive click attempt somewhere in the trace,
    # not just a guessed fetch_url — SPA "links" are often JS buttons with
    # real content behind them (lumirogue.com About/Terms footer, recurred
    # 2026-08-10 and again 2026-08-12 despite prompt-only guidance).
    from app.modules.newspaper.broken_link_claim_gate import flag_unverified_broken_link_claims

    payload = flag_unverified_broken_link_claims(payload, trace)
    # Stale-deadline backstop (read-only for now): any lapsed-deadline-framed-
    # as-open sentence the revision loop didn't catch (or that never went
    # through a revision loop at all, e.g. the article-edit path) is recorded
    # for visibility (Meld Gold 2026-08-04). See stale_deadline_gate.py.
    from app.modules.newspaper.stale_deadline_gate import stale_deadline_issues

    stale_deadlines = stale_deadline_issues(str(payload.get("body", "")))
    if stale_deadlines:
        payload["_stale_deadlines"] = stale_deadlines
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
    writer_model: str,
    duration_ms: int,
    session_id: UUID,
    created_at: datetime,
    debug: dict,
    usage_so_far: Callable[[], dict[str, int]],
    session_register: SessionRegister,
    digest: str = "",
) -> None:
    """Best-effort: store investigation findings and tool-insight telemetry for this compose session. Never raises — a telemetry failure must not fail the compose.

    `writer_model` must be the writer client's own resolved model (e.g.
    ``llm.model``), not a config constant — a canary/DeepSeek-routed call
    resolves to a different model than its purpose's configured default, and
    compose_sessions.model is the only record of which model actually wrote
    this article (root-caused 2026-08-05, alongside the provider-routing
    work: this was hardcoded to MISTRAL_MODEL_WRITER, so a canary call would
    have been mislabeled as Mistral in the one place an A/B comparison
    would look).
    """
    try:
        from app.modules.newspaper.investigation_store import store_investigation_findings

        store_investigation_findings(service_id=source_url, source_url=source_url, trace=trace)
    except Exception:
        logger.warning("failed to store investigation findings for %s", source_url, exc_info=True)
    try:
        from app.modules.ai.tool_insights_store import (
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
        session_register.upsert(
            debug=debug,
            trace=trace,
            service_id=source_url,
            source_url=source_url,
            model=writer_model,
            final_output=raw,
            status="ok",
            duration_ms=duration_ms,
            session_id=session_id,
            created_at=created_at,
            prompt_tokens=final_usage["prompt_tokens"],
            completion_tokens=final_usage["completion_tokens"],
            total_tokens=final_usage["total_tokens"],
            cached_tokens=final_usage["cached_tokens"],
            digest=digest,
        )
    except Exception:
        logger.warning("failed to record tool-insights session", exc_info=True)


def _compose_via_writer_tools_locked(
    *,
    system: str,
    user: str,
    source_url: str,
    llm: LLMProvider,
    research_user: str | None = None,
    is_special_edition: bool = False,
    research_client: LLMProvider | None = None,
    session_register: SessionRegister | None = None,
    admin_sources: list[AdminSource] | None = None,
    check_completeness_gate: bool = False,
) -> LLMArticleFields:
    from app.core.config import WRITER_TOOLS_ENABLED

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if WRITER_TOOLS_ENABLED:
        playwright_session = None
        # Bound below to `trace` (chart_data_session_trace) so the chart_data
        # tool can check a custom chart's numbers against THIS session's own
        # tool-call trace so far, not the whole system's history. Declared here
        # (before `trace` exists) so the `finally` below can always close it,
        # even on a failure before that point.
        _chart_trace_scope = contextlib.ExitStack()
        try:
            from app.core.config import (
                LLM_MAX_TOOL_ROUNDS,
                LLM_TIMEOUT_SECONDS,
                LLM_TIMEOUT_SPECIAL_EDITION_MULTIPLIER,
                WRITER_TWO_STAGE,
            )
            from app.modules.ai.writer_tools import all_tools

            research_max_rounds = LLM_MAX_TOOL_ROUNDS * 4 if is_special_edition else None
            # A special edition's research chat_with_tools loop resends the
            # whole accumulated trace every round; by round 16+ that prompt
            # is large enough that the plain per-attempt timeout isn't
            # always enough (root-caused 2026-08-04 -- see config.py).
            research_timeout = (
                LLM_TIMEOUT_SECONDS * LLM_TIMEOUT_SPECIAL_EDITION_MULTIPLIER
                if is_special_edition
                else None
            )

            research_llm = research_client or get_llm_research_client(timeout=research_timeout)
            register = session_register or SessionRegisterCassandra()
            from app.modules.scraper.core.browser_scrape import maybe_start_session

            # One Chromium instance for the WHOLE compose, reused by every
            # fetch_url/click_element/type_into_page call -- launching a
            # fresh browser per call was too expensive to make Playwright
            # rendering the default for every HTML fetch (2026-08-11).
            # Closed in the finally below regardless of how this compose ends.
            playwright_session = maybe_start_session()
            tool_context = {
                "service_id": source_url,
                "source_url": source_url,
                # The model actually serving this session's research calls --
                # not the Mistral-only config constant, which stays wrong the
                # moment LLM_PROVIDER_RESEARCH (or a canary roll) routes this
                # session to DeepSeek instead.
                "model": research_llm.model,
                "_playwright_session": playwright_session,
            }
            tool_schemas, tool_handlers = all_tools(context=tool_context)
            trace: list = []
            # Seed BEFORE the research loop starts (design doc section 3) so
            # owner-supplied figures are grounded anchors from round 1, not
            # just at final telemetry time.
            _seed_admin_source_trace(trace, admin_sources)
            from app.modules.ai.chart_tools import chart_data_session_trace

            _chart_trace_scope.enter_context(chart_data_session_trace(trace))
            debug: dict = {}
            # Set once at the "writing" checkpoint (the research digest is only
            # known once stage 1 finishes) and read by EVERY later upsert in
            # this session, including the terminal one -- a plain per-call
            # digest="" default would silently overwrite the real value the
            # moment the next checkpoint fires. Root-caused 2026-08-14: a
            # Kimi K3 write call timed out right after a real, expensive
            # research phase completed, and that phase's digest had nowhere
            # to persist to -- see session_register.py's digest docstring.
            _digest_holder: dict[str, str] = {"value": ""}
            import time as _time

            _t0 = _time.monotonic()

            # Progress checkpoints: one stable session row, upserted at each stage,
            # so the admin Sessions view shows live progress (research -> writing
            # -> done) instead of nothing until the very end.
            _sid, _screated = register.new_ref()

            # Running total for every ephemeral rubric/digest-tier client this
            # session spends (grade/revise's get_llm_rubric_client(), digest
            # synthesis/gap-fill/entity-enumeration/narrative-outline's
            # get_llm_digest_client()) -- none of those reuse research_llm/llm,
            # so _usage_so_far below folds this in explicitly (2026-08-28
            # audit; see _merge_usage's docstring). Two-stage only: the legacy
            # single-loop branch grades via a tool call on `llm` itself, so
            # its usage is already inside llm.usage_totals().
            _extra_usage: dict[str, int] = dict.fromkeys(_USAGE_KEYS, 0)

            def _usage_so_far() -> dict[str, int]:
                """Combined token usage across every client used in this session: research_llm for stage 1, llm for stage 2/revise, plus _extra_usage for any ephemeral rubric/digest-tier client spawned along the way. research_llm/llm are each a fresh instance per compose, so their counters are this session's total, not a lifetime one."""
                research_usage = research_llm.usage_totals()
                write_usage = llm.usage_totals()
                return {
                    key: research_usage[key] + write_usage[key] + _extra_usage[key]
                    for key in _USAGE_KEYS
                }

            def _checkpoint(stage_status: str, *, detail: str = "", digest: str = "") -> None:
                _digest_holder["value"] = digest or _digest_holder["value"]
                with contextlib.suppress(Exception):
                    usage = _usage_so_far()
                    register.upsert(
                        debug=debug,
                        trace=trace,
                        service_id=source_url,
                        source_url=source_url,
                        model=(research_llm.model if stage_status == "researching" else llm.model),
                        # final_output is otherwise always empty on a terminal
                        # failure status -- reusing it to carry the exception
                        # text costs no schema change and is the ONLY place
                        # this ever gets persisted. Root-caused 2026-08-07:
                        # a LLMError from the API (rate limit, context
                        # length, etc.) checkpointed status='error' with no
                        # detail anywhere, Bugsnag had no record of it either,
                        # and this host has no log access to fall back on --
                        # the actual failure reason was unrecoverable after
                        # the fact.
                        final_output=detail[:2000],
                        status=stage_status,
                        duration_ms=int((_time.monotonic() - _t0) * 1000),
                        session_id=_sid,
                        created_at=_screated,
                        prompt_tokens=usage["prompt_tokens"],
                        completion_tokens=usage["completion_tokens"],
                        total_tokens=usage["total_tokens"],
                        cached_tokens=usage["cached_tokens"],
                        digest=_digest_holder["value"],
                    )

            if WRITER_TWO_STAGE:
                payload = _run_two_stage_compose(
                    research_llm=research_llm,
                    llm=llm,
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
                    extra_usage=_extra_usage,
                    check_completeness_gate=check_completeness_gate,
                )
            else:
                # Legacy single agentic loop: tools + final article in one pass.
                raw = llm.chat_with_tools(
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
                glossary_client=research_llm,
            )
            raw = json.dumps(payload)
            _duration_ms = int((_time.monotonic() - _t0) * 1000)
            _record_compose_telemetry(
                source_url,
                trace,
                raw,
                report_errors_model=(research_llm.model if WRITER_TWO_STAGE else llm.model),
                writer_model=llm.model,
                duration_ms=_duration_ms,
                session_id=_sid,
                created_at=_screated,
                debug=debug,
                usage_so_far=_usage_so_far,
                session_register=register,
                digest=_digest_holder["value"],
            )
            return _parse_article_fields(payload)
        except StorySpikedError as spike:
            # The writer refused the story (abort_article tool) — a judgment,
            # not a failure. MUST be caught before the generic Exception
            # below: falling through would trigger the ungrounded single-shot
            # fallback, i.e. compose exactly the evidence-free article the
            # writer just declined to write. The trace already carries the
            # spike call (llm_openai_compatible.py records it before re-raising), so
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
        except LLMCreditError as exc:
            # 401/402 — no retry will help (bad key or credit exhausted), so
            # tag it distinctly from a generic API error: the admin Sessions
            # view and the queue's last_reason should say WHY at a glance
            # instead of a plain "error" that looks the same as any other fault.
            with contextlib.suppress(Exception):
                _checkpoint("credit_insufficient", detail=str(exc))
            raise
        except LLMError as exc:
            # A real API error (rate limit, context length, etc.) — already
            # retried with backoff inside the client for the retryable cases.
            # Don't burn another call on a single-shot retry that will just
            # fail the same way; let the caller fall to template. Finalize
            # the checkpoint row first, else the Sessions view shows it
            # frozen at 'researching'/'writing' forever (looks stuck
            # mid-compose). logger.exception here mirrors the fallback
            # branch's own 2026-07-16 fix below (same "exception vanishes
            # with zero trace" failure mode) -- root-caused live 2026-08-07
            # when a LLMError from this exact branch left no detail
            # anywhere (Bugsnag had no record, this host has no log access).
            logger.exception("compose hit a Mistral/DeepSeek API error for %s", source_url)
            with contextlib.suppress(Exception):
                _checkpoint("error", detail=str(exc))
            raise
        except Exception as exc:
            # Tool/parse failure (the API worked). Used to fall back to an
            # ungrounded tool-less chat_json_object() single-shot here --
            # removed 2026-08-28 (CLAUDE.md invariant #4 / owner decision
            # 2026-07-14: no ungrounded fallback compose, ever -- a compose
            # that fails must raise, never quietly return a worse, tool-less
            # draft as if nothing happened). Re-raise so this failure is
            # indistinguishable from any other failed compose to the caller:
            # retried or surfaced, never silently downgraded.
            #
            # Root-caused 2026-07-16: this branch swallowed the exception with
            # ZERO logging, so a real crash (dork.fi: 16 real tool calls,
            # including a genuine docs.dork.fi fetch, then silently died before
            # the next assistant turn) was only reconstructable after the fact
            # by manually replaying compose_sessions.messages — the traceback
            # itself was gone forever. logger.exception here costs nothing and
            # makes every future one of these actually diagnosable.
            logger.exception("compose tool loop failed for %s", source_url)
            with contextlib.suppress(Exception):
                _checkpoint("error", detail=str(exc))
            raise
        finally:
            _chart_trace_scope.close()
            if playwright_session is not None:
                with contextlib.suppress(Exception):
                    playwright_session.close()

    payload = llm.chat_json_object(messages)
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
broader implications, and open questions or what to watch next — but place
each open question beside the claim it qualifies as you go (per WEIGH GOOD
FAITH in your system instructions: what comes last carries the same weight
as what comes first), not gathered into a trailing "what we don't know"
movement at the end. "What to watch next" means a real forward fact — a
date, a pending decision, a threshold this story builds toward — not a
recap of unresolved items, so a reader comes away with a story, not just a
summary.

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
you already found — a dozen searches that surface only a handful of
distinct domains means the queries kept circling the same sources."""


def compose_assignment_article(
    *,
    brief_title: str,
    brief_body: str,
    keywords: str,
    brief_id: str,
    is_special_edition: bool = False,
    client: MistralProvider | None = None,
    check_completeness_gate: bool = False,
) -> LLMArticleFields:
    """Generate a from-scratch article for an editor-assigned topic (no scraped source page). Unlike ``compose_scrape_article``, the brief text is NOT verified fact — the model must substantiate the topic itself via tools before writing, using the same research -> write -> grade/revise loop. ``is_special_edition`` requests a longer, multi-angle in-depth treatment instead of the standard length-scaled-to-substance pass.

    ``check_completeness_gate`` (2026-09-09): see compose_scrape_article's
    own docstring -- same opt-in, forwarded unchanged.
    """
    llm = client or get_llm_writer_client()
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
        llm=llm,
        topic="editorial_assignment",
        is_special_edition=is_special_edition,
        check_completeness_gate=check_completeness_gate,
    )


def compose_recap_from_transcript(
    *,
    service_name: str,
    source_url: str,
    page_title: str,
    transcript_text: str,
    client: MistralProvider | None = None,
) -> LLMArticleFields:
    """Community-call recap from a video transcript (Phase 4).

    Uses the premium model — transcripts are long-form input.
    """
    from app.core.config import MISTRAL_MODEL_PREMIUM

    llm = client or MistralProvider(model=MISTRAL_MODEL_PREMIUM)
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

    payload = llm.chat_json_object(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=2048,
    )
    return _parse_article_fields(payload)


def compose_weekly_digest_article(
    context: WeeklyDigestContext,
    *,
    client: MistralProvider | None = None,
) -> LLMArticleFields:
    """Generate full weekly digest (price + feed highlights) via the digest-tier LLM."""
    from app.core.config import PUBLIC_ARTICLE_BASE_URL

    llm = client or get_llm_digest_client()
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

    payload = llm.chat_json_object(
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
    translate_article). A markdown table has no blank lines inside it,
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


def translate_article(
    *,
    english_title: str,
    english_summary: str,
    english_body: str,
    target_language: str,
    client: MistralProvider | None = None,
) -> dict[str, str]:
    """Translate an English article to the target language via the translate-tier LLM, block-aligned to the source.

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

    llm = client or get_llm_translate_client()

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
        '- When you NAME an entity via apposition ("Algorand\'s native token, ALGO" / a brand, '
        "a ticker, a protocol), the named term stays BARE in that slot — do not add an article or "
        "possessive to it there, even if the target language would normally use one. Verified "
        'against live French crypto press: "OpenSea reveals $SEA, its native token" style '
        "constructions never article the name in the naming position, only in a later standalone "
        'reference ("$SEA then rose 10%" DOES take the article once it is no longer being '
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
    payload = llm.chat_json_object(messages)
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
        payload = llm.chat_json_object(messages)
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
