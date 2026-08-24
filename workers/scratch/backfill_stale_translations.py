"""Retroactive backfill for stale translations: an admin content correction
made BEFORE today's update_article fix left every translation exactly as it
was before the correction. Finds every article with (a) at least one
article_versions entry whose editor is an admin edit (not "system" or
"recompose") and (b) translations currently stored, then clears + re-enqueues
them via the modern batch task.

This does not (and cannot) tell whether a given admin edit actually changed
enough to invalidate the translations -- it treats every admin-edited article
with translations as a candidate, matching update_article's own new
content_changed check being unavailable retroactively. Erring toward
re-translating a few articles that didn't strictly need it, rather than
leaving any genuinely stale one uncorrected.
"""

import json

from app.celery_app import celery_app
from app.core.article_translation_langs import ARTICLE_TRANSLATION_LANGS
from app.core.cassandra import get_cassandra_session
from app.core.statements import ArticleStmts, ArticleVersionStmts


def main() -> None:
    session = get_cassandra_session()
    rows = list(
        session.execute(
            "SELECT article_id, title, translations FROM algorand_platform.articles_by_id"
        )
    )

    candidates = []
    for r in rows:
        if not r.translations:
            continue
        versions = list(session.execute(ArticleVersionStmts.LIST, (r.article_id, 50)))
        has_admin_edit = any(
            (v.editor or "").lower().startswith("admin") for v in versions
        )
        if has_admin_edit:
            candidates.append(r)

    print(f"scanned {len(rows)} articles, found {len(candidates)} admin-edited with translations", flush=True)
    for r in candidates:
        print(f"  {r.article_id} | {r.title}", flush=True)

    print("\n--- clearing + re-enqueueing ---", flush=True)
    fixed = []
    for r in candidates:
        session.execute(ArticleStmts.CLEAR_TRANSLATIONS, (r.article_id,))
        task = celery_app.send_task(
            "app.tasks.newspaper.translate_article_batch",
            args=[str(r.article_id), list(ARTICLE_TRANSLATION_LANGS)],
        )
        fixed.append({"article_id": str(r.article_id), "title": r.title, "task_id": task.id})
        print(f"  cleared + enqueued {r.article_id} -> {task.id}", flush=True)

    with open("/home/guillaume/stale_translation_backfill.json", "w") as f:
        json.dump(fixed, f, ensure_ascii=False, indent=2)

    print("\nBACKFILL_DONE", flush=True)


if __name__ == "__main__":
    main()
