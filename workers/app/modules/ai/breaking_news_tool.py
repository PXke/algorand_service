"""Writer-declared breaking news: replaces the deterministic keyword classifier.

Root-caused 2026-07-17: classify_publish_tier's keyword scan (any of "down",
"lost ", "network", "100,000"...) mistagged ordinary positive infrastructure
claims as network incidents — an interview boasting Algorand's "zero downtime"
track record got tagged NETWORK_INCIDENT and shipped titled "Breaking:" about
a campaign that had already run its course months earlier. At least 4 more
live articles carried the same false "breaking" tag from the same trigger.
That keyword path is now permanently off (BREAKING_TIER_ENABLED=False).

Owner decision: if BREAKING is going to exist at all, the WRITER decides,
having actually read and researched the material — not a page-text substring
scan run before compose even starts. mark_breaking_news is that judgment
call, made available throughout research and writing like the other
self-report tools (report_compose_issue, abort_article).
"""

from __future__ import annotations

from typing import Any

MARK_BREAKING_NEWS_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "mark_breaking_news",
        "description": (
            "Flag THIS story as breaking news — urgent enough to publish immediately "
            "instead of waiting for the next scheduled slot. Reserve for what an "
            "editor would interrupt the print run for: an active exploit or loss of "
            "funds happening now, a live network outage or consensus failure, a "
            "credible imminent security threat, or a first-of-its-kind regulatory/"
            "protocol event with immediate real-world stakes. Do NOT call for routine "
            "launches, feature updates, funding news, partnerships, or content merely "
            "using words like 'launch'/'major' — and never for anything praising "
            "uptime or the ABSENCE of an incident, regardless of urgent-sounding "
            "language nearby. If the story would read the same today or in three "
            "days, it is not breaking. Does not block or change what you write — call "
            "once confident, then continue; fact-checking and review still happen, "
            "this only affects how fast a verified story reaches readers."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "The specific, concrete fact making this urgent right now "
                        "(e.g. 'consensus halted at round X per official status page', "
                        "not a vague sense of importance) — a human reviewer reads this "
                        "to judge whether the urgency claim holds up"
                    ),
                },
            },
            "required": ["reason"],
        },
    },
}


def mark_breaking_news_handler(reason: str = "", **_: object) -> dict[str, Any]:
    """Record the writer's mark_breaking_news tool call and its stated reason."""
    return {"marked_breaking": True, "reason": (reason or "").strip()[:400]}


def breaking_reason_from_trace(trace: list[dict] | None) -> str | None:
    """Last mark_breaking_news call's reason, or None if never called. Scanned post-hoc (like the dead-link/chain-entity gates) rather than threaded through a mutable context — the tool never blocks or mutates the draft, it only needs to be visible to the caller once compose finishes."""
    reason: str | None = None
    for entry in trace or ():
        if entry.get("tool") != "mark_breaking_news":
            continue
        result = entry.get("result") or {}
        if isinstance(result, dict) and result.get("marked_breaking"):
            reason = str(result.get("reason") or "").strip() or "breaking news"
    return reason
