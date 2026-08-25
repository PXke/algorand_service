"""One-time, read-only snapshot of a real article's compose_scrape_article inputs.

Freezes exactly what a real archive-refresh recompose would compose from
(the same _recompose_published_source_text prod uses -- a live re-scrape of
the source page, aggregated into the service's other already-crawled pages)
into a local JSON fixture, so a later benchmark loop can compose the SAME
material across every provider/run without re-scraping each time (removing
"the site changed between runs" as a confound) and without touching prod
again after this one read.

This is the ONLY step in the multi-provider benchmark workflow that touches
anything live -- run it once, by hand:

    cd workers && .venv/bin/python scripts/snapshot_compose_input.py <article_id> [output_path]

Everything downstream (compose_runner.compose(), the benchmark script) loads
the frozen file and never scrapes/hits Cassandra for input again -- though
compose() itself still exercises the writer's own live research tool calls,
since that's the actual thing being benchmarked.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def snapshot(article_id: str) -> dict:
    """Fetch the real article + do the same fresh-scrape/service-context aggregation prod's recompose path does, return a plain dict matching compose_runner.ArticleInput's fields."""
    from app.modules.newspaper.article_store import get_article
    from app.modules.newspaper.tasks.publish_tasks import _recompose_published_source_text

    existing = get_article(article_id)
    if existing is None:
        raise SystemExit(f"no article found for id {article_id!r}")

    service_id = existing.service_id
    source_url = existing.source_url
    page_text, page_title, _scraped_og = _recompose_published_source_text(
        existing, service_id, source_url
    )

    return {
        "service_name": service_id or source_url or "archive",
        "source_url": source_url or f"article:{article_id}",
        "page_title": page_title,
        "page_text": page_text,
        "txid": f"recompose-{article_id[:12]}",
        "round_num": 0,
        "diff": None,
        "is_first_snapshot": True,
        "enrichment_block": "",
        "source_links": None,
        "publish_topic": "",
        "first_coverage": False,
        "prior_coverage_block": "",
        # Not part of ArticleInput -- kept in the fixture purely as a
        # provenance record of what article/moment this snapshot came from.
        "_snapshot_source_article_id": article_id,
        "_snapshot_source_title": existing.title,
    }


def main() -> None:
    """CLI entry point: snapshot_compose_input.py <article_id> [output_path]."""
    if len(sys.argv) < 2:
        raise SystemExit("usage: snapshot_compose_input.py <article_id> [output_path]")
    article_id = sys.argv[1]
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("scratch/lumirogue_snapshot.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    data = snapshot(article_id)
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {output_path} ({len(data['page_text'])} chars of page_text)")  # noqa: T201


if __name__ == "__main__":
    main()
