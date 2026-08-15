r"""Compose the same frozen article snapshot across every configured LLM provider, 3x each, and record token usage + grade for comparison -- entirely local (SessionRegisterSQLite), no prod/Celery/publish involvement.

Usage:

    cd workers && .venv/bin/python scripts/benchmark_compose_providers.py \
        [--snapshot scratch/lumirogue_snapshot.json] \
        [--runs 3] \
        [--providers mistral,deepseek,openai,kimi,glm,gemini]

Only providers with an API key actually configured (locally, via env) are
run -- others are skipped with a note, not silently faked. Requires a
reachable Redis (compose_lock) and Cassandra (several research tools) --
see docker-compose.yml at the repo root for a local stack, or point
REDIS_URL/CASSANDRA_* at wherever your local infra actually lives.

Every run's token usage, duration, and heuristic_grade land in one shared
SQLite file; a final summary prints per-provider averages. The full
transcripts stay in the SQLite file's `messages`/`trace` columns for a
closer read afterward (see SessionRegisterSQLite in session_register.py).
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from app.modules.ai.compose_runner import ArticleInput, compose
from app.modules.ai.session_register import SessionRegisterSQLite

_PROVIDER_KEY_CONFIG = {
    "mistral": "MISTRAL_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "kimi": "KIMI_API_KEY",
    "glm": "GLM_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def _provider_configured(name: str) -> bool:
    """True if this provider's API key is actually set locally -- never silently substitute a different provider for one that isn't configured."""
    from app.core import config

    key_const = _PROVIDER_KEY_CONFIG.get(name)
    return bool(key_const and getattr(config, key_const, "").strip())


def _load_article_input(snapshot_path: Path) -> ArticleInput:
    raw = json.loads(snapshot_path.read_text(encoding="utf-8"))
    known_fields = set(ArticleInput.__dataclass_fields__)
    kwargs = {k: v for k, v in raw.items() if k in known_fields}
    return ArticleInput(**kwargs)


def run_benchmark(
    *,
    snapshot_path: Path,
    db_path: Path,
    runs_per_provider: int,
    provider_names: list[str],
) -> list[dict]:
    """Run `runs_per_provider` composes for each configured provider in `provider_names` against the frozen snapshot at `snapshot_path`, all checkpointed into one SessionRegisterSQLite file at `db_path`. Returns a list of per-run summary dicts.

    Loop order is round-robin (one run per provider, then the next round) rather
    than provider-by-provider, so a comparison across every provider is available
    as early as possible instead of only after the first provider's full quota.
    """
    article_input = _load_article_input(snapshot_path)
    register = SessionRegisterSQLite(db_path)
    results: list[dict] = []

    configured = [p for p in provider_names if _provider_configured(p)]
    for p in provider_names:
        if p not in configured:
            print(f"skip {p}: no API key configured locally")  # noqa: T201

    for run_idx in range(runs_per_provider):
        for provider_name in configured:
            print(f"[{provider_name} run {run_idx + 1}/{runs_per_provider}] composing...")  # noqa: T201
            try:
                result = compose(
                    article_input=article_input,
                    provider_name=provider_name,
                    session_register=register,
                )
            except Exception as exc:  # a benchmark run failing must not abort the whole sweep
                print(f"[{provider_name} run {run_idx + 1}] FAILED: {exc}")  # noqa: T201
                results.append(
                    {
                        "provider": provider_name,
                        "run": run_idx + 1,
                        "error": str(exc),
                        "recorded_at": datetime.now(tz=UTC).isoformat(),
                    }
                )
                continue
            summary = {
                "provider": result.provider,
                "run": run_idx + 1,
                "model": result.model,
                "duration_ms": result.duration_ms,
                "usage": result.usage,
                "heuristic_grade": result.fields.heuristic_grade,
                "title": result.fields.title,
                "recorded_at": datetime.now(tz=UTC).isoformat(),
            }
            results.append(summary)
            print(  # noqa: T201
                f"[{provider_name} run {run_idx + 1}] done in {result.duration_ms}ms, "
                f"{result.usage['total_tokens']} tokens, grade="
                f"{(result.fields.heuristic_grade or {}).get('grade')}"
            )
    return results


def _print_summary(results: list[dict]) -> None:
    by_provider: dict[str, list[dict]] = {}
    for r in results:
        by_provider.setdefault(r["provider"], []).append(r)

    print("\n=== summary ===")  # noqa: T201
    for provider_name, runs in sorted(by_provider.items()):
        ok_runs = [r for r in runs if "error" not in r]
        failed = len(runs) - len(ok_runs)
        if not ok_runs:
            print(f"{provider_name}: {failed}/{len(runs)} failed, 0 succeeded")  # noqa: T201
            continue
        avg_tokens = sum(r["usage"]["total_tokens"] for r in ok_runs) / len(ok_runs)
        avg_ms = sum(r["duration_ms"] for r in ok_runs) / len(ok_runs)
        grades = [
            g for r in ok_runs if (g := (r["heuristic_grade"] or {}).get("grade")) is not None
        ]
        avg_grade = sum(grades) / len(grades) if grades else None
        grade_text = f"avg grade {avg_grade:.2f}" if avg_grade is not None else "no grade"
        print(  # noqa: T201
            f"{provider_name}: {len(ok_runs)}/{len(runs)} ok, "
            f"avg {avg_tokens:.0f} tokens, avg {avg_ms:.0f}ms, {grade_text}"
        )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", type=Path, default=Path("scratch/lumirogue_snapshot.json")
    )
    parser.add_argument(
        "--db", type=Path, default=Path("scratch/benchmark_compose_providers.sqlite")
    )
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument(
        "--providers", type=str, default="mistral,deepseek,openai,kimi,glm,gemini,anthropic"
    )
    args = parser.parse_args()

    if not args.snapshot.exists():
        raise SystemExit(
            f"{args.snapshot} not found -- run snapshot_compose_input.py first "
            "to freeze a real article's compose inputs."
        )

    provider_names = [p.strip() for p in args.providers.split(",") if p.strip()]
    results = run_benchmark(
        snapshot_path=args.snapshot,
        db_path=args.db,
        runs_per_provider=args.runs,
        provider_names=provider_names,
    )

    results_path = args.db.with_suffix(".results.json")
    results_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {len(results)} run summaries to {results_path}")  # noqa: T201
    print(f"full transcripts in {args.db}")  # noqa: T201
    _print_summary(results)


if __name__ == "__main__":
    main()
