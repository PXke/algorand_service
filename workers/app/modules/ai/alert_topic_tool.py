"""Writer-confirmed alert topics: keyword topic classification demotes to a routing hint; the reader-facing consequences require the writer's judgment.

Root-caused 2026-07-18: classify_publish_topic's context+alarm heuristic
tagged the Algorand Foundation's own homepage rebrand as SCAM_ALERT — the
page quotes a 2021 research paper asking "is this approach vulnerable to
malicious servers?", and "malicious" near ordinary "opt-in" vocabulary
satisfied the scan. Second false scam labeling in a week (same disease the
BREAKING tier had before mark_breaking_news replaced its keyword path).

Division of labor after this change:
- The keyword topic still ROUTES: queue priority, mandatory-review — cheap,
  pre-compose, and a false positive there only costs a review slot.
- The reader-facing consequences — the scam-alert/incident article tag and
  the scam-topic match-key carve-out (body domains/cashtags becoming edit-
  routing keys) — activate ONLY when the writer, having actually read and
  researched the material, confirms via confirm_alert_topic.
"""

from __future__ import annotations

from typing import Any

ALERT_KINDS = ("scam_alert", "network_incident")

CONFIRM_ALERT_TOPIC_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "confirm_alert_topic",
        "description": (
            "Confirm THIS story genuinely is an active alert: 'scam_alert' for a "
            "real scam/phishing/drainer campaign targeting users (wallet drainer "
            "live now, fake site impersonating a project, malicious airdrop), or "
            "'network_incident' for a real degradation of the chain or core "
            "infrastructure (outage, consensus stall, exploit in progress). Call "
            "it only when the SUBJECT of the story is the alert itself — never "
            "because security words appear in the material (a research paper "
            "discussing 'malicious servers', a security-feature announcement, or "
            "a post-mortem of a long-resolved incident is NOT an active alert). "
            "Confirming controls the article's alert tag and follow-up routing; "
            "it does not change what you write or skip any review."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": list(ALERT_KINDS),
                    "description": "Which alert class this story is",
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "The concrete, current fact making this an active alert "
                        "(e.g. 'drainer site X live now, address Y receiving "
                        "victim funds') — a reviewer reads this to judge the call"
                    ),
                },
            },
            "required": ["kind", "reason"],
        },
    },
}


def confirm_alert_topic_handler(kind: str = "", reason: str = "", **_: object) -> dict[str, Any]:
    """Validate and record the writer's confirm_alert_topic tool call."""
    kind = (kind or "").strip().lower()
    if kind not in ALERT_KINDS:
        return {"confirmed": False, "error": f"kind must be one of {ALERT_KINDS}"}
    return {"confirmed": True, "kind": kind, "reason": (reason or "").strip()[:400]}


def confirmed_alert_from_trace(trace: list[dict] | None) -> str | None:
    """Last confirmed alert kind, or None if the writer never confirmed one.

    Post-hoc trace scan, same shape as breaking_reason_from_trace.
    """
    kind: str | None = None
    for entry in trace or ():
        if entry.get("tool") != "confirm_alert_topic":
            continue
        result = entry.get("result") or {}
        if isinstance(result, dict) and result.get("confirmed"):
            kind = str(result.get("kind") or "").strip() or None
    return kind
