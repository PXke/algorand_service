from __future__ import annotations

import contextlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from app.core.config import MISTRAL_MAX_SOURCE_CHARS
from app.modules.ai.mistral_client import (
    MistralClient,
    MistralError,
    get_mistral_client,
    get_mistral_digest_client,
)
from app.modules.ai.reference_block import append_reference_block
from app.modules.metrics.price_metrics_store import load_mistral_context
from app.modules.newspaper.price_analysis import WeeklyPriceSnapshot
from app.modules.newspaper.weekly_digest import WeeklyDigestContext

logger = logging.getLogger(__name__)

# Bump whenever a compose prompt in this module changes materially (system
# guidelines, _ARTICLE_FORMAT_RULES, recency/profile rules, etc). Stamped onto
# every stored article so analytics can correlate a prompt edit with a shift in
# grades/engagement instead of guessing from deploy timestamps.
PROMPT_VERSION = "2026-07-02"


@dataclass(frozen=True)
class MistralArticleFields:
    title: str
    summary: str
    body: str
    tags: tuple[str, ...] = ()
    prompt_version: str = PROMPT_VERSION


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

# Shared guidance appended to the system prompt when the agentic tool loop is on.
# The ALGO PRICE/MARKET RULE is deliberate: the small model used to fetch the
# price for every story and pad unrelated articles (a dev tool, a partnership)
# with an irrelevant price table, which made them look automated.
_TOOLS_GUIDANCE = (
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
    "ALGO PRICE/MARKET RULE: do not add market or chain metrics by default. This "
    "covers ALGO price AND network stats like TVL, volume, node/validator counts and "
    "block times. Fetch and mention any of them ONLY when the metric materially helps "
    "the reader understand THIS story — e.g. a supply-cap or tokenomics change and its "
    "inflation impact, a treasury/funding move, a markets/trading story, or a metric "
    "that is itself the news (a TVL milestone). For product launches, dev tools, "
    "partnerships, governance procedure, NFTs and general news where such numbers add "
    "nothing, omit them — do not append a metrics table or a price line to prove the "
    "piece is current. When in doubt, leave it out.\n"
    "JOURNALISM RULES: only state facts a tool actually returned; cite the "
    "tool/source in the text; never assert wrongdoing about a named person or "
    "company unless a tool returned concrete evidence; when a SPECIFIC claim is "
    "unverified, hedge or drop THAT claim — not the rest of the story.\n"
    "NO FABRICATION (but DO use what you found): never invent, guess at, or embellish "
    "facts to fill space or hit a length target. Within that bound, fully develop "
    "every angle your research actually supports — explain mechanisms, who is "
    "affected, and why it matters. Match length to verified substance: never pad "
    "with fluff, but never cut relevant, verified context short either. A thin "
    "source earns a short piece; a rich one earns a thorough one. Stop only when the "
    "verified material is genuinely exhausted. Making things up is the one thing "
    "that is never acceptable.\n"
    + _STRICT_QUOTE_GROUNDING
    + "TOOL GAPS (do this every story, do not skip): after researching, ask whether "
    "any fact, number, or source would have made THIS story sharper, deeper, or "
    "better verified if a tool could have fetched it. Call suggest_tool for EACH "
    "such gap — even when you finished the article without it. This is NOT only for "
    "hard walls: a fact you worked around with a weaker source, or could verify less "
    "than you wanted, counts too. suggest_tool returns no data and never blocks the "
    "article — naming these gaps is part of the job, not an exception.\n"
    "SELF-REVIEW (MANDATORY — every article, no exceptions): you MUST call "
    "review_draft at least once before you finish; do NOT output the final JSON "
    "until you have. When the draft is complete, call review_draft with your "
    "title and full body. It returns a 0-10 grade, "
    "per-dimension subscores (novelty, relevance, recency, length, specificity, "
    "structure) and a list of concrete issues. If the grade is below ~7 or any "
    "issues are listed, REVISE the draft to fix them — work toward the target "
    "length the review reports (a well-developed piece, not a short stub) WITHOUT "
    "inventing facts to pad it, and give it scannable structure (a heading plus at "
    "least one list or table). If you cannot reach the length on real material, "
    "leave it shorter. "
    "You may call review_draft at most twice (initial check, then one re-check "
    "after revising). Then output the final JSON article."
)


