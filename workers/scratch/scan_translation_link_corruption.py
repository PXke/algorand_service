"""Read-only corpus scan for the two MiLMMT inline-link translation defects
fixed (going forward) in `app.modules.ai.local_translate._repair_inline_links`
(commit 018facb): a stray `] (` space regenerated on an otherwise-valid
markdown link, and a multi-word link anchor left completely untranslated.
That fix only protects translations generated FROM NOW ON -- this script
finds how much of the ALREADY-STORED corpus (any language, any article) is
already affected by either defect, so a backfill can be scoped with real
numbers instead of a guess.

Detection logic is deliberately copy-pasted (not imported) from
`_repair_inline_links` / `_looks_translatable_anchor` in local_translate.py,
so this script's definition of "broken" can never silently drift from the
shipped fix's, but also survives that module changing shape later without
this one-off breaking:

  - Pattern 1 (broken link syntax): `re.compile(r"\\]\\s+\\(")` -- the exact
    substitution regex `_repair_inline_links` runs unconditionally over the
    WHOLE translated text, so "affected" here means "at least one match of
    this pattern anywhere in title/summary/body", not "inside something we
    independently verified is a real link". Per that function's own comment,
    that space is never valid markdown, so this can only ever be flagging a
    real defect.
  - Pattern 2 (untranslated anchor): source anchors are read off the
    CURRENT English title/summary/body via `_INLINE_LINK`
    (`\\[([^\\]\\n]+)\\]\\(([^)\\s]+)\\)`), filtered to multi-word anchors via
    `_looks_translatable_anchor` (single-word anchors are usually brand
    names correctly left alone), then checked for the literal marker
    `[<anchor>](` still present verbatim in the corresponding translated
    field. Same three-field pairing `_translate_article_no_lock` uses:
    English title -> translated title, English summary -> translated
    summary, English body -> translated body (per-block in the real
    pipeline, but the substring check does not care about block
    boundaries).

Usage (from the `workers` directory, or with an equivalent PYTHONPATH so
`app.core.cassandra` and `algorand_shared` both resolve -- same convention as
every other script in this directory):

    PYTHONPATH=.:../shared python workers/scratch/scan_translation_link_corruption.py

Writes two JSON reports next to this script's cwd for the pattern-2 followup
and for a pattern-1 spot-check:
    translation_pattern1_affected.json
    translation_pattern2_affected.json
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime

from algorand_shared.article_statements import ArticlesStmts

from app.core.cassandra import get_cassandra_session

KS = "algorand_platform"

# Same range backfill_articles_by_tag.py uses: article-table consolidation
# landed 2026-08-24, nothing published predates this platform's real history.
_YEARS = range(2024, datetime.now(tz=UTC).year + 1)

_SPACE_BEFORE_PAREN = re.compile(r"\]\s+\(")
_INLINE_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")


def _looks_translatable_anchor(anchor: str) -> bool:
    return len(anchor.split()) >= 2


def _pattern1_hits(text: str) -> int:
    return len(_SPACE_BEFORE_PAREN.findall(text or ""))


def _pattern2_hits(source_text: str, translated_text: str) -> list[str]:
    hits = []
    for anchor, _url in _INLINE_LINK.findall(source_text or ""):
        if not _looks_translatable_anchor(anchor):
            continue
        marker = f"[{anchor}]("
        if marker in (translated_text or ""):
            hits.append(anchor)
    return hits


def main() -> None:
    session = get_cassandra_session()
    session.default_fetch_size = 2000

    articles_scanned = 0
    articles_with_translations = 0
    pairs_scanned = 0

    p1_pairs: dict[tuple[str, str], int] = {}
    p1_by_lang: dict[str, int] = {}
    p1_by_field: dict[str, int] = {"title": 0, "summary": 0, "body": 0}
    p1_samples: list[dict] = []

    p2_pairs: dict[tuple[str, str], list[dict]] = {}
    p2_by_lang: dict[str, int] = {}

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
            articles_with_translations += 1

            english_fields = {
                "title": full.title or "",
                "summary": full.summary or "",
                "body": full.body or "",
            }

            for lang, blob in translations.items():
                pairs_scanned += 1
                try:
                    decoded = json.loads(blob)
                except (ValueError, TypeError):
                    print(
                        f"  WARN: {full.article_id} lang={lang} translation blob is not "
                        "valid JSON, skipping",
                        flush=True,
                    )
                    continue

                # --- pattern 1 ---
                total_p1 = 0
                for field in ("title", "summary", "body"):
                    n = _pattern1_hits(decoded.get(field, ""))
                    if n:
                        total_p1 += n
                        p1_by_field[field] += n
                if total_p1:
                    key = (str(full.article_id), lang)
                    p1_pairs[key] = total_p1
                    p1_by_lang[lang] = p1_by_lang.get(lang, 0) + total_p1
                    if len(p1_samples) < 15:
                        for field in ("title", "summary", "body"):
                            text = decoded.get(field, "")
                            m = _SPACE_BEFORE_PAREN.search(text)
                            if m:
                                start = max(0, m.start() - 40)
                                end = min(len(text), m.end() + 40)
                                p1_samples.append(
                                    {
                                        "article_id": str(full.article_id),
                                        "title": full.title,
                                        "lang": lang,
                                        "field": field,
                                        "before": text[start:end],
                                        "after": _SPACE_BEFORE_PAREN.sub(
                                            "](", text[start:end]
                                        ),
                                    }
                                )
                                break

                # --- pattern 2 ---
                anchors_hit = []
                for field in ("title", "summary", "body"):
                    hits = _pattern2_hits(english_fields[field], decoded.get(field, ""))
                    for a in hits:
                        anchors_hit.append({"field": field, "anchor": a})
                if anchors_hit:
                    key = (str(full.article_id), lang)
                    p2_pairs[key] = anchors_hit
                    p2_by_lang[lang] = p2_by_lang.get(lang, 0) + len(anchors_hit)

    print(f"articles scanned (status='published'): {articles_scanned}", flush=True)
    print(f"  ...with at least one translation: {articles_with_translations}", flush=True)
    print(f"article x language pairs scanned: {pairs_scanned}", flush=True)
    print(flush=True)
    print(
        f"PATTERN 1 (broken '] (' link syntax): {len(p1_pairs)} article x lang pairs affected, "
        f"{sum(p1_pairs.values())} total occurrences",
        flush=True,
    )
    print(f"  by field: {p1_by_field}", flush=True)
    print(f"  by lang: {p1_by_lang}", flush=True)
    print(flush=True)
    print(
        f"PATTERN 2 (untranslated multi-word anchor): {len(p2_pairs)} article x lang pairs "
        f"affected, {sum(len(v) for v in p2_pairs.values())} total anchor instances",
        flush=True,
    )
    print(f"  by lang: {p2_by_lang}", flush=True)

    with open("translation_pattern1_affected.json", "w") as f:
        json.dump(
            {
                "affected_pairs": [
                    {"article_id": aid, "lang": lang, "occurrences": n}
                    for (aid, lang), n in sorted(p1_pairs.items())
                ],
                "samples": p1_samples,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    with open("translation_pattern2_affected.json", "w") as f:
        json.dump(
            [
                {"article_id": aid, "lang": lang, "anchors": hits}
                for (aid, lang), hits in sorted(p2_pairs.items())
            ],
            f,
            ensure_ascii=False,
            indent=2,
        )

    print("\nSCAN_DONE", flush=True)


if __name__ == "__main__":
    main()
