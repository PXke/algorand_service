"""Writer's spike: let the research agent refuse to write a story at all.

Owner-proposed 2026-07-17, straight after the AlgoGlyph incident: the writer's
research trace showed every dormancy signal (token minted 2021, 12 holders
ever, last transfer 2024, a template landing page) — but its only available
move was "write the article anyway", so it inflated dead evidence into a
launch story. A newsroom editor can spike a story; this gives the writer the
same power.

Calling the ``spike_story`` tool raises :class:`StorySpikedError`, which
deliberately escapes the tool loop's failure-swallowing (`mistral_client`
re-raises it) and aborts the compose. The task layer resolves the queue row
with terminal status ``aborted_by_writer`` — no fallback compose, no retry
this cycle — and the admin Sessions/Queue views carry the writer's stated
reason so a human can override (recompose) or dead-end the domain.
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


SPIKE_STORY_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "spike_story",
        "description": (
            "Refuse to write this article — the editorial spike. Call this when "
            "your research shows there is NO real story here: the project is dead "
            "or abandoned (years-old token, dust holders, no recent on-chain or "
            "web activity, template landing page), the verifiable substance is too "
            "thin to fill an honest article, we already covered exactly this, or "
            "you cannot verify the central claim and writing would mean inventing. "
            "Spiking is a SUCCESS, not a failure — a fabricated or hollow article "
            "damages the paper far more than a missing one. This ends the session "
            "immediately: nothing you write after it will be published, and an "
            "editor reviews your reason. Do NOT use it for fixable friction (a "
            "tool erroring, one page not loading) — report those with "
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
                        "The concrete evidence for spiking, citing what you found "
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


# The writer sometimes calls spike_story to NARRATE a decision NOT to spike
# ("No spike needed. I have verified six wallets…"), misusing a terminal tool as
# commentary — which threw away a fully-researched, correct article (Pera Wallet
# recompose, 2026-07-20: it had verified Pera is actively developed, then aborted
# on a "no spike needed" spike call). A self-negating spike must NOT abort.
_NON_SPIKE_MARKERS = (
    "no spike", "not spike", "don't spike", "do not spike", "no need to spike",
    "spike is not needed", "spike not needed", "no spiking", "should not spike",
    "won't spike", "will not spike",
)


def spike_story_handler(category: str = "", reason: str = "", **_: Any) -> dict[str, Any]:
    low = (reason or "").strip().lower()
    if any(m in low for m in _NON_SPIKE_MARKERS):
        # Not a real spike — the reason negates it. Return a corrective nudge so
        # the agentic loop continues and the writer writes the article instead of
        # discarding it.
        return {
            "spiked": False,
            "note": (
                "You called spike_story but your reason says a spike is NOT needed. "
                "spike_story ABORTS the article and is terminal — only call it when "
                "there is genuinely no story. You appear to have enough verified "
                "material, so do NOT spike: write the full article now."
            ),
        }
    raise StorySpikedError(reason, category)
