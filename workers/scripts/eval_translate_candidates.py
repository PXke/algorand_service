r"""Offline candidate-model comparison harness for the translation "promising-ranking" survey.

Runs entirely on the same production box's CPU/RAM budget as real
translation batches (no separate offline machine) -- reuses
app.modules.ai.local_translate's own _MAX_THREADS cap and, for the two
baseline candidates, its own loaded model singletons. Run this BY HAND,
off-peak, and never while a real translation batch might be in flight --
same convention scripts/eval_compose_prompts.py already uses for its own
cost reasons, and there is no lock here to stop an overlap the way
local_translate_lock.py does for production. Never run in CI.

Usage:

    cd workers && python -m scripts.eval_translate_candidates
    cd workers && python -m scripts.eval_translate_candidates \\
        --fixtures agent_term_consistency --languages fa \\
        --candidates "milmmt-46-4b (prod baseline)" --runs 1
    cd workers && python -m scripts.eval_translate_candidates --mode sampling --runs 3

Two run modes (see translation_eval.Candidate's ``sample`` docstring for why
a naive repeat wouldn't test anything under deterministic decoding):

  - "deterministic" (default): each fixture translated once per candidate,
    across every fixture selected. This already covers cross-article
    recurrence -- whether a failure mode is systematic, not one unlucky
    excerpt -- without a separate mode, since the default fixture set spans
    several unrelated excerpts.
  - "sampling": temperature sampling enabled, --runs repeats (default 3) of
    the SAME excerpt per candidate/language -- tests whether the model's own
    decoding uncertainty is wide enough to drift on some runs but not
    others.

Writes one Markdown file per (candidate, language) pair into a timestamped
output dir (scripts/eval_translate_output/<stamp>_<mode>/), covering every
selected fixture. Candidates load at most ONCE for the whole run (grouped
across every language they appear in, not once per language) and unload
before the next candidate -- mirrors local_translate.translate_article_batch's
own load-once-per-group discipline.

Nothing is buffered until the end: every file is opened and its header
written before the first case runs, and each case's result is appended and
flushed to disk the moment it finishes -- MiLMMT alone can take minutes per
case, so waiting for a whole (candidate, language) pair, let alone the
whole run, before anything is readable would defeat the point of watching
results land. `_progress.md` in the output dir is a single running index,
one line per finished case, written as you go -- `tail -f` it to watch the
whole run without guessing which per-file to open, then dive into the
per-(candidate, language) file once something in the index looks worth a
closer read.

This is a fixed set of frozen excerpts and automatable structural/back-
translation checks read with your own eyes -- not a scored gate. See
translation_eval.py's module docstring for the full layered framework and
why Layer 3 (fluency) has no function here at all.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from scripts.eval_translate_fixtures import FIXTURES, TranslationFixture

if TYPE_CHECKING:
    from app.modules.ai.translation_eval import Candidate

logger = logging.getLogger(__name__)


def _run_case(
    candidate: Candidate, language: str, fixture: TranslationFixture, *, sample: bool
) -> dict:
    """Translate one fixture into ``language`` with ``candidate``, back-translate it, and run every Layer 1/2 check. Never raises -- a failure is caught and returned as an {"error": ...} entry so one bad case doesn't abort the whole run."""
    from app.modules.ai.llm_compose import split_markdown_blocks
    from app.modules.ai.translation_eval import (
        back_translation_consistency,
        digit_consistency,
        structural_alignment,
        translate_block_with,
    )

    try:
        src_blocks = split_markdown_blocks(fixture.excerpt)
        translated_blocks = [
            translate_block_with(candidate, b, "en", language, sample=sample) for b in src_blocks
        ]
        translated = "\n\n".join(translated_blocks)
        back_blocks = [
            translate_block_with(candidate, b, language, "en", sample=sample)
            for b in translated_blocks
        ]
        back_translated = "\n\n".join(back_blocks)
    except Exception as exc:
        logger.error(
            "case failed: candidate=%s lang=%s fixture=%s",
            candidate.name,
            language,
            fixture.name,
            exc_info=True,
        )
        return {"error": str(exc)}

    return {
        "translated": translated,
        "back_translated": back_translated,
        "structural": structural_alignment(fixture.excerpt, translated),
        "digits": digit_consistency(fixture.excerpt, translated),
        "backtrans": back_translation_consistency(src_blocks, back_blocks, fixture.dominant_term),
    }


