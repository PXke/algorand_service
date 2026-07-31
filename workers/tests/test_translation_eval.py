"""Tests for translation_eval.py's Layer 1/2 checks and Candidate dispatch.

Mirrors test_local_translate.py's approach: no real model/tokenizer/torch
object is ever touched. Layer 1/2 checks are pure functions, tested
directly. Candidate dispatch (translate_block_with) is tested against a
stub Candidate whose translate_fn just records its call args -- the same
"monkeypatch above the model call" seam test_local_translate.py uses, here
applied to a plain function reference instead of module-level state.
"""

from __future__ import annotations

from collections.abc import Callable

from app.modules.ai import translation_eval as te


def _stub_candidate(
    fn: Callable[[str], str] | None = None,
) -> tuple[te.Candidate, list[tuple[str, str, str, bool]]]:
    """A Candidate whose translate_fn just uppercases (by default) and records every call's args, so a test can assert exactly what translate_block_with dispatched."""
    calls: list[tuple[str, str, str, bool]] = []

    def _translate(text: str, src_lang: str, tgt_lang: str, *, sample: bool = False) -> str:
        calls.append((text, src_lang, tgt_lang, sample))
        return (fn or (lambda t: t.upper()))(text)

    return (
        te.Candidate(
            name="stub", license="test-only", translate_fn=_translate, unload_fn=lambda: None
        ),
        calls,
    )


# --- digit_consistency -----------------------------------------------------


def test_digit_consistency_matches_native_digit_glyphs() -> None:
    """Extended Arabic-Indic digits in the translated text are read natively -- no glyph normalization step needed."""
    result = te.digit_consistency("The price is $0.18 today.", "قیمت امروز ۰.۱۸ دلار است.")
    assert result.total == 1
    assert result.grounded == 1
    assert result.ungrounded == ()


def test_digit_consistency_vacuous_when_translation_drops_the_number_entirely() -> None:
    """No numeric claim in the translated text at all is vacuously grounded, per numeric_entailment_score's own contract -- distinct from a claim present but wrong."""
    result = te.digit_consistency("TVL reached $310M this week.", "TVL بدون تغییر باقی ماند.")
    assert result.total == 0
    assert result.score == 1.0


def test_digit_consistency_documents_the_percent_glyph_gap() -> None:
    """Known, documented gap: a native percent sign (٪) isn't recognized as the '%' suffix, so the value falls back to the 'plain' class and no longer matches the source's 'percent' anchor -- this is the exact caveat in digit_consistency's docstring, verified here so a future fix to the gap is a visible test change, not a silent behavior shift."""
    result = te.digit_consistency("Up 4.2% this week.", "۴.۲٪ افزایش یافت.")
    assert "۴.۲" in result.ungrounded


def test_digit_consistency_handles_french_comma_decimal_with_spaced_magnitude() -> None:
    """Regression for a real false positive hit running the eval harness 2026-07-31: MiLMMT rendered "1.4M" as French "1,4 M" (comma decimal, space before the magnitude letter) -- must not shatter into two ungrounded fragments."""
    result = te.digit_consistency(
        "DeFi tooling got 1.4M ALGO.", "Les outils DeFi ont reçu 1,4 M d'ALGO."
    )
    assert result.ungrounded == ()


def test_digit_consistency_handles_space_and_period_thousands_grouping() -> None:
    """Regression for real false positives hit running the eval harness 2026-07-31: French renders 900,000 as "900 000" (space-grouped), Spanish as "900.000" (period-grouped) -- neither is an ungrounded value, just a different grouping character than the English source's comma."""
    fr = te.digit_consistency("900,000 ALGO allocated.", "900 000 ALGO alloué.")
    es = te.digit_consistency("900,000 ALGO allocated.", "900.000 ALGO asignado.")
    assert fr.ungrounded == ()
    assert es.ungrounded == ()


def test_digit_consistency_still_treats_a_short_decimal_as_a_decimal() -> None:
    """The thousands-vs-decimal heuristic hinges on an exact 3-digit group after the separator -- a genuine 1-2 digit decimal (12.5, or its comma-decimal equivalent) must not be swept up as if it were a thousands separator."""
    result = te.digit_consistency("Grew 12.5% this quarter.", "Creció un 12,5% este trimestre.")
    assert result.ungrounded == ()


# --- list_table_row_count / structural_alignment ----------------------------


def test_list_table_row_count_counts_items_and_rows_separately() -> None:
    """A list block counts as (N, 0) and a table block as (0, N) -- never conflated."""
    block = "- one\n- two\n- three"
    assert te.list_table_row_count(block) == (3, 0)

    table = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    assert te.list_table_row_count(table) == (0, 3)