# Research-phase guidance for two-stage compose: the tool-discipline + journalism
# rules WITHOUT the self-review/"then WRITE" steering — the warm pass produces the
# article separately, and review_draft has no draft to grade yet.
_RESEARCH_PHASE_GUIDANCE = (
    _TOOLS_GUIDANCE.split("SELF-REVIEW (MANDATORY")[0].rstrip()
    + "\n\nRESEARCH PHASE ONLY: right now your job is to gather and verify the "
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
    "the tools surface no fresh facts, that is acceptable — reply READY anyway, but "
    "the article must then be a tight summary, not padded to length.\n"
    "Only once you genuinely have enough, reply with the single word READY "
    "and nothing else; the article is written in a separate step."
)


def _format_research_digest(trace: list[dict]) -> str:
    """Condense the research trace into a ground-truth findings block for the warm
    generation pass: one line per tool call (tool, args -> result)."""
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
    "into cohesive prose (aim for a polished ~400-600 words when the facts support "
    "it; otherwise length is flexible 250-2000 — let the facts decide, never pad). "
    "Three rules:\n"
    "1. TRANSLATE TECHNICAL FINDINGS: when a finding is technical (an SDK, a token "
    "standard, a protocol like x402, smart-contract/escrow mechanics), don't just "
    "name it — spend 1-2 sentences on what it ENABLES and why it matters to a "
    "developer or business using the platform.\n"
    "2. PROBLEM -> SOLUTION FRAME: ground the opening in the real-world friction "
    "the project addresses — contrast the legacy pain (e.g. multi-day settlement, "
    "intermediary fees) with the specific chain mechanics that resolve it (e.g. "
    "~4s finality, sub-cent fees), using the concrete figures you actually found.\n"
    "3. CONNECT THE DISCOVERIES: smooth transitions so it reads as ONE story — link "
    "the user-facing product to the underlying tech you uncovered (e.g. how the web "
    "app sits on the open-source library / escrow contracts).\n"
    "This is EXPANSION BY EXPLANATION of real findings only — still never invent "
    "facts, and still no generic filler."
)


def _urls_from_result(result: Any) -> list[str]:
    """Best-effort extraction of every URL a tool result carries — a single fetch
    exposes a top-level "url", while search-style tools nest hits under a list
    key (search_web's "results", search_bluesky's "posts", etc.)."""
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
    """The distinct source(s) one research call actually touched: the domain(s)
    in its result, else a domain-like argument, else the tool name. Keying on
    bare tool identity let the floor be satisfied by several trivial calls that
    all skim the same one or two domains; keying on domains instead rewards what
    the floor is meant to enforce — breadth of sources, not call count."""
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
    """Count distinct research sources touched so far (domains fetched, or a
    stable per-tool identity for calls with no URL), excluding review_draft
    self-checks."""
    signals: set[str] = set()
    for entry in trace:
        signals |= _research_call_signals(entry)
    return len(signals)


def _research_floor_nudge(have: int, need: int, digest: str) -> str:
    """A stronger directive to send the model back for a deeper research pass when
    it stopped too early (the Stage-1 research floor)."""
    return (
        f"\n\nSTOP — you only touched {have} distinct research source(s); this story "
        f"needs at least {need} before writing. You have so far gathered:\n{digest}\n\n"
        "Now dig deeper with DIFFERENT tools and arguments than above — e.g. "
        "search_bluesky for current community sentiment, search_web for recent "
        "integrations/partnerships/comparisons, or a market/on-chain tool for live "
        "metrics. Do NOT repeat calls you already made. Reply READY only once you "
        "have genuinely gathered more — or, if the tools truly surface nothing new, "
        "reply READY and the article will be a tight summary."
    )


def _debug_tool_turn(debug: dict | None, name: str, arguments: dict, result: dict) -> None:
    """Record a (synthetic) tool call + result into the debug transcript so the
    admin Sessions view shows it. Two-stage compose calls the grader directly
    rather than via the model's tool loop, so these turns aren't captured
    automatically the way the legacy single-loop's were."""
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


