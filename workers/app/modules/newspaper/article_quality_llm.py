"""LLM rubric for qualitative journalism dimensions the heuristic cannot score."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.modules.ai.llm_openai_compatible import MistralProvider

logger = logging.getLogger(__name__)

_QUALITY_RUBRIC = (
    "You are a strict editor grading a draft for PXke, an Algorand newspaper "
    "whose job is to make the ecosystem feel alive to a curious reader and get "
    "them to try what is being built — rigorously, but for a reader, not an "
    "auditor.\n"
    "Score ONLY these dimensions from 1 (poor) to 5 (excellent):\n"
    "- reader_value: would a curious reader with average crypto familiarity "
    "finish this knowing (1) what the subject IS in plain words, (2) what they "
    "can DO on it today and what that looks like, (3) why it matters and how "
    "it compares to what already exists, (4) the real catch, and (5) what is "
    "next? Score 5 only if the FIRST PARAGRAPH already answers (1) and the "
    "first section after it describes the product as a person meets it. "
    "Score LOW (1-2) if the reader is several sections into contract "
    "parameters, key roles or on-chain forensics before learning what the "
    "product is or what using it is like, if verification detail is the bulk "
    "of the body, if there is no outside context at all "
    "(no comparable, no 'why should an Algorand reader care'), or if section "
    "headers and sentence endings are aphorisms and teases rather than plain "
    "statements. A rigorous, fully-verified audit that never says what the "
    "thing is scores 1 here regardless of its other scores.\n"
    "- narrative_synthesis: cohesive journalism weaving findings together — NOT "
    "comma-separated feature dumps, NOT generic press-release tone, NOT dictionary "
    "definitions of curriculum pillars or feature lists, and NOT a draft that "
    "raises a question in one section and leaves it open while another section "
    "states the fact that answers it.\n"
    "- technical_depth: when a layer-1 mechanic genuinely played a role in the "
    "story's events, is it named and explained (with the legacy friction it "
    "solves)? RELEVANCE GATES THIS SCORE: a mechanic that did not bear on the "
    "story (e.g. citing state proofs in a wallet-phishing post-mortem, or a "
    "PPoS explainer in a partnership piece) is filler and scores LOW — the same "
    "as a foundation name-drop. Never suggest 'add more layer-1 mechanics' as a "
    "fix; suggest explaining the mechanics already implicated by the story.\n"
    "- critical_distance: does the draft apply independent scrutiny to a company's "
    "or project's own claims, or does it just restate their marketing framing as "
    "fact? A subject with an obvious conflict of interest (e.g. a centralized "
    "exchange's staking product, a token whose reward tier structure incentivizes "
    "holding the SAME platform's token, an unaudited or newly-launched protocol) "
    "should have that conflict, and the real risk/tradeoff a reader needs (custodial "
    "risk vs protocol-level control, counterparty risk, lack of audit, centralization) "
    "named explicitly — not omitted, and not buried under the subject's own framing "
    "of its benefits. A piece that only lists features/benefits without naming what "
    "a skeptical reader would want to know scores LOW here even if well-written.\n"
    "- repetition: does any specific fact or judgment (a number, a named risk, a "
    "conclusion like 'fees are undisclosed') get independently RESTATED across "
    "multiple sections — e.g. once in the prose, again in a table, again in a "
    "bullet list, again in a closing summary — instead of being said once and "
    "referenced afterward? Score 5 only if nothing is restated from scratch after "
    "its first mention; score LOW (1-2) if the same specific point is restated as "
    "a fresh observation 3+ times across the piece, even if each restatement is "
    "worded slightly differently — that still counts as repetition, not new "
    "information.\n\n"
    "Output a single JSON object with exactly these keys:\n"
    '{"reader_value": 3, "narrative_synthesis": 3, "technical_depth": 3, '
    '"critical_distance": 3, "repetition": 3, "issues": ["short fix"]}\n'
    "JSON SAFETY: Return JSON only — no markdown fences or prose. In issue strings "
    "use single quotes for any quoted text, or avoid double quotes entirely; never "
    "emit unescaped double quotes inside JSON string values.\n"
    "List 0-4 short, actionable issues only when a score is below 4."
)

_FALLBACK_QUALITY = {
    "model": "llm_rubric_error",
    "reader_value": 2,
    "narrative_synthesis": 2,
    "technical_depth": 2,
    "critical_distance": 2,
    "repetition": 2,
    "issues": [
        "quality rubric could not be parsed — weave facts into connected journalism, "
        "not dictionary-style summaries",
        "say what the subject is and what a reader does on it in the first "
        "paragraph, describe the product before its contract internals, and "
        "never let verification detail be the bulk of the body",
        "explain the Algorand layer-1 mechanics THIS story actually involves "
        "(never bolt on unrelated ones); put multi-item data in a Markdown "
        "table (Concept / Real-World Implication columns)",
        "name the actual risk/tradeoff instead of just relaying the subject's own "
        "marketing framing",
    ],
}


def _parse_quality_response(raw: Any) -> dict[str, Any] | None:  # noqa: ANN401 -- model output, dict or JSON-in-string
    """Parse rubric JSON; salvage fenced/prose-wrapped objects when possible."""
    from app.modules.ai.llm_openai_compatible import _parse_json_object

    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    return _parse_json_object(raw.strip())


_QUALITY_DIMS = (
    "reader_value",
    "narrative_synthesis",
    "technical_depth",
    "critical_distance",
    "repetition",
)


def _graded_scores(
    llm: MistralProvider, messages: list[dict], *, temperature: float
) -> tuple[dict[str, int | None], dict, list[str]]:
    """Call the rubric, retrying once for any dimension the first pass left null. Returns (scores, parsed_first_response, missing_dims_after_retry).

    Partial responses must not silently pass (2026-07-16: a real draft got
    narrative_synthesis=3 with the other three dimensions null — graded on 1
    of 4, and quality_needs_revision treats None as fine). One retry, then
    any still-missing dimension FAILS CLOSED at 2 (below every revision
    threshold), same stance as _FALLBACK_QUALITY.
    """
    parsed = llm.chat_json_object(messages, temperature=temperature, max_tokens=800)
    if not isinstance(parsed, dict):
        raise ValueError("non-object LLM grade")
    scores = {k: _clamp_score(parsed.get(k)) for k in _QUALITY_DIMS}
    if any(v is None for v in scores.values()):
        retry = llm.chat_json_object(messages, temperature=temperature, max_tokens=800)
        if isinstance(retry, dict):
            for k in _QUALITY_DIMS:
                if scores[k] is None:
                    scores[k] = _clamp_score(retry.get(k))
            if isinstance(retry.get("issues"), list) and not parsed.get("issues"):
                parsed["issues"] = retry["issues"]
    missing = [k for k, v in scores.items() if v is None]
    for k in missing:
        scores[k] = 2
    return scores, parsed, missing


def _dimension_issues(scores: dict[str, int | None]) -> list[str]:
    """Actionable feedback for each dimension scoring below the 4/5 quality bar."""
    issues = []
    reader_value = scores["reader_value"]
    narrative = scores["narrative_synthesis"]
    technical = scores["technical_depth"]
    critical_distance = scores["critical_distance"]
    repetition = scores["repetition"]
    if reader_value is not None and reader_value < 4:
        issues.append(
            f"reader value scored {reader_value}/5 — the first paragraph must say "
            "what the subject is and what a reader does on it; the first section "
            "describes the product as a person meets it; on-chain/contract "
            "verification gets one section, never the bulk of the body; "
            "add the outside context that says why an Algorand reader should care; "
            "headers and closing lines are plain statements, not aphorisms"
        )
    if narrative is not None and narrative < 4:
        issues.append(
            f"narrative synthesis scored {narrative}/5 — weave facts into "
            "connected prose, not comma lists or PR filler"
        )
    if technical is not None and technical < 4:
        issues.append(
            f"technical depth scored {technical}/5 — explain why Algorand's "
            "layer-1 mechanics fit this story, not just name-drop the foundation"
        )
    if critical_distance is not None and critical_distance < 4:
        issues.append(
            f"critical distance scored {critical_distance}/5 — name the actual "
            "risk/tradeoff (custodial risk, conflict of interest, lack of audit) "
            "instead of just relaying the subject's own marketing framing"
        )
    if repetition is not None and repetition < 4:
        issues.append(
            f"repetition scored {repetition}/5 — a specific fact or judgment is "
            "restated as a fresh observation in more than one section; keep the "
            "first mention and cut (or reference back to) the rest"
        )
    return issues


def grade_article_quality_llm(
    *,
    title: str,
    body: str,
    client: MistralProvider | None = None,
) -> dict[str, Any]:
    """Fast Small-tier rubric for narrative synthesis and technical depth."""
    from app.core.config import WRITER_QUALITY_LLM_ENABLED
    from app.modules.ai.llm_purpose_router import get_llm_digest_client

    if not WRITER_QUALITY_LLM_ENABLED:
        return {
            "model": "disabled",
            "reader_value": None,
            "narrative_synthesis": None,
            "technical_depth": None,
            "issues": [],
        }
    text_body = (body or "").strip()
    if not text_body:
        return {
            "model": "skipped",
            "reader_value": None,
            "narrative_synthesis": None,
            "technical_depth": None,
            "issues": ["empty body"],
        }
    llm: MistralProvider = client or get_llm_digest_client()
    # Root-caused 2026-09-09 (Fable review, grounded in a real 17,255-char
    # AlgoChess draft): the old 12,000-char cap cut off before the piece's
    # last three sections -- the rubric was grading roughly the first 70% of
    # a long article and never seeing the rest at all. 40,000 chars covers
    # any realistic article body (LLM_MAX_SOURCE_CHARS, the writer's own
    # source-material cap, is 48,000) with a comfortable margin, while still
    # bounding token cost far below sending the full raw digest.
    snippet = text_body[:40000]
    try:
        from app.core.config import LLM_TEMP_RESEARCH

        messages = [
            {"role": "system", "content": _QUALITY_RUBRIC},
            {
                "role": "user",
                "content": (f"Title: {title}\n\nBody:\n{snippet}\n\nReturn JSON only."),
            },
        ]
        scores, parsed, missing = _graded_scores(llm, messages, temperature=LLM_TEMP_RESEARCH)
        issues = [str(i).strip() for i in (parsed.get("issues") or []) if str(i).strip()][:6]
        if missing:
            logger.warning("LLM rubric returned partial scores; missing %s", missing)
            issues.append(
                "rubric returned no score for "
                + ", ".join(missing)
                + " (twice) — treated as failing; re-grade on revision"
            )
        issues.extend(_dimension_issues(scores))
        return {
            "model": "llm_rubric_partial" if missing else "llm_rubric",
            **{dim: scores[dim] for dim in _QUALITY_DIMS},
            "issues": issues,
        }
    except Exception as exc:
        logger.warning("LLM quality grade failed: %s", exc, exc_info=True)
        fallback = dict(_FALLBACK_QUALITY)
        fallback["error"] = str(exc)[:200]
        return fallback


def _clamp_score(value: Any) -> int | None:  # noqa: ANN401 -- arbitrary model-emitted score value, coerced via float()
    try:
        n = round(float(value))
    except (TypeError, ValueError):
        return None
    return max(1, min(5, n))


def quality_needs_revision(quality: dict[str, Any], *, min_score: int) -> bool:
    """True when any LLM dimension falls below the revision threshold."""
    for key in _QUALITY_DIMS:
        score = quality.get(key)
        if score is not None and int(score) < min_score:
            return True
    return False


# Root-caused 2026-09-09 (AlgoChess, post-self-grounding-fix): a recomposed
# article stated "under Algorand's rules only the address that created an
# application can submit a transaction that updates or deletes it" as flat
# fact -- false (any account can submit that call; success depends entirely
# on the application's own approval-program logic). No deterministic gate
# catches this class: it isn't a claim attributed to a specific fetched
# record (the record-attribution gate in unsourced_specifics_gate.py is
# blind to it by construction), it's the model asserting something wrong
# from its OWN general knowledge. When the operator asked the same model
# family that exact question directly and open-endedly, it answered
# correctly -- the knowledge exists, nothing in the pipeline ever asked for
# it. This is a SEPARATE call (not a 5th dimension bolted onto
# _QUALITY_RUBRIC above) for failure isolation, its own token budget (a
# claim list with per-claim reasoning needs more room than the 4-dimension
# rubric's shared cap), and because "strict editor grading prose" is the
# wrong persona for a correctness audit.
#
# Deliberately a claim-EXTRACTION prompt, not an open "does anything look
# wrong" question: a vague open question reproduces the same skim behavior
# that missed the error in the first place (the model only caught it when
# forced to attend to that EXACT claim). Extraction forces the same
# attention the operator's direct question got. Deliberately NOT a 1-5
# score either -- forcing a score on "is this correct" invites the same
# lazy default-high grading that let a separate deterministic gate score
# 0.98 "factuality" on the article that actually contained the original
# fabrication; a free per-claim verdict is more likely to produce a real
# answer.
#
# Deliberately scoped to PROTOCOL/MECHANICS claims only (how Algorand/AVM/
# ASA/app-call/consensus mechanics work as a general system), explicitly
# excluding project-specific facts the piece attributes to a source ("the
# FAQ says...", a fetched on-chain record) -- the grader has no access to
# sources, so treating every sourced claim as "unverifiable" would just
# have it relitigate the deterministic gates' job and flag true, correctly
# sourced claims as suspicious.
_FACTCHECK_PROMPT = (
    "You are auditing an Algorand-focused news draft for factual correctness in "
    "its PROTOCOL/TECHNICAL-MECHANICS claims — a separate task from grading its "
    "prose quality.\n\n"
    "List every claim in the piece about how the Algorand PROTOCOL, the AVM, "
    "smart contracts/applications, ASAs, or consensus actually work AS A GENERAL "
    "MECHANISM. Do NOT include: project-specific facts the piece attributes to a "
    "source ('the FAQ says...', 'the page states...', a quoted on-chain record) "
    "— those are sourcing questions for a different check, not general-knowledge "
    "questions; or anything the piece already hedges as unverified/undisclosed.\n\n"
    "For each protocol/mechanics claim you find, verify it against your own "
    "knowledge — anchored on these current figures, which override anything "
    "older you recall: Algorand produces a new block roughly every 2.8 "
    "seconds with finality in that same block, so a window stated in rounds "
    "converts at 2.8 seconds per round (7,200 rounds is about 5.6 hours; a "
    "piece saying 'about three seconds' and 'roughly five hours' for 7,200 "
    "rounds is internally inconsistent but neither figure is wrong on its "
    "own) — and give it a verdict:\n"
    '- "correct": accurate as stated.\n'
    '- "overstated": broadly right but asserts more certainty or scope than is '
    "actually true.\n"
    '- "wrong": factually incorrect.\n\n'
    "Also include any internal contradiction — two passages in the SAME piece "
    "stating incompatible facts about the same thing — as its own claim entry.\n\n"
    "Output a single JSON object with exactly this shape:\n"
    '{"claims": [{"claim": "the exact sentence or clause, verbatim", "verdict": '
    '"correct", "why": "one line, only for overstated/wrong entries"}]}\n'
    "List every protocol/mechanics claim you evaluated, INCLUDING ones scored "
    '"correct" — an empty claims list on a piece that discusses smart-contract '
    "or protocol mechanics is itself a sign nothing was actually checked, not "
    "evidence everything is fine; if the piece truly makes no such claims, "
    'return {"claims": []} and nothing else.\n'
    "JSON SAFETY: Return JSON only — no markdown fences or prose. In claim/why "
    "strings use single quotes for any quoted text, or avoid double quotes "
    "entirely; never emit unescaped double quotes inside JSON string values."
)

_FALLBACK_FACTCHECK: dict[str, Any] = {
    "model": "llm_factcheck_error",
    "claims": None,
}


def check_factual_claims(
    *, title: str, body: str, client: MistralProvider | None = None
) -> dict[str, Any]:
    """Audit the piece's protocol/technical-mechanics claims against the model's own knowledge — catches a confidently-wrong general-knowledge assertion that no source-grounding gate can see (see the module-level 2026-09-09 note above _FACTCHECK_PROMPT). ``claims: None`` (as opposed to ``[]``) marks "the model never actually returned a claims list" (a disabled/skipped/errored/missing-key run) so a caller never mistakes silence for a clean bill of health — the same missing-key-is-not-empty stance quality_needs_revision's callers already take (2026-07-16 partial-scores incident, CLAUDE.md invariant 8)."""
    from app.core.config import WRITER_QUALITY_LLM_ENABLED
    from app.modules.ai.llm_purpose_router import get_llm_digest_client

    if not WRITER_QUALITY_LLM_ENABLED:
        return {"model": "disabled", "claims": None}
    text_body = (body or "").strip()
    if not text_body:
        return {"model": "skipped", "claims": None}
    llm: MistralProvider = client or get_llm_digest_client()
    try:
        from app.core.config import LLM_TEMP_RESEARCH

        messages = [
            {"role": "system", "content": _FACTCHECK_PROMPT},
            {
                "role": "user",
                "content": (f"Title: {title}\n\nBody:\n{text_body}\n\nReturn JSON only."),
            },
        ]
        parsed = llm.chat_json_object(messages, temperature=LLM_TEMP_RESEARCH, max_tokens=2000)
        if not isinstance(parsed, dict):
            raise ValueError("non-object LLM factcheck response")
        claims = parsed.get("claims")
        if not isinstance(claims, list):
            # A response with no parseable claims list is a missing-key case,
            # not "nothing wrong" -- retry once, same stance as _graded_scores.
            retry = llm.chat_json_object(messages, temperature=LLM_TEMP_RESEARCH, max_tokens=2000)
            claims = retry.get("claims") if isinstance(retry, dict) else None
            if not isinstance(claims, list):
                fallback = dict(_FALLBACK_FACTCHECK)
                fallback["error"] = "no claims list after retry"
                return fallback
        clean_claims = [c for c in claims if isinstance(c, dict) and c.get("claim")]
        return {"model": "llm_factcheck", "claims": clean_claims}
    except Exception as exc:
        logger.warning("LLM factcheck failed: %s", exc, exc_info=True)
        fallback = dict(_FALLBACK_FACTCHECK)
        fallback["error"] = str(exc)[:200]
        return fallback


