"""Pattern-1 backfill for already-stored translation corruption: a stray
space MiLMMT sometimes regenerated between a markdown link's `]` and `(`
(`[text] (url)` instead of `[text](url)`), which renders as literal
bracket/paren text instead of a link. Fixed for NEW translations by
`_repair_inline_links` in `app.modules.ai.local_translate` (commit 018facb);
this is the retroactive backfill for translations stored BEFORE that fix
landed.

Pure deterministic text transform, same regex the shipped fix runs
unconditionally over every translated block: `re.sub(r"\\]\\s+\\(", "](", text)`.
Per that function's own comment, that space is never valid markdown, so
collapsing it can only repair syntax, never change meaning -- no model
inference needed, unlike pattern 2 (untranslated anchor), which is NOT
handled by this script (see scan_translation_link_corruption.py's pattern-2
output for that follow-up; it needs a real MiLMMT call per affected anchor to
repair correctly, and is deliberately out of scope here).

Safety: defaults to a DRY RUN (prints every article/lang/field that would
change plus a before/after snippet, touches nothing). Set DRY_RUN=0 to
actually write. Only ever touches the `translations` map entries that
contain the defect -- everything else on the row (including OTHER languages'
translations, which are untouched keys in the same map) is left alone, via
`ArticlesStmts.UPDATE_TRANSLATIONS`'s additive `translations = translations + ?`.

Usage (from the `workers` directory, or with an equivalent PYTHONPATH):

    PYTHONPATH=.:../shared python workers/scratch/backfill_translation_link_spacing.py            # dry run
    DRY_RUN=0 PYTHONPATH=.:../shared python workers/scratch/backfill_translation_link_spacing.py   # real update
"""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime

from algorand_shared.article_statements import ArticlesStmts

from app.core.cassandra import get_cassandra_session

KS = "algorand_platform"
_YEARS = range(2024, datetime.now(tz=UTC).year + 1)
_SPACE_BEFORE_PAREN = re.compile(r"\]\s+\(")

DRY_RUN = os.environ.get("DRY_RUN", "1") != "0"


def _fix(text: str) -> str:
    return _SPACE_BEFORE_PAREN.sub("](", text or "")


def main() -> None:
    session = get_cassandra_session()
    session.default_fetch_size = 2000

    print(f"DRY_RUN={DRY_RUN}", flush=True)

    articles_scanned = 0
    pairs_fixed = 0
    occurrences_fixed = 0
    shown_diffs = 0

    for year in _YEARS:
        rows = session.execute(ArticlesStmts.LIST_IDS_BY_STATUS, ("published", year))
        for id_row in rows:
            full = session.execute(ArticlesStmts.GET_FULL_BY_ID, (id_row.article_id,)).one()
            if full is None or full.status != "published" or full.published_at is None:
                continue
            articles_scanned += 1
            translations = dict(full.translations or {})
            if not translations:
                continue

            updates: dict[str, str] = {}
            for lang, blob in translations.items():
                try:
                    decoded = json.loads(blob)
                except (ValueError, TypeError):
                    continue

                changed = False
                fixed_fields = {}
                pair_occurrences = 0
                for field in ("title", "summary", "body"):
                    original = decoded.get(field, "") or ""
                    n = len(_SPACE_BEFORE_PAREN.findall(original))
                    if n:
                        pair_occurrences += n
                        fixed_fields[field] = _fix(original)
                        changed = True
                    else:
                        fixed_fields[field] = original

                if not changed:
                    continue

                pairs_fixed += 1
                occurrences_fixed += pair_occurrences

                if shown_diffs < 25:
                    for field, new_text in fixed_fields.items():
                        old_text = decoded.get(field, "") or ""
                        if old_text == new_text:
                            continue
                        m = _SPACE_BEFORE_PAREN.search(old_text)
                        if not m:
                            continue
                        start = max(0, m.start() - 40)
                        end = min(len(old_text), m.end() + 40)
                        print(
                            f"  {full.article_id} lang={lang} field={field}\n"
                            f"    before: {old_text[start:end]!r}\n"
                            f"    after:  {new_text[start:end]!r}",
                            flush=True,
                        )
                    shown_diffs += 1

                new_blob = dict(decoded)
                new_blob.update(fixed_fields)
                updates[lang] = json.dumps(new_blob, ensure_ascii=False)

            if updates and not DRY_RUN:
                session.execute(
                    ArticlesStmts.UPDATE_TRANSLATIONS,
                    (
                        updates,
                        {},  # translated_titles: an additive empty-map merge is a no-op,
                        # correct here -- this backfill only repairs link syntax inside
                        # existing translation blobs, it never touches the lightweight
                        # title/summary companion column (migration 088).
                        full.status,
                        full.year,
                        full.published_at,
                        full.article_id,
                    ),
                )

    print(flush=True)
    print(f"articles scanned (status='published'): {articles_scanned}", flush=True)
    print(f"article x lang pairs fixed: {pairs_fixed}", flush=True)
    print(f"total '] (' occurrences collapsed: {occurrences_fixed}", flush=True)
    print("DRY RUN -- nothing written. Re-run with DRY_RUN=0 to apply." if DRY_RUN else "WRITES APPLIED.", flush=True)
    print("\nBACKFILL_DONE", flush=True)


if __name__ == "__main__":
    main()