def _review_and_revise(
    mistral: MistralClient,
    payload: dict,
    *,
    system: str,
    gen_user: str,
    trace: list[dict],
    debug: dict | None = None,
) -> dict:
    """Stage 3+4 of two-stage compose: grade the draft, then revise once if weak.

    The warm generation pass runs with NO tools, so the model cannot call
    review_draft itself — we run the heuristic grader deterministically here and,
    on a sub-threshold grade or any listed issues, do exactly one revision pass
    with the concrete issues fed back. Both gradings are recorded in the trace
    like review_draft tool calls so telemetry/insights see them.
    """
    from app.core.config import MISTRAL_TEMP_WRITE, WRITER_REVIEW_ENABLED
    from app.modules.newspaper.article_grader import grade_article_draft

    if not WRITER_REVIEW_ENABLED:
        return payload
    title = str(payload.get("title", "") or "")
    body = str(payload.get("body", "") or "")
    if not body:
        return payload

    try:
        review = grade_article_draft(title=title, body=body)
    except Exception as exc:
        review = {"error": str(exc)[:200], "grade": None}
    # The grader reads the FULL title+body; we record only a compact label in the
    # trace (title + word count) to avoid dumping the whole body into the trace.
    grade_args = {"title": title, "words": len(body.split())}
    trace.append({"tool": "review_draft", "arguments": grade_args, "result": review})
    _debug_tool_turn(debug, "review_draft", grade_args, review)

    issues = review.get("issues") or []
    # A tool-less rewrite can only RESTRUCTURE or TRIM — it cannot add facts or do
    # more research. So revise ONLY for those fixable problems; never revise for
    # "short" / "shallow research" / "vague" (they need real fetching, and a
    # revision asked to fix them would just hallucinate filler).
    fixable = [i for i in issues if i.startswith(("too long", "structure"))]
    if not fixable:
        return payload

    issues_block = "\n".join(f"- {i}" for i in fixable[:8])
    too_long = any(i.startswith("too long") for i in fixable)
    # The reviser is judged by the 75% word-count guard below; give it that
    # constraint as a CONCRETE number — "keep roughly the same length" alone
    # still lost >25% of the words in ~10% of prod revisions, wasting the call.
    draft_words = len(body.split())
    min_words = int(draft_words * 0.8)
    length_rule = (
        "Trim padding/filler to bring it under the limit, but keep every real fact."
        if too_long
        else "PRESERVE every fact AND keep the same length — only REORGANIZE "
        "the existing prose into headings, short paragraphs and at least one list or "
        "table. Do NOT drop information, summarize away detail, or shorten the article: "
        f"the draft is {draft_words} words and your revision MUST stay above "
        f"{min_words} words or it will be rejected."
    )
    revise_user = (
        gen_user + f"\n\nA reviewer flagged these formatting/length problems:\n{issues_block}\n\n"
        f"{length_rule} Do NOT add, invent, or restate facts beyond the research "
        "findings above, and do not pad with new filler. Return the full revised "
        "article as the same JSON object."
    )

    def _note_revision_failure(reason: str) -> None:
        # Surface WHY the revision didn't happen instead of silently keeping the
        # weak draft — otherwise a rate-limited/failed revision is invisible and
        # looks like "the grade changed nothing".
        result = {"error": reason[:300]}
        args = {"revision": "failed"}
        trace.append({"tool": "review_draft", "arguments": args, "result": result})
        _debug_tool_turn(debug, "review_draft", args, result)

    try:
        revised = mistral.chat_json_object(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": revise_user},
            ],
            temperature=MISTRAL_TEMP_WRITE,
        )
    except Exception as exc:
        _note_revision_failure(f"revision call failed: {type(exc).__name__}: {exc}")
        return payload
    if not str(revised.get("body", "") or "").strip():
        _note_revision_failure("revision returned an empty body")
        return payload
    # Safety net: a structure-only fix must not gut the article. If it dropped
    # more than ~25% of the words (and we weren't trimming an over-long piece),
    # keep the original — a reformat that loses that much has lost real content.
    orig_words = len(body.split())
    new_words = len(str(revised.get("body", "")).split())
    if not too_long and orig_words and new_words < 0.75 * orig_words:
        _note_revision_failure(
            f"revision dropped too much content ({orig_words} -> {new_words} words); kept original"
        )
        return payload

    # Record the post-revision grade for telemetry. No further revision pass.
    try:
        revised_body = str(revised.get("body", "") or "")
        regrade = grade_article_draft(
            title=str(revised.get("title", "") or ""),
            body=revised_body,
        )
        recheck_args = {
            "title": revised.get("title", ""),
            "words": len(revised_body.split()),
            "recheck": True,
        }
        trace.append({"tool": "review_draft", "arguments": recheck_args, "result": regrade})
        _debug_tool_turn(debug, "review_draft", recheck_args, regrade)
    except Exception:
        logger.warning("failed to record post-revision grade telemetry", exc_info=True)
    return revised


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
    "title": "Nodely: The Global Backbone for Algorand’s Developer Ecosystem",
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
    "Write the article as a single JSON object adhering exactly to this schema:\n"
    '{"title": "string", "summary": "string", "body": "string", "tags": ["string"]}\n\n'
    "Field constraints:\n"
    "- title: a captivating, professional headline, max 120 chars; do NOT use "
    '"Service: Page title" format\n'
    "- summary: a concise deck for feed cards; STRICT MAXIMUM of 280 characters; "
    "describe the story, not the pipeline\n"
    "- body: the full article in Markdown, length scaled to the substance (a short "
    "update can be a few hundred words; a meaty story can run long) — never pad a thin "
    "story to hit a length. Write like a skilled human journalist and format it however "
    "best serves THIS story: YOU decide on sections, lists, tables or pure prose. Avoid "
    "robotic, templated phrasing.\n"
    "  - SCANNABILITY: break up the text with descriptive Markdown headers (## and ###). "
    "Do NOT write more than three consecutive paragraphs without introducing a new section "
    "header. (Very short updates of only a paragraph or two are exempt.)\n"
    "  - DATA PRESENTATION: never bury multiple metrics, pricing tiers, dates or feature "
    "lists in a single dense paragraph. Extract dense numerical or itemized data into a "
    "clean Markdown table or a bulleted list so the reader can scan it.\n"
    "  - IGNORE navigation menus, cookie/consent banners, footers, share/subscribe/login "
    "prompts and other page boilerplate in the source — extract the actual story. If the "
    "source carries no real news, write a brief honest note rather than padding\n"
    "  - a chart is OPTIONAL: include ONE only if this article itself has a trend or "
    "comparison worth visualizing, and chart that subject's own data — never ALGO "
    "price/market metrics by default (see the ALGO PRICE/MARKET RULE). Fenced format:\n"
    "    ```chart\n"
    f"    {_CHART_EXAMPLE}\n"
    "    ```\n"
    '    Use "line" for trends over time, "bar" for category comparisons. Only chart '
    "REAL data you fetched via tools; never invent numbers.\n"
    "  - cite sources clearly within the text where relevant, and end with ## Source "
    "linking the URL\n"
    "  - when you first mention a notable project, protocol, company or person, link its "
    "name to its canonical site with an inline Markdown link [Name](https://...); link "
    "only the FIRST mention and only URLs you are confident about (prefer ones a tool "
    "returned) — do not over-link or invent URLs\n"
    "- tags: 2–5 lowercase topical tags drawn from the content (e.g. defi, governance, "
    "tokenization, wallet, nft, sdk, partnership) — specific, not generic\n"
    "- On-chain context (the round and tx given in the payload) is background only — do "
    "not list it in the body"
    f"{_GOOD_EXAMPLE}"
)


