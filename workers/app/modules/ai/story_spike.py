"""Writer's abort: let the research agent refuse to write a story at all.

Owner-proposed 2026-07-17, straight after the AlgoGlyph incident: the writer's
research trace showed every dormancy signal (token minted 2021, 12 holders
ever, last transfer 2024, a template landing page) — but its only available
move was "write the article anyway", so it inflated dead evidence into a
launch story. A newsroom editor can spike a story; this gives the writer the
same power.

The tool is named ``abort_article`` (2026-07-20 rename: the old ``spike_story``
was newsroom jargon the model didn't reliably map to "terminate", and it misused
it as a status-report step — calling it with "No spike needed" and throwing away
a correctly-researched wallet guide). Calling it raises :class:`StorySpikedError`,
which deliberately escapes the tool loop's failure-swallowing (`mistral_client`
re-raises it) and aborts the compose. The task layer resolves the queue row with
terminal status ``aborted_by_writer`` — no fallback compose, no retry this cycle
— and the admin Sessions/Queue views carry the writer's stated reason so a human
can override (recompose) or dead-end the domain.
"""

from __future__ import annotations

from typing import Any

SPIKE_CATEGORIES = (
    "dead_project",
    "insufficient_sources",
    "not_newsworthy",
    "duplicate_coverage",
    "factual_concerns",
)


class StorySpikedError(Exception):
    """The writer declined to compose this story. Not a failure — a judgment."""

    def __init__(self, reason: str, category: str = "not_newsworthy") -> None:
        self.reason = (reason or "").strip()[:500]
        self.category = category if category in SPIKE_CATEGORIES else "not_newsworthy"
        super().__init__(f"{self.category}: {self.reason}")


ABORT_ARTICLE_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "abort_article",
        "description": (
            "Abort this article — stop and refuse to write it. This is TERMINAL "
            "and IRREVERSIBLE: it ends the session immediately, nothing you write "
            "afterward is published, and your research is discarded. Call it ONLY "
            "when your research shows there is no real story: the project is dead "
            "or abandoned (years-old token, dust holders, no recent on-chain or web "
            "activity, template landing page), the verifiable substance is too thin "
            "for an honest article, we already covered exactly this, or you cannot "
            "verify the central claim and writing would mean inventing. Aborting is "
            "a SUCCESS when the alternative is a hollow or fabricated article. "
            "CRITICAL: if you have enough material to write, do NOT call this — just "
            "write the article. NEVER call it to report progress, confirm your "
            "sources, or say that an abort is not needed. Do NOT use it for fixable "
            "friction (a tool erroring, one page not loading) — report those with "
            "report_compose_issue and keep researching."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "enum": list(SPIKE_CATEGORIES),
                    "description": (
                        "dead_project = subject is abandoned/inactive; "
                        "insufficient_sources = not enough verifiable material; "
                        "not_newsworthy = real but nothing worth reporting; "
                        "duplicate_coverage = we already published this story; "
                        "factual_concerns = central claims cannot be verified"
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        "The concrete evidence for aborting, citing what you found "
                        "(e.g. 'asset minted 2021, 12 holders, last transfer "
                        "2024-03, site is a template page') — an editor reads "
                        "this to decide whether to override or retire the source"
                    ),
                },
            },
            "required": ["category", "reason"],
        },
    },
}

# Backwards-compat alias (older imports referenced SPIKE_STORY_SCHEMA).
SPIKE_STORY_SCHEMA = ABORT_ARTICLE_SCHEMA

# The writer sometimes calls the abort tool to NARRATE a decision NOT to abort
# ("No spike needed. I have verified six wallets…"), misusing a terminal tool as
# commentary — which threw away a fully-researched, correct article (Pera Wallet
# recompose, 2026-07-20: it had verified Pera is actively developed, then aborted
# on a "no spike needed" call). A self-negating abort must NOT terminate. Covers
# both the old "spike" wording and the new "abort" wording.
_NON_ABORT_MARKERS = (
    "no spike", "not spike", "don't spike", "do not spike", "no need to spike",
    "spike is not needed", "spike not needed", "no spiking", "should not spike",
    "won't spike", "will not spike",
    "no abort", "not abort", "don't abort", "do not abort", "no need to abort",
    "abort is not needed", "abort not needed", "no aborting", "should not abort",
    "won't abort", "will not abort",
)


def abort_article_handler(category: str = "", reason: str = "", **_: Any) -> dict[str, Any]:
    low = (reason or "").strip().lower()
    if any(m in low for m in _NON_ABORT_MARKERS):
        # Not a real abort — the reason negates it. Return a corrective nudge so
        # the agentic loop continues and the writer writes the article instead of
        # discarding it.
        return {
            "aborted": False,
            "note": (
                "You called abort_article but your reason says an abort is NOT "
                "needed. abort_article is TERMINAL and discards the article — only "
                "call it when there is genuinely no story. You appear to have enough "
                "verified material, so do NOT abort: write the full article now."
            ),
        }
    raise StorySpikedError(reason, category)


# Backwards-compat alias (older imports referenced spike_story_handler).
spike_story_handler = abort_article_handler
