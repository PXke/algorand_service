"""Archive refresh: queue recompose_published tasks for published articles.

Each task re-composes the article (Large writer + headline enforcement) into an
UNLISTED draft and holds it in the admin review queue. The live article does
not change until the review is APPROVED, at which point the draft's content is
swapped onto the same article_id (URL and published_at survive, updated_at is
stamped, translations re-run). Rejecting a review leaves the live article
exactly as it was.

Composes hold the global compose lock, so tasks run one at a time — queue a
small batch and approve/reject reviews as they arrive.

Usage (on a host with the workers env):
    cd workers && python -m scripts.recompose_archive <article_id> [...]
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    from app.celery_app import celery_app

    for article_id in argv:
        result = celery_app.send_task(
            "app.tasks.newspaper.recompose_published",
            args=[article_id],
            queue="pipeline",
        )
        print(f"queued recompose_published({article_id}) -> task {result.id}")
    print(
        f"\n{len(argv)} task(s) queued. Drafts will land in the admin review "
        "queue; approve to swap content in place, reject to keep the original."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