def _today_utc() -> str:
    return datetime.now(tz=UTC).date().isoformat()


def _recency_rule(today: str) -> str:
    """Temporal-awareness rule: scraped pages routinely carry stale figures and the
    model otherwise has no idea what 'today' is, so it restates old numbers as current
    (e.g. an article written in 2026 quoting 2022 TVL)."""
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
    """A root domain / shallow marketing page (e.g. https://tinyman.org) is a
    static profile, not a dated news item. Such pages have no reliable timeline
    (a persistent 'v2 is live!' banner sits next to an undated roadmap), so the
    writer must produce an evergreen profile, never breaking news — this is the
    deterministic fix for chronological context collapse."""
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


def _clip(text: str, limit: int = MISTRAL_MAX_SOURCE_CHARS) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _source_links_block(source_links: list[dict[str, str]] | None, *, limit: int = 25) -> str:
    """The source page's own outbound links (the research trail), rendered for the
    composer. Links are stripped from `page_text` (they'd pollute the relevance/
    novelty signals), so this is how the writer learns what to `fetch_url` next."""
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


def _coerce_markdown(value: Any) -> str:
    """Flatten a body the model emitted as nested JSON back into markdown.

    In json_object mode the model sometimes returns ``body`` as an object keyed
    by section heading (``{"## Market snapshot": "...table...", ...}``) or a list
    of blocks instead of a single markdown string. ``str(dict)`` would store the
    Python repr verbatim (the "broken JSON structure" on the page), so reconstruct
    real markdown: each key becomes a heading, each value its content."""
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
    return MistralArticleFields(title=title, summary=summary, body=body, tags=tuple(tags[:6]))


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
    client: MistralClient | None = None,
) -> MistralArticleFields:
    """Generate newspaper article fields from scrape context via Mistral."""
    mistral = client or get_mistral_client()
    today = _today_utc()
    source_domain = (urlparse(source_url).netloc or "").lower()
    links_block = _source_links_block(source_links)
    diff_block = ""
    if diff:
        diff_block = f"\n\nText diff (unified):\n```\n{_clip(diff, 4000)}\n```"
    elif not is_first_snapshot:
        diff_block = "\n\n(Content hash changed but no textual diff was produced.)"

    system = (
        "You are an expert journalist and news writer for a premier Algorand-focused "
        "media outlet. Your goal is to write captivating, optimistic, and highly "
        "professional news articles based on provided source material.\n\n"
        "Writing guidelines:\n"
        "- Tone: professional, objective, and positive. Strictly avoid sensationalism, "
        "marketing speak, and fluffy language. The writing must be high-signal and "
        "sound distinctly human.\n"
        "- Establish the Stakes: never announce a technical upgrade or new feature "
        "without immediately explaining the real-world friction it eliminates or the "
        "threat it defends against.\n"
        "- Narrative Synthesis: identify the connective tissue between your research "
        "findings. Weave distinct developments into a unified narrative rather than "
        "presenting them as isolated bullet points.\n"
        "- Concrete Scenarios: translate abstract blockchain concepts into concrete "
        "operational scenarios to make the implications vivid for the reader.\n"
        "- Depth: develop the story to the full depth your verified research supports. "
        "Don't be terse when you have the material — explain how it works, who it "
        "affects, and why it matters, and draw out the implications. Thorough writing "
        "built on real, cited findings is the goal; brevity is for when the verified "
        "material genuinely runs out, not a default.\n"
        "- Audience: intelligent general readers who are NOT crypto specialists. Briefly "
        "explain blockchain/DeFi/Algorand jargon in plain language on first use (e.g. "
        "what an ASA, validator, or TVL is), spell out acronyms once, and never assume "
        "prior crypto knowledge — without dumbing the story down or over-explaining basics.\n"
        "- Expertise: seamlessly integrate your deep knowledge of the Algorand ecosystem.\n"
        "- Adaptability: tailor the depth and focus to the source material (technical "
        "analysis for price or market updates; accessible, generalist coverage for "
        "broader announcements).\n"
        f"{_recency_rule(today)}"
        "- Accuracy: use only facts from the source material; never invent quotes, "
        "numbers, or on-chain events. Never put raw transaction IDs, round numbers, "
        "or 'Service:' labels in the body.\n"
        f"- {_STRICT_QUOTE_GROUNDING}\n"
        f"{_ARTICLE_FORMAT_RULES}\n\n"
        f"{_JSON_ONLY}"
    )
    # Source-type router: a static landing page (root domain) becomes an evergreen
    # profile, not breaking news — prevents chronological context collapse upstream.
    from app.core.config import SOURCE_TYPE_ROUTER_ENABLED

    if SOURCE_TYPE_ROUTER_ENABLED and is_static_landing_page(source_url):
        system = system + _PROFILE_GUIDANCE
    user = f"""Write the article now from the material below.

Today (UTC): {today}
Publisher / monitor: {service_name}
Source URL: {source_url}
Source domain: {source_domain}
Page title from source: {page_title}
First snapshot: {is_first_snapshot}
On-chain context (background only): round {round_num}, tx {txid}

Source material (may be days or years old — judge figures against today's date):
```
{_clip(page_text)}
```
{links_block}
{diff_block}
{_clip(enrichment_block, 5000) if enrichment_block else ""}"""

    return _compose_via_writer_tools(
        system=system,
        user=user,
        source_url=source_url,
        mistral=mistral,
        topic=publish_topic,
    )