def _format_case(result: dict, run_idx: int, runs: int) -> str:
    label = f"run {run_idx + 1}/{runs}" if runs > 1 else "run"
    if "error" in result:
        return f"**{label}: FAILED** -- {result['error']}\n"

    structural = result["structural"]
    digits = result["digits"]
    backtrans = result["backtrans"]
    lines = [
        f"**{label}**",
        f"- structural: {structural.source_blocks} -> {structural.translated_blocks} blocks "
        f"({'OK' if structural.block_count_matches else 'MISMATCH'})"
        + (f", row diffs: {list(structural.row_diffs)}" if structural.row_diffs else ""),
        f"- digits: {digits.grounded}/{digits.total} grounded"
        + (f", ungrounded: {list(digits.ungrounded)}" if digits.ungrounded else ""),
        f"- back-translation term '{backtrans.term}': "
        f"{backtrans.blocks_consistent}/{backtrans.blocks_checked} consistent"
        + (
            f", DRIFTED blocks: {list(backtrans.drifted_block_indices)}"
            if backtrans.drifted_block_indices
            else ""
        ),
        "",
        "<details><summary>translated</summary>\n\n" + result["translated"] + "\n\n</details>",
        "",
        "<details><summary>back-translated</summary>\n\n"
        + result["back_translated"]
        + "\n\n</details>",
        "",
    ]
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--fixtures", default="all", help="comma-separated fixture names, or 'all' (default)"
    )
    ap.add_argument(
        "--languages",
        default="all",
        help="comma-separated target language codes, or 'all' (default: every "
        "language in translation_eval.CANDIDATES)",
    )
    ap.add_argument(
        "--candidates",
        default="all",
        help="comma-separated candidate names (see translation_eval.CANDIDATES), or 'all' (default)",
    )
    ap.add_argument(
        "--mode",
        choices=("deterministic", "sampling"),
        default="deterministic",
        help="'sampling' enables temperature sampling for --runs repeats per case. "
        "Cross-article recurrence is not a separate mode -- running "
        "'deterministic' across the default multi-fixture set already covers it.",
    )
    ap.add_argument(
        "--runs",
        type=int,
        default=0,
        help="repeat count for --mode sampling (default 3 if unset); forced to 1 "
        "in deterministic mode, where repeats would be byte-identical",
    )
    ap.add_argument(
        "--out-dir",
        default="",
        help="output directory (default: scripts/eval_translate_output/<UTC timestamp>_<mode>)",
    )
    return ap.parse_args()


def _resolve_fixtures(names: str) -> list[TranslationFixture]:
    if names == "all":
        return list(FIXTURES)
    wanted = {n.strip() for n in names.split(",") if n.strip()}
    selected = [f for f in FIXTURES if f.name in wanted]
    missing = wanted - {f.name for f in selected}
    if missing:
        logger.error("unknown fixture(s): %s", sorted(missing))
        sys.exit(1)
    return selected


def _resolve_languages(names: str, known: list[str]) -> list[str]:
    if names == "all":
        return known
    wanted = {lang.strip() for lang in names.split(",") if lang.strip()}
    unknown = wanted - set(known)
    if unknown:
        logger.error("unknown language(s): %s; known: %s", sorted(unknown), known)
        sys.exit(1)
    return [lang for lang in known if lang in wanted]


def _resolve_runs(mode: str, requested: int) -> int:
    runs = requested or (3 if mode == "sampling" else 1)
    if mode == "deterministic" and runs != 1:
        logger.info(
            "deterministic mode always uses runs=1 (repeats would be byte-identical) -- "
            "ignoring --runs=%d",
            runs,
        )
        runs = 1
    return runs


def _build_worklist(
    languages: list[str],
    fixtures: list[TranslationFixture],
    candidate_filter: set[str] | None,
) -> dict[Candidate, list[tuple[str, TranslationFixture]]]:
    """Worklist keyed by candidate object (Candidate is a frozen, hashable dataclass) -> its (language, fixture) cases, so a candidate shared across several languages (MiLMMT, M2M-100) loads exactly ONCE for the whole run instead of once per language. Exits with an error if --candidates names something not in CANDIDATES for any selected language."""
    from app.modules.ai.translation_eval import CANDIDATES

    worklist: dict[Candidate, list[tuple[str, TranslationFixture]]] = {}
    known_names: set[str] = set()
    for language in languages:
        for candidate in CANDIDATES[language]:
            known_names.add(candidate.name)
            if candidate_filter is not None and candidate.name not in candidate_filter:
                continue
            for fixture in fixtures:
                worklist.setdefault(candidate, []).append((language, fixture))

    if candidate_filter is not None:
        unknown = candidate_filter - known_names
        if unknown:
            logger.error(
                "unknown candidate(s): %s; known: %s", sorted(unknown), sorted(known_names)
            )
            sys.exit(1)
    return worklist


def _flags(result: dict) -> list[str]:
    """Short machine-greppable tags for a case's result -- empty list means nothing stood out."""
    if "error" in result:
        return ["error"]
    flags = []
    if not result["structural"].block_count_matches:
        flags.append("structural_mismatch")
    if result["structural"].row_diffs:
        flags.append("row_diff")
    if result["digits"].ungrounded:
        flags.append("digits_ungrounded")
    if result["backtrans"].drifted_block_indices:
        flags.append("backtrans_drift")
    return flags