def factcheck_concerns(factcheck: dict[str, Any]) -> list[dict[str, str]]:
    """Claims scored overstated/wrong — the actionable subset of check_factual_claims' output."""
    claims = factcheck.get("claims")
    if not isinstance(claims, list):
        return []
    return [
        c for c in claims if isinstance(c, dict) and c.get("verdict") in ("wrong", "overstated")
    ]


def factcheck_needs_revision(factcheck: dict[str, Any]) -> bool:
    """True only when a claim is scored WRONG — "overstated" is surfaced as feedback but does not force a revision pass on its own (a hedge is a smaller ask than a rewrite; only a flat error forces one)."""
    claims = factcheck.get("claims")
    if not isinstance(claims, list):
        return False
    return any(isinstance(c, dict) and c.get("verdict") == "wrong" for c in claims)


def _format_concern(c: dict[str, Any]) -> str:
    claim = str(c.get("claim", "")).strip()
    verdict = c.get("verdict")
    why = str(c.get("why", "")).strip()
    label = "factual concern (wrong)" if verdict == "wrong" else "factual concern (overstated)"
    text = f'{label}: "{claim}"'
    if why:
        text += f" — {why}"
    return text


def factcheck_issues(factcheck: dict[str, Any]) -> list[str]:
    """Every overstated/wrong claim as a human-readable string, for the persisted review record (review["factual_concerns"]) a human sees in the review queue regardless of whether it forced a revision — see factcheck_forcing_issues for the narrower, revision-triggering subset."""
    return [_format_concern(c) for c in factcheck_concerns(factcheck)]


def factcheck_forcing_issues(factcheck: dict[str, Any]) -> list[str]:
    """Only WRONG-verdict claims, formatted — the subset that actually forces a revision pass. "overstated" is real feedback (factcheck_issues carries it into the persisted record) but is a smaller ask than a rewrite, so it does not by itself put the draft back into the revision loop."""
    claims = factcheck.get("claims")
    if not isinstance(claims, list):
        return []
    return [
        _format_concern(c) for c in claims if isinstance(c, dict) and c.get("verdict") == "wrong"
    ]
