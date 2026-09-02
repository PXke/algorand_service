"""Individual static-analysis checks, each a standalone module plugged into scan.py's orchestrator.

Every check module exposes one function:

    def run(path: str) -> dict

Called with the path to the file already on disk (never a URL, never
executed). Must NEVER execute, import, or otherwise run the target as code —
same hard rule as scan.py itself. Must NEVER raise: catch everything
internally and return `{"error": str(exc)}` instead, so one check failing
never crashes the whole scan or gets silently dropped (CLAUDE.md invariant:
empty is not "none found" -- a failed check must say so, not vanish).

The returned dict is merged into the top-level report under this module's
own key (see scan.py's CHECKS list) and additionally may include:

    "risk_score": float in [0.0, 1.0]  -- this check's own opinion of how
        suspicious the target is, 0 = no concern, 1 = certain/confirmed bad.
        Omit the key entirely if this check found nothing risk-relevant
        (absence is treated as 0.0 by risk_score.py, but an explicit 0.0 is
        also fine and reads more clearly in the JSON).
    "caution_notes": list[str] -- short, human-readable reasons a payer
        should look twice, if any. Empty list or omitted key if none.

risk_score.py aggregates every check's risk_score/caution_notes into one
top-level verdict; it does not know about any individual check's internal
fields, so a new check module needs no registration anywhere except being
added to scan.py's CHECKS list.
"""