def test_list_table_row_count_skips_fenced_code() -> None:
    """A line that looks like a list item inside a code fence must not be counted."""
    block = "```\n- not a real list item\n```"
    assert te.list_table_row_count(block) == (0, 0)


def test_structural_alignment_flags_block_count_mismatch() -> None:
    """Two source blocks collapsed into one translated block is a block-count mismatch."""
    result = te.structural_alignment("para one\n\npara two", "only one paragraph")
    assert result.source_blocks == 2
    assert result.translated_blocks == 1
    assert not result.block_count_matches


def test_structural_alignment_flags_dropped_list_item() -> None:
    """Block count can match while a list item silently disappears inside the one surviving block -- row_diffs is what catches that."""
    src = "- one\n- two\n- three"
    translated = "- un\n- deux"  # third item dropped
    result = te.structural_alignment(src, translated)
    assert result.block_count_matches
    assert result.row_diffs == (te.RowCountDiff(0, "list", 3, 2),)


def test_structural_alignment_clean_case_has_no_diffs() -> None:
    """Matching block count and matching row counts -> no diffs reported at all."""
    src = "| a | b |\n| --- | --- |\n| 1 | 2 |"
    translated = "| c | d |\n| --- | --- |\n| 3 | 4 |"
    result = te.structural_alignment(src, translated)
    assert result.block_count_matches
    assert result.row_diffs == ()


# --- dominant_term -----------------------------------------------------------


def test_dominant_term_picks_the_most_frequent_content_word() -> None:
    """Stopwords are excluded, so the repeated content word wins over incidental filler."""
    text = "Each agent submits data. The lead agent handles the feed. Any agent can be replaced."
    assert te.dominant_term(text) == "agent"


def test_dominant_term_folds_simple_plurals_into_the_singular() -> None:
    """Plural and singular forms of the same word share one count via the crude trailing-s fold; the returned spelling is whichever form was seen first."""
    text = "agents agents agent"  # 2 plural + 1 singular, same stem
    assert te.dominant_term(text) == "agents"


def test_dominant_term_empty_for_stopword_only_text() -> None:
    """Text with no qualifying content word returns "" rather than raising or picking a stopword."""
    assert te.dominant_term("the a an of to in on") == ""


# --- back_translation_consistency -------------------------------------------


def test_back_translation_consistency_flags_the_agent_agency_style_drift() -> None:
    """Regression shape for the real MiLMMT defect: term correct in one source block, drifted in another after the round trip."""
    source_blocks = ["The agent submits data.", "Any agent can be replaced without downtime."]
    back_translated_blocks = [
        "The agent submits data.",
        "Any agency can be replaced without downtime.",
    ]
    result = te.back_translation_consistency(source_blocks, back_translated_blocks, "agent")
    assert result.blocks_checked == 2
    assert result.blocks_consistent == 1
    assert result.drifted_block_indices == (1,)
    assert result.consistency == 0.5


def test_back_translation_consistency_accepts_listed_synonyms() -> None:
    """A listed synonym counts as consistent, not a drift -- e.g. 'purse' standing in for 'wallet'."""
    source_blocks = ["The wallet holds funds."]
    back_translated_blocks = ["The purse holds funds."]
    result = te.back_translation_consistency(
        source_blocks, back_translated_blocks, "wallet", synonyms=("purse",)
    )
    assert result.drifted_block_indices == ()
    assert result.consistency == 1.0


def test_back_translation_consistency_only_checks_blocks_containing_the_term() -> None:
    """A back-translated block is free to read completely differently when its SOURCE block never mentioned the tracked term."""
    source_blocks = ["No mention here.", "The agent handles this."]
    back_translated_blocks = ["Completely unrelated back-translation.", "The agent handles this."]
    result = te.back_translation_consistency(source_blocks, back_translated_blocks, "agent")
    assert result.blocks_checked == 1
    assert result.consistency == 1.0


def test_back_translation_consistency_vacuous_when_term_never_appears() -> None:
    """A term absent from every source block yields consistency 1.0 (vacuous), not 0.0 or an error."""
    result = te.back_translation_consistency(["no match"], ["no match"], "agent")
    assert result.blocks_checked == 0
    assert result.consistency == 1.0


# --- translate_block_with (structural pass-throughs + Candidate dispatch) --


def test_translate_block_with_passes_through_code_fences_untouched() -> None:
    """A fenced code block never reaches the candidate's translate_fn."""
    candidate, calls = _stub_candidate()
    block = "```\nsome code\n```"
    assert te.translate_block_with(candidate, block, "en", "fr") == block
    assert calls == []


def test_translate_block_with_passes_through_bare_urls_untouched() -> None:
    """A bare URL line never reaches the candidate's translate_fn."""
    candidate, calls = _stub_candidate()
    block = "https://example.com/page"
    assert te.translate_block_with(candidate, block, "en", "fr") == block
    assert calls == []


