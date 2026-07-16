"""LLM rubric for qualitative journalism dimensions the heuristic cannot score."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_QUALITY_RUBRIC = (
    "You are a strict editor grading an Algorand-focused news draft.\n"
    "Score ONLY these dimensions from 1 (poor) to 5 (excellent):\n"
    "- narrative_synthesis: cohesive journalism weaving findings together — NOT "
    "comma-separated feature dumps, NOT generic press-release tone, NOT dictionary "
    "definitions of curriculum pillars or feature lists.\n"
    "- technical_depth: bridges the story to Algorand layer-1 mechanics (throughput, "
    "consensus, ASA/tokenization, state proofs, etc.) and names the legacy friction "
    "being solved — NOT foundation name-drops without explaining why Algorand fits.\n"
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
    '{"narrative_synthesis": 3, "technical_depth": 3, "critical_distance": 3, '
    '"repetition": 3, "issues": ["short fix"]}\n'
    "JSON SAFETY: Return JSON only — no markdown fences or prose. In issue strings "
    "use single quotes for any quoted text, or avoid double quotes entirely; never "
    "emit unescaped double quotes inside JSON string values.\n"
    "List 0-4 short, actionable issues only when a score is below 4."
)

_FALLBACK_QUALITY = {
    "model": "llm_rubric_error",
    "narrative_synthesis": 2,
    "technical_depth": 2,
    "critical_distance": 2,
    "repetition": 2,
    "issues": [
        "quality rubric could not be parsed — weave facts into connected journalism, "
        "not dictionary-style summaries",
        "explain Algorand layer-1 mechanics vs legacy friction; put multi-item data "
        "in a Markdown table (Concept / Real-World Implication columns)",
        "name the actual risk/tradeoff instead of just relaying the subject's own "
        "marketing framing",
    ],
}


def _parse_quality_response(raw: Any) -> dict[str, Any] | None:
    """Parse rubric JSON; salvage fenced/prose-wrapped objects when possible."""
    from app.modules.ai.mistral_client import _parse_json_object

    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    return _parse_json_object(raw.strip())


def grade_article_quality_llm(
    *,
    title: str,
    body: str,
    client: Any | None = None,
) -> dict[str, Any]:
    """Fast Small-tier rubric for narrative synthesis and technical depth."""
    from app.core.config import WRITER_QUALITY_LLM_ENABLED
    from app.modules.ai.mistral_client import MistralClient, get_mistral_digest_client

    if not WRITER_QUALITY_LLM_ENABLED:
        return {
            "model": "disabled",
            "narrative_synthesis": None,
            "technical_depth": None,
            "issues": [],
        }
    text_body = (body or "").strip()
    if not text_body:
        return {
            "model": "skipped",
            "narrative_synthesis": None,
            "technical_depth": None,
            "issues": ["empty body"],
        }
    mistral: MistralClient = client or get_mistral_digest_client()
    snippet = text_body[:12000]
    try:
        from app.core.config import MISTRAL_TEMP_RESEARCH

        messages = [
            {"role": "system", "content": _QUALITY_RUBRIC},
            {
                "role": "user",
                "content": (
                    f"Title: {title}\n\nBody:\n{snippet}\n\n"
                    "Return JSON only."
                ),
            },
        ]
        parsed = mistral.chat_json_object(
            messages,
            temperature=MISTRAL_TEMP_RESEARCH,
            max_tokens=800,
        )
        if not isinstance(parsed, dict):
            raise ValueError("non-object LLM grade")
        dims = ("narrative_synthesis", "technical_depth", "critical_distance", "repetition")
        scores = {k: _clamp_score(parsed.get(k)) for k in dims}
        # Partial responses must not silently pass (2026-07-16: a real draft
        # got narrative_synthesis=3 with the other three dimensions null —
        # graded on 1 of 4, and quality_needs_revision treats None as fine).
        # One retry, then any still-missing dimension FAILS CLOSED at 2
        # (below every revision threshold), same stance as _FALLBACK_QUALITY.
        if any(v is None for v in scores.values()):
            retry = mistral.chat_json_object(
                messages,
                temperature=MISTRAL_TEMP_RESEARCH,
                max_tokens=800,
            )
            if isinstance(retry, dict):
                for k in dims:
                    if scores[k] is None:
                        scores[k] = _clamp_score(retry.get(k))
                if isinstance(retry.get("issues"), list) and not parsed.get("issues"):
                    parsed["issues"] = retry["issues"]
        missing = [k for k, v in scores.items() if v is None]
        for k in missing:
            scores[k] = 2
        narrative = scores["narrative_synthesis"]
        technical = scores["technical_depth"]
        critical_distance = scores["critical_distance"]
        repetition = scores["repetition"]
        issues = [
            str(i).strip()
            for i in (parsed.get("issues") or [])
            if str(i).strip()
        ][:6]
        if missing:
            logger.warning("LLM rubric returned partial scores; missing %s", missing)
            issues.append(
                "rubric returned no score for "
                + ", ".join(missing)
                + " (twice) — treated as failing; re-grade on revision"
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
        return {
            "model": "llm_rubric_partial" if missing else "llm_rubric",
            "narrative_synthesis": narrative,
            "technical_depth": technical,
            "critical_distance": critical_distance,
            "repetition": repetition,
            "issues": issues,
        }
    except Exception as exc:
        logger.warning("LLM quality grade failed: %s", exc, exc_info=True)
        fallback = dict(_FALLBACK_QUALITY)
        fallback["error"] = str(exc)[:200]
        return fallback


def _clamp_score(value: Any) -> int | None:
    try:
        n = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(1, min(5, n))


def quality_needs_revision(quality: dict[str, Any], *, min_score: int) -> bool:
    """True when any LLM dimension falls below the revision threshold."""
    for key in ("narrative_synthesis", "technical_depth", "critical_distance", "repetition"):
        score = quality.get(key)
        if score is not None and int(score) < min_score:
            return True
    return False