def _compose_via_writer_tools(
    *,
    system: str,
    user: str,
    source_url: str,
    mistral: MistralClient,
    topic: str = "",
) -> MistralArticleFields:
    """Shared research -> write -> grade/revise loop behind every writer-tools
    compose path. Only depends on the system/user prompt pair and a label
    (``source_url``) used for tool scoping and session/investigation bookkeeping
    — it doesn't assume the source material was a real scraped page, so callers
    can feed it a from-scratch topic assignment just as well as a scrape diff."""
    from app.core.config import WRITER_TOOLS_ENABLED

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if WRITER_TOOLS_ENABLED:
        try:
            from app.core.config import MISTRAL_MODEL_WRITER
            from app.modules.ai.writer_tools import all_tools

            tool_context = {
                "service_id": source_url,
                "source_url": source_url,
                "model": MISTRAL_MODEL_WRITER,
            }
            tool_schemas, tool_handlers = all_tools(context=tool_context, topic=topic)
            trace: list = []
            debug: dict = {}
            import json as _json
            import time as _time

            _t0 = _time.monotonic()
            from app.core.config import (
                MISTRAL_TEMP_RESEARCH,
                MISTRAL_TEMP_WRITE,
                WRITER_TWO_STAGE,
            )

            # Progress checkpoints: one stable session row, upserted at each stage,
            # so the admin Sessions view shows live progress (research -> writing
            # -> done) instead of nothing until the very end.
            from app.modules.ai.tool_insights_store import (
                new_session_ref,
                record_compose_session,
            )

            _sid, _screated = new_session_ref()

            def _checkpoint(stage_status: str) -> None:
                with contextlib.suppress(Exception):
                    record_compose_session(
                        debug=debug,
                        trace=trace,
                        service_id=source_url,
                        source_url=source_url,
                        model=MISTRAL_MODEL_WRITER,
                        status=stage_status,
                        duration_ms=int((_time.monotonic() - _t0) * 1000),
                        session_id=_sid,
                        created_at=_screated,
                    )

            if WRITER_TWO_STAGE:
                _checkpoint("researching")
                # Stage 1 — cold research: tools available (minus review_draft, no
                # draft yet), low temp for deterministic tool selection. We keep the
                # trace; the model's prose here is discarded.
                research_schemas = [
                    s
                    for s in tool_schemas
                    if (s.get("function") or {}).get("name") != "review_draft"
                ]
                research_handlers = {k: v for k, v in tool_handlers.items() if k != "review_draft"}
                mistral.chat_with_tools(
                    [
                        {"role": "system", "content": system + _RESEARCH_PHASE_GUIDANCE},
                        {"role": "user", "content": user},
                    ],
                    tools=research_schemas,
                    handlers=research_handlers,
                    trace=trace,
                    debug=debug,
                    temperature=MISTRAL_TEMP_RESEARCH,
                    require_tool=None,
                )
                # Research FLOOR: if it stopped too early (the exact failure where
                # it reads an existing profile and quits), send it back to dig
                # deeper — bounded to RESEARCH_FLOOR_MAX_PASSES extra passes.
                from app.core.config import (
                    RESEARCH_FLOOR_ENABLED,
                    RESEARCH_FLOOR_MAX_PASSES,
                    RESEARCH_MIN_TOOL_CALLS,
                )

                if RESEARCH_FLOOR_ENABLED:
                    for _ in range(max(0, RESEARCH_FLOOR_MAX_PASSES)):
                        have = _distinct_research_calls(trace)
                        if have >= RESEARCH_MIN_TOOL_CALLS:
                            break
                        nudge = _research_floor_nudge(
                            have, RESEARCH_MIN_TOOL_CALLS, _format_research_digest(trace)
                        )
                        mistral.chat_with_tools(
                            [
                                {"role": "system", "content": system + _RESEARCH_PHASE_GUIDANCE},
                                {"role": "user", "content": user + nudge},
                            ],
                            tools=research_schemas,
                            handlers=research_handlers,
                            trace=trace,
                            debug=debug,
                            temperature=MISTRAL_TEMP_RESEARCH,
                            require_tool=None,
                        )
                _checkpoint("writing")  # research done, now generating
                # Stage 2 — warm generation: tools removed, prompt swapped back to
                # the plain article contract, higher temp, JSON mode. The research
                # findings ride in as ground truth.
                digest = _format_research_digest(trace)
                gen_user = user + (
                    "\n\nVerified research findings (from tools — these outrank the "
                    "raw source material where they differ; treat them as ground "
                    "truth and do not contradict or invent beyond them):\n"
                    f"{digest}\n\n"
                    "Build the article around these findings."
                    + _NARRATIVE_GUIDANCE
                    + " Write it now."
                    if digest
                    else ""
                )
                payload = mistral.chat_json_object(
                    [
                        {"role": "system", "content": system},
                        {"role": "user", "content": gen_user},
                    ],
                    temperature=MISTRAL_TEMP_WRITE,
                )
                # The warm pass runs outside the tool loop, so its turn isn't in
                # the debug transcript — add it so Sessions shows the draft.
                if isinstance(debug.get("messages"), list):
                    debug["messages"].append(
                        {
                            "role": "user",
                            "content": "[stage 2] generate the article from research findings",
                        }
                    )
                    debug["messages"].append(
                        {"role": "assistant", "content": _json.dumps(payload)[:4000]}
                    )
                # Stage 3+4 — deterministic grade, then one revision if weak.
                payload = _review_and_revise(
                    mistral, payload, system=system, gen_user=gen_user, trace=trace, debug=debug
                )
                raw = _json.dumps(payload)
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
                )
                payload = _json.loads(raw)
            # Stage-2 assembly: append every successfully fetched research URL the
            # body doesn't already cite, so deep links survive into the published
            # article (lifts citation density; preserves existing prose).
            payload = append_reference_block(payload, trace)
            raw = _json.dumps(payload)
            _duration_ms = int((_time.monotonic() - _t0) * 1000)
            try:
                from app.modules.newspaper.investigation_store import store_investigation_findings

                store_investigation_findings(
                    service_id=source_url, source_url=source_url, trace=trace
                )
            except Exception:
                logger.warning(
                    "failed to store investigation findings for %s", source_url, exc_info=True
                )
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
                    model=MISTRAL_MODEL_WRITER,
                )
                record_tool_usage_from_trace(trace)
                record_compose_session(
                    debug=debug,
                    trace=trace,
                    service_id=source_url,
                    source_url=source_url,
                    model=MISTRAL_MODEL_WRITER,
                    final_output=raw,
                    status="ok",
                    duration_ms=_duration_ms,
                    session_id=_sid,
                    created_at=_screated,
                )
            except Exception:
                logger.warning("failed to record tool-insights session", exc_info=True)
            return _parse_article_fields(payload)
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
            with contextlib.suppress(Exception):
                _checkpoint("fallback")

    payload = mistral.chat_json_object(messages)
    return _parse_article_fields(payload)


