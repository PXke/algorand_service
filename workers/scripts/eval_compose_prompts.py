"""Prompt-change eval harness for the scrape-article compose prompt.

COSTS MONEY (real Mistral API calls) and needs the full worker env (Mistral
key, and ideally Cassandra/Typesense for the grader's novelty/relevance
signals, which degrade gracefully to neutral defaults without them). Run it
by hand before/after editing `_ARTICLE_FORMAT_RULES` or the system prompt in
`app.modules.ai.mistral_compose` — never in CI.

Usage:

    cd workers && python -m scripts.eval_compose_prompts
    cd workers && python -m scripts.eval_compose_prompts \
        --fixtures static_landing_page,stale_source_old_figures
    cd workers && python -m scripts.eval_compose_prompts --out-dir /tmp/run_after

Each run writes one Markdown file per fixture (title/summary/tags/body plus
the grader's subscores and issues) into an output directory stamped with the
compose PROMPT_VERSION and a UTC timestamp. Compare two runs — one before,
one after a prompt edit — with a plain diff:

    diff -ru scripts/eval_compose_output/<old_run> scripts/eval_compose_output/<new_run>

This is a fixed set of frozen inputs read with your own eyes, not a scored
gate — there is no pass/fail. It exists so a prompt edit's effect on real
output is visible instead of only guessed at by watching prod.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from scripts.eval_compose_fixtures import FIXTURES, ComposeFixture


def _run_one(fixture: ComposeFixture) -> str:
    """Compose + grade one fixture; return a Markdown report block."""
    from app.modules.ai.mistral_client import MistralError
    from app.modules.ai.mistral_compose import compose_scrape_article_mistral
    from app.modules.newspaper.article_grader import grade_article_draft

    try:
        result = compose_scrape_article_mistral(
            service_name=fixture.service_name,
            source_url=fixture.source_url,
            page_title=fixture.page_title,
            page_text=fixture.page_text,
            txid=fixture.txid,
            round_num=fixture.round_num,
            diff=fixture.diff,
            is_first_snapshot=fixture.is_first_snapshot,
        )
    except MistralError as exc:
        return f"# {fixture.name}\n\nCOMPOSE FAILED: {exc}\n"

    try:
        grade = grade_article_draft(
            title=result.title,
            body=result.body,
            source_url=fixture.source_url,
            tags=result.tags,
        )
    except Exception as exc:  # grader needs live services for some subscores
        grade = {"error": str(exc)}

    words = len(result.body.split())
    lines = [
        f"# {fixture.name}",
        "",
        f"- watch for: {fixture.watch_for}",
        f"- prompt_version: {getattr(result, 'prompt_version', '')}",
        f"- words: {words}",
        f"- grade: {grade}",
        "",
        f"## Title\n{result.title}",
        "",
        f"## Summary\n{result.summary}",
        "",
        f"## Tags\n{', '.join(result.tags)}",
        "",
        "## Body",
        result.body,
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--fixtures",
        default="all",
        help="comma-separated fixture names, or 'all' (default)",
    )
    ap.add_argument(
        "--out-dir",
        default="",
        help="output directory (default: scripts/eval_compose_output/<UTC timestamp>)",
    )
    args = ap.parse_args()

    from app.core.config import mistral_configured
    from app.modules.ai.mistral_compose import PROMPT_VERSION

    if not mistral_configured():
        print("MISTRAL_ENABLED / MISTRAL_API_KEY not configured — nothing to eval.")
        sys.exit(1)

    if args.fixtures == "all":
        selected = list(FIXTURES)
    else:
        wanted = {n.strip() for n in args.fixtures.split(",") if n.strip()}
        selected = [f for f in FIXTURES if f.name in wanted]
        missing = wanted - {f.name for f in selected}
        if missing:
            print(f"unknown fixture(s): {sorted(missing)}")
            sys.exit(1)

    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
    out_dir = (
        Path(args.out_dir)
        if args.out_dir
        else Path(__file__).parent / "eval_compose_output" / f"{stamp}_{PROMPT_VERSION}"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"PROMPT_VERSION={PROMPT_VERSION}")
    print(f"writing {len(selected)} fixture(s) to {out_dir}")

    for fixture in selected:
        print(f"  composing {fixture.name} ...")
        report = _run_one(fixture)
        (out_dir / f"{fixture.name}.md").write_text(report)

    print(f"done. diff two runs with: diff -ru <old_run_dir> {out_dir}")


if __name__ == "__main__":
    main()
