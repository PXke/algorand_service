"""Frozen source fixtures for scripts/eval_translate_candidates.py.

Each fixture is a short English markdown excerpt (2-3 paragraphs, not a full
article -- see the plan's cost-aware scope rationale: MiLMMT measured up to
51 minutes for one language on one long article, so a survey multiplying
candidates x languages x articles x runs must use excerpts or it never
finishes). Synthetic content modeled on the newspaper's real domain and on a
real, confirmed defect (MiLMMT rendered "agent(s)" as عاملان, correct, in a
Farsi article's title, but آژانس‌های, "agencies", wrong, in its body) -- not
verbatim corpus text.

Deliberately no ``language`` field: an excerpt is English source content
translatable into any target language, and Layer 2's back-translation check
(translation_eval.back_translation_consistency) works the same way
regardless of target language -- it always reads its own result back in
English. Language selection is a run parameter (--languages) in the runner,
not a fixture property, so 8 near-duplicate per-language fixtures aren't
needed to cover 8 languages.

Keep this list SMALL (5-10) and STABLE, same discipline as
eval_compose_fixtures.py: add a fixture only when a real failure mode needs
a permanent regression check; don't grow this into a general test corpus.

Manual Google Translate comparison: Google Translate is not wired into
translation_eval.CANDIDATES on purpose -- it's a paid external API, not a
self-hosted model we CPU-cap and never-both-load, so it doesn't fit this
harness's architecture (see translation_eval.py's module docstring) or its
license/cost accounting. But as an informal REFERENCE point read by a human
-- especially valuable for a language nobody on the team reads -- it's
useful for free. To use it: paste one fixture's ``excerpt`` field (exactly
as written here, so it's the same input as every automated candidate) into
Google Translate by hand, and read the result alongside a candidate's
output in the eval report. Not scored, not automated, not run by the
harness -- just another set of eyes on the same fixed input.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class TranslationFixture:
    """One fixture case for the offline translation-candidate evaluation harness."""

    name: str
    excerpt: str
    dominant_term: str
    # What to eyeball / what the automated checks specifically target here --
    # not enforced, a reading guide for whoever opens the report.
    watch_for: str = field(default="")


FIXTURES: tuple[TranslationFixture, ...] = (
    TranslationFixture(
        name="agent_term_consistency",
        excerpt=(
            "## Oracle Network Adds New Data Agents\n\n"
            "Gora's oracle network onboarded three new data agents this "
            "quarter, expanding coverage for price feeds used by Algorand "
            "DeFi protocols. Each agent independently submits price "
            "observations, and the network's consensus layer discards "
            "outliers before finalizing a value on-chain.\n\n"
            "The lead agent for the ALGO/USD feed has processed over 40,000 "
            "submissions since launch. According to the team, adding "
            "redundant agents reduces the risk that a single agent's "
            "downtime disrupts a feed, since any agent can be replaced "
            "without pausing the network."
        ),
        dominant_term="agent",
        watch_for=(
            "the real, confirmed MiLMMT defect this fixture exists to catch: "
            "'agent' correct in one block, drifted to a near-synonym like "
            "'agency' in another. back_translation_consistency should flag "
            "any block where that happens."
        ),
    ),
    TranslationFixture(
        name="list_block",
        excerpt=(
            "## AlgoKit 4.0 Feature Highlights\n\n"
            "AlgoKit 4.0 introduces several developer-facing improvements:\n\n"
            "- A TypeScript client generator for ARC-56 contracts\n"
            "- Box storage helpers for large on-chain state\n"
            "- A local sandbox that boots in under 5 seconds\n"
            "- Typed error messages for failed application calls\n\n"
            "Developers can opt into the new generator without changing "
            "existing contract code."
        ),
        dominant_term="contract",
        watch_for=(
            "4 list items in, 4 list items out. local_translate.py's own "
            "docstring flags list-item preservation as UNPROVEN for both "
            "production engines -- structural_alignment's row_diffs is the "
            "check that would catch a dropped or merged item."
        ),
    ),
    TranslationFixture(
        name="table_block",
        excerpt=(
            "## xGov Period 12 Results\n\n"
            "| Category | Proposals | ALGO Allocated |\n"
            "| --- | --- | --- |\n"
            "| DeFi tooling | 18 | 1.4M |\n"
            "| Developer education | 12 | 900K |\n"
            "| Community events | 14 | 650K |\n"
            "| Infrastructure | 9 | 780K |\n\n"
            "Voter turnout was 38% of eligible governors, up from 31% last "
            "period."
        ),
        dominant_term="proposal",
        watch_for=(
            "4 data rows in, 4 data rows out (same unproven-table risk as "
            "list_block), plus digit_consistency should hold on every cell "
            "value and the two turnout percentages."
        ),
    ),
    TranslationFixture(
        name="dense_numbers",
        excerpt=(
            "## Weekly Market Snapshot\n\n"
            "ALGO traded at $0.18, up 4.2% over the past 7 days. Total "
            "value locked across Algorand DeFi protocols reached $310M, "
            "led by Tinyman at $95M and Folks Finance at $62M. Daily "
            "active addresses averaged 48,000, a 12% increase from the "
            "prior week."
        ),
        dominant_term="protocol",
        watch_for=(
            "primarily a Layer-1 digit_consistency fixture: every currency "
            "figure, percentage, and the raw address count should survive "
            "with its value intact, regardless of which numeral glyphs the "
            "target language renders them in."
        ),
    ),
    TranslationFixture(
        name="plain_prose_control",
        excerpt=(
            "## Algorand Foundation Announces Partnership\n\n"
            "The Algorand Foundation today announced a partnership with a "
            "regional payments processor to explore stablecoin settlement "
            "rails. The processor's network already reaches several "
            "markets in Southeast Asia, and the partnership's first phase "
            "will focus on pilot transactions rather than a public launch. "
            "Terms were not disclosed."
        ),
        dominant_term="partnership",
        watch_for=(
            "control case: single paragraph, no list/table, one repeated "
            "term with no known defect pattern. A candidate failing here is "
            "a stronger signal than failing on the harder fixtures above."
        ),
    ),
)


def get(name: str) -> TranslationFixture:
    """Look up a named translation fixture, raising KeyError if it doesn't exist."""
    for fx in FIXTURES:
        if fx.name == name:
            return fx
    raise KeyError(f"no such fixture: {name!r}; known: {[f.name for f in FIXTURES]}")