def compose_assignment_article_mistral(
    *,
    brief_title: str,
    brief_body: str,
    keywords: str,
    brief_id: str,
    client: MistralClient | None = None,
) -> MistralArticleFields:
    """Generate a from-scratch article for an editor-assigned topic (no scraped
    source page). Unlike ``compose_scrape_article_mistral``, the brief text is
    NOT verified fact — the model must substantiate the topic itself via tools
    before writing, using the same research -> write -> grade/revise loop."""
    mistral = client or get_mistral_client()
    today = _today_utc()

    system = (
        "You are an expert journalist and news writer for a premier Algorand-focused "
        "media outlet. You have been assigned an original story by an editor — it has "
        "NOT been pre-researched, so your job is to investigate it yourself with your "
        "tools and then write a captivating, optimistic, and highly professional "
        "article from what you verify.\n\n"
        "Writing guidelines:\n"
        "- Tone: professional, objective, and positive. Strictly avoid sensationalism, "
        "marketing speak, and fluffy language. The writing must be high-signal and "
        "sound distinctly human.\n"
        "- Establish the Stakes: never announce a technical upgrade or new feature "
        "without immediately explaining the real-world friction it eliminates or the "
        "threat it defends against.\n"
        "- Narrative Synthesis: identify the connective tissue between your research "
        "findings. Weave distinct developments into a unified narrative rather than "
        "presenting them as isolated bullet points.\n"
        "- Concrete Scenarios: translate abstract blockchain concepts into concrete "
        "operational scenarios to make the implications vivid for the reader.\n"
        "- Depth: develop the story to the full depth your verified research supports. "
        "Don't be terse when you have the material — explain how it works, who it "
        "affects, and why it matters, and draw out the implications. Thorough writing "
        "built on real, cited findings is the goal; brevity is for when the verified "
        "material genuinely runs out, not a default.\n"
        "- Audience: intelligent general readers who are NOT crypto specialists. Briefly "
        "explain blockchain/DeFi/Algorand jargon in plain language on first use (e.g. "
        "what an ASA, validator, or TVL is), spell out acronyms once, and never assume "
        "prior crypto knowledge — without dumbing the story down or over-explaining basics.\n"
        "- Expertise: seamlessly integrate your deep knowledge of the Algorand ecosystem.\n"
        f"{_recency_rule(today)}"
        "- Accuracy: the editorial pointers below are a starting brief, NOT verified "
        "fact — confirm everything via your tools this session before relying on it. "
        "Use only facts you have verified; never invent quotes, numbers, or on-chain "
        "events. If your research cannot substantiate enough of the assigned topic, "
        "say so plainly in the article rather than inventing content.\n"
        f"- {_STRICT_QUOTE_GROUNDING}\n"
        f"{_ARTICLE_FORMAT_RULES}\n\n"
        f"{_JSON_ONLY}"
    )
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
verifiable facts before writing."""

    return _compose_via_writer_tools(
        system=system,
        user=user,
        source_url=f"editorial://brief/{brief_id}",
        mistral=mistral,
        topic="editorial_assignment",
    )


def compose_article_edit_mistral(
    *,
    service_name: str,
    source_url: str,
    existing_title: str,
    existing_summary: str,
    existing_body: str,
    new_page_title: str,
    new_page_text: str,
    diff: str | None,
    enrichment_block: str = "",
    client: MistralClient | None = None,
) -> MistralArticleFields:
    """Merge new reporting into an existing article (same story, in-place edit)."""
    mistral = client or get_mistral_client()
    today = _today_utc()
    diff_block = ""
    if diff:
        diff_block = f"\n\nDiff since last version:\n```\n{_clip(diff, 4000)}\n```"

    system = (
        "You are a news editor updating an existing Algorand community article. "
        "Preserve accurate prior facts; add a clear ## Updated section with new "
        "developments. Do not remove safety warnings.\n"
        "Audience: intelligent general readers who are NOT crypto specialists — explain "
        "any jargon in plain language on first use and never assume prior crypto knowledge.\n"
        f"{_recency_rule(today)}\n"
        "Write the updated article as a single JSON object with this schema:\n"
        '{"title": "string", "summary": "string", "body": "string"}\n'
        "- title: may tweak slightly\n"
        "- summary: mention the update\n"
        "- body: full markdown including a ## Updated section with the UTC date\n\n"
        f"{_JSON_ONLY}"
    )
    user = f"""Update this article with the new linked reporting below.