def test_translate_block_with_strips_and_reapplies_heading_prefix() -> None:
    """Only the heading's content is sent to translate_fn; the '##' prefix is reapplied outside the model call."""
    candidate, calls = _stub_candidate()
    result = te.translate_block_with(candidate, "## Hello World", "en", "fr")
    assert result == "## HELLO WORLD"
    assert calls == [("Hello World", "en", "fr", False)]


def test_translate_block_with_forwards_sample_flag() -> None:
    """sample=True on translate_block_with reaches the candidate's translate_fn unchanged."""
    candidate, calls = _stub_candidate()
    te.translate_block_with(candidate, "plain text", "en", "fr", sample=True)
    assert calls == [("plain text", "en", "fr", True)]


# --- translate_block_with: list/table cell-level splitting ------------------


def test_translate_block_with_splits_list_items_and_preserves_prefixes() -> None:
    """Each item's text is translated in isolation and reassembled with its original bullet prefix -- this is the actual fix for the survey's list-collapse finding."""
    candidate, calls = _stub_candidate()
    block = "- one\n- two\n- three"
    result = te.translate_block_with(candidate, block, "en", "fr")
    assert result == "- ONE\n- TWO\n- THREE"
    assert calls == [
        ("one", "en", "fr", False),
        ("two", "en", "fr", False),
        ("three", "en", "fr", False),
    ]


def test_translate_block_with_preserves_numbered_list_prefixes() -> None:
    """Numbered list markers (1., 2)) are preserved the same way bullet markers are."""
    candidate, calls = _stub_candidate()
    block = "1. first\n2. second"
    result = te.translate_block_with(candidate, block, "en", "fr")
    assert result == "1. FIRST\n2. SECOND"
    assert calls == [("first", "en", "fr", False), ("second", "en", "fr", False)]


def test_translate_block_with_splits_table_cells_and_skips_the_separator_row() -> None:
    """Each cell is translated in isolation and reassembled into the row -- the all-dashes separator row is never sent to the model at all, just passed through verbatim. This is the actual fix for the survey's table-destruction finding."""
    candidate, calls = _stub_candidate()
    block = "| Category | Count |\n| --- | --- |\n| tools | 18 |\n| training | 12 |"
    result = te.translate_block_with(candidate, block, "en", "fr")
    assert result == ("| CATEGORY | COUNT |\n| --- | --- |\n| TOOLS | 18 |\n| TRAINING | 12 |")
    assert calls == [
        ("Category", "en", "fr", False),
        ("Count", "en", "fr", False),
        ("tools", "en", "fr", False),
        ("18", "en", "fr", False),
        ("training", "en", "fr", False),
        ("12", "en", "fr", False),
    ]


def test_translate_block_with_leaves_a_plain_paragraph_untouched_by_splitting() -> None:
    """A regular paragraph (not a pure list or table block) still goes through as one whole-block call -- splitting must not misfire on ordinary prose."""
    candidate, calls = _stub_candidate()
    te.translate_block_with(
        candidate, "Just a normal sentence with a dash - not a list.", "en", "fr"
    )
    assert calls == [("Just a normal sentence with a dash - not a list.", "en", "fr", False)]


# --- CANDIDATES registry sanity ---------------------------------------------


def test_candidates_registry_covers_every_article_translation_language() -> None:
    """Every language the newspaper actually publishes translations for has a candidate list -- no silent gap."""
    from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS

    assert set(te.CANDIDATES) == set(ARTICLE_TRANSLATION_LANGS)


def test_candidates_registry_has_at_least_two_entries_per_language() -> None:
    """A one-candidate language defeats the whole point of a comparison survey."""
    for lang, candidates in te.CANDIDATES.items():
        assert len(candidates) >= 2, f"{lang} has only {len(candidates)} candidate(s)"


def test_candidates_registry_names_are_unique_within_a_language() -> None:
    """Duplicate names within one language would silently collapse the runner's per-(candidate, language) report files."""
    for lang, candidates in te.CANDIDATES.items():
        names = [c.name for c in candidates]
        assert len(names) == len(set(names)), f"duplicate candidate name(s) for {lang}: {names}"


def test_milmmt_baseline_is_the_same_object_across_languages() -> None:
    """Candidate is a frozen, hashable dataclass and the SAME MiLMMT Candidate instance is reused across every language that includes it -- this is what lets the runner's worklist dedupe by object identity and load it exactly once for the whole run, not once per language."""
    shared = [
        candidates[0]
        for lang, candidates in te.CANDIDATES.items()
        if lang != "ps" and candidates[0].name.startswith("milmmt")
    ]
    assert len(shared) >= 2
    assert len({id(c) for c in shared}) == 1
