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
    "being solved — NOT foundation name-drops without explaining why Algorand fits.\n\n"
    "Output a single JSON object with exactly these keys:\n"
    '{"narrative_synthesis": 3, "technical_depth": 3, "issues": ["short fix"]}\n'
    "JSON SAFETY: Return JSON only — no markdown fences or prose. In issue strings "
    "use single quotes for any quoted text, or avoid double quotes entirely; never "
    "emit unescaped double quotes inside JSON string values.\n"
    "List 0-4 short, actionable issues only when a score is below 4."
)

_FALLBACK_QUALITY = {
    "model": "llm_rubric_error",
    "narrative_synthesis": 2,
    "technical_depth": 2,
    "issues": [
        "quality rubric could not be parsed — weave facts into connected journalism, "
        "not dictionary-style summaries",
        "explain Algorand layer-1 mechanics vs legacy friction; put multi-item data "
        "in a Markdown table (Concept / Real-World Implication columns)",
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
        narrative = _clamp_score(parsed.get("narrative_synthesis"))
        technical = _clamp_score(parsed.get("technical_depth"))
        issues = [
            str(i).strip()
            for i in (parsed.get("issues") or [])
            if str(i).strip()
        ][:6]
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
        return {
            "model": "llm_rubric",
            "narrative_synthesis": narrative,
            "technical_depth": technical,
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
    """True when either LLM dimension falls below the revision threshold."""
    for key in ("narrative_synthesis", "technical_depth"):
        score = quality.get(key)
        if score is not None and int(score) < min_score:
            return True
    return False