Service: {service_name}
New source URL: {source_url}
New signal title: {new_page_title}

Existing article:
Title: {existing_title}
Summary: {existing_summary}
Body:
```
{_clip(existing_body, 4000)}
```

New reporting to integrate:
```
{_clip(new_page_text)}
```
{diff_block}
{_clip(enrichment_block, 5000) if enrichment_block else ""}"""

    from app.core.config import WRITER_TOOLS_ENABLED

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if WRITER_TOOLS_ENABLED:
        try:
            from app.modules.ai.writer_tools import all_tools

            tool_schemas, tool_handlers = all_tools()
            tool_system = system + _TOOLS_GUIDANCE
            raw = mistral.chat_with_tools(
                [{"role": "system", "content": tool_system}, {"role": "user", "content": user}],
                tools=tool_schemas,
                handlers=tool_handlers,
                require_tool="review_draft",
            )
            import json as _json

            payload = _json.loads(raw)
            return _parse_article_fields(payload)
        except MistralError:
            # A real API error (rate limit, etc.) — already retried with backoff
            # inside the client. Don't burn another call on a single-shot retry
            # that will just fail the same way; let the caller fall to template.
            raise
        except Exception:
            # Tool/parse failure (the API worked): fall back to single-shot.
            logger.debug("tool-call compose failed; falling back to single-shot", exc_info=True)

    payload = mistral.chat_json_object(messages)
    return _parse_article_fields(payload)


def compose_recap_from_transcript_mistral(
    *,
    service_name: str,
    source_url: str,
    page_title: str,
    transcript_text: str,
    client: MistralClient | None = None,
) -> MistralArticleFields:
    """
    Community-call recap from a video transcript (Phase 4).
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