def _append_progress(
    progress_path: Path,
    candidate: Candidate,
    language: str,
    fixture: TranslationFixture,
    run_idx: int,
    runs: int,
    result: dict,
) -> None:
    """One line per finished case, appended and flushed immediately -- this file is the thing to `tail -f`, not any individual report."""
    stamp = datetime.now(tz=UTC).strftime("%H:%M:%SZ")
    label = f"run {run_idx + 1}/{runs}" if runs > 1 else "run"
    flags = _flags(result)
    flag_text = f" [{','.join(flags)}]" if flags else " [clean]"
    safe_name = candidate.name.replace("/", "_").replace(" ", "_")
    line = (
        f"- {stamp} {candidate.name} -> {language} / {fixture.name} / {label}{flag_text} "
        f"-> {safe_name}__{language}.md\n"
    )
    with progress_path.open("a") as f:
        f.write(line)
        f.flush()


def _write_reports_for_candidate(
    candidate: Candidate,
    cases: list[tuple[str, TranslationFixture]],
    *,
    mode: str,
    runs: int,
    sample: bool,
    out_dir: Path,
    progress_path: Path,
) -> None:
    """Writes and flushes each case's result to its (candidate, language) file the moment it finishes, and appends a summary line to progress_path -- nothing here waits for the whole candidate, or even the whole language, to be done before becoming readable."""
    by_language: dict[str, list[TranslationFixture]] = {}
    for language, fixture in cases:
        by_language.setdefault(language, []).append(fixture)

    for language, fixtures_for_lang in by_language.items():
        safe_name = candidate.name.replace("/", "_").replace(" ", "_")
        report_path = out_dir / f"{safe_name}__{language}.md"
        with report_path.open("w") as report:
            report.write(f"# {candidate.name} -> {language}\n\n")
            report.write(f"- license: {candidate.license}\n")
            report.write(f"- mode: {mode} (runs={runs})\n\n")
            report.flush()

            for fixture in fixtures_for_lang:
                report.write(f"## {fixture.name}\n")
                report.write(f"- watch for: {fixture.watch_for}\n\n")
                report.flush()
                for run_idx in range(runs):
                    logger.info(
                        "  %s / %s / %s / run %d ...",
                        candidate.name,
                        language,
                        fixture.name,
                        run_idx + 1,
                    )
                    result = _run_case(candidate, language, fixture, sample=sample)
                    report.write(_format_case(result, run_idx, runs) + "\n")
                    report.flush()
                    _append_progress(
                        progress_path, candidate, language, fixture, run_idx, runs, result
                    )


def main() -> None:
    """Parse CLI args, run the selected (candidate, language, fixture) cases, and write one Markdown report per (candidate, language) pair."""
    args = _parse_args()

    from app.modules.ai.translation_eval import CANDIDATES

    selected_fixtures = _resolve_fixtures(args.fixtures)
    selected_languages = _resolve_languages(args.languages, list(CANDIDATES))
    candidate_filter = (
        None
        if args.candidates == "all"
        else {n.strip() for n in args.candidates.split(",") if n.strip()}
    )
    runs = _resolve_runs(args.mode, args.runs)
    sample = args.mode == "sampling"

    worklist = _build_worklist(selected_languages, selected_fixtures, candidate_filter)
    if not worklist:
        logger.error("nothing to run -- check --languages/--candidates/--fixtures filters")
        sys.exit(1)

    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else Path(__file__).parent / "eval_translate_output" / f"{stamp}_{args.mode}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    total_cases = sum(len(cases) for cases in worklist.values()) * runs
    progress_path = out_dir / "_progress.md"
    progress_path.write_text(
        f"# eval_translate_candidates run: {stamp}\n\n"
        f"mode={args.mode} runs={runs} fixtures={len(selected_fixtures)} "
        f"candidates={len(worklist)} total_cases={total_cases}\n\n"
        "One line per finished case, appended as the run progresses -- "
        f"`tail -f {progress_path}` to watch live.\n\n"
    )

    logger.info(
        "mode=%s runs=%d fixtures=%d candidates=%d total_cases=%d -> %s (tail -f %s)",
        args.mode,
        runs,
        len(selected_fixtures),
        len(worklist),
        total_cases,
        out_dir,
        progress_path,
    )

    for candidate, cases in worklist.items():
        logger.info("loading %s (license: %s) ...", candidate.name, candidate.license)
        _write_reports_for_candidate(
            candidate,
            cases,
            mode=args.mode,
            runs=runs,
            sample=sample,
            out_dir=out_dir,
            progress_path=progress_path,
        )
        logger.info("unloading %s ...", candidate.name)
        candidate.unload_fn()

    logger.info("done. wrote reports for %d candidate(s) to %s", len(worklist), out_dir)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