def compose_weekly_price_article_mistral(
    snapshot: WeeklyPriceSnapshot,
    *,
    client: MistralClient | None = None,
) -> MistralArticleFields:
    """Generate weekly price analysis narrative via Mistral."""
    mistral = client or get_mistral_client()
    system = (
        "You are a financial markets writer for a crypto newspaper. "
        "Be factual and concise.\n\n"
        "Write the analysis as a single JSON object with this schema:\n"
        '{"title": "string", "summary": "string", "body": "string"}\n'
        "- body: include a markdown table of the metrics and a short analysis "
        "section\n\n"
        f"{_JSON_ONLY}"
    )
    user = f"""Write a weekly price analysis article from the data below.

Asset: {snapshot.asset_name} ({snapshot.asset_id})
Currency: {snapshot.currency}
Current price: {snapshot.price_usd}
7d open: {snapshot.week_open_usd}
7d high: {snapshot.week_high_usd}
7d low: {snapshot.week_low_usd}
7d change %: {snapshot.week_change_pct:+.2f}
As of UTC: {snapshot.as_of.isoformat()}
Data source: CoinGecko market chart.{_price_metrics_block(snapshot.asset_id)}"""

    from app.core.config import WRITER_TOOLS_ENABLED

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if WRITER_TOOLS_ENABLED:
        try:
            from app.modules.ai.writer_tools import all_tools

            tool_schemas, tool_handlers = all_tools()
            tool_system = system + _TOOLS_GUIDANCE
            raw = mistral.chat_with_tools(
                [{"role": "system", "content": tool_system}, {"role": "user", "content": user}],
                tools=tool_schemas,
                handlers=tool_handlers,
                require_tool="review_draft",
            )
            import json as _json

            payload = _json.loads(raw)
            return _parse_article_fields(payload)
        except MistralError:
            # A real API error (rate limit, etc.) — already retried with backoff
            # inside the client. Don't burn another call on a single-shot retry
            # that will just fail the same way; let the caller fall to template.
            raise
        except Exception:
            # Tool/parse failure (the API worked): fall back to single-shot.
            logger.debug("tool-call compose failed; falling back to single-shot", exc_info=True)

    payload = mistral.chat_json_object(messages)
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
