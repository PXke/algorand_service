from __future__ import annotations

from app.celery_app import celery_app
from app.core.config import URL_QUEUE_ENABLED
from app.modules.crawler.url_queue import dequeue_url, pending_url_count
from app.modules.scraper.crawlers.web_crawler import WebCrawlerDriver


@celery_app.task(name="app.tasks.crawler.drain_url_queue")
def drain_url_queue(*, max_items: int = 5) -> dict[str, object]:
    """Dequeue URLs and run the web discovery pipeline."""
    if not URL_QUEUE_ENABLED:
        return {"status": "skipped", "reason": "url_queue_disabled", "processed": 0}

    driver = WebCrawlerDriver()
    results: list[dict[str, object]] = []
    processed = 0
    for _ in range(max_items):
        item = dequeue_url()
        if item is None:
            break
        outcome = driver.scrape_from_queue_item(item)
        results.append(outcome)
        processed += 1

    return {
        "status": "ok",
        "processed": processed,
        "remaining": pending_url_count(),
        "results": results,
    }


@celery_app.task(name="app.tasks.crawler.classify_pending_domains")
def classify_pending_domains(
    *, limit: int = 40, dry_run: bool = True, auto_reject: bool = False
) -> dict[str, object]:
    """Content-based domain relevance: crawl each pending domain's landing page,
    classify the REAL page text (not the <head> preview that wrongly blocked
    pact.fi etc.), store the score, and OPTIONALLY auto-reject only the clearly
    off-topic ones. Safe by default: auto_reject=False so the scores can be
    validated first; protected domains are never auto-rejected."""
    from app.core.cassandra import get_cassandra_session
    from app.core.config import FRONTIER_CONTENT_REJECT_SCORE
    from app.core.statements import DomainTrackingStmts
    from app.modules.crawler.domain_tracker import is_protected_domain, update_domain_status
    from app.modules.scraper.crawlers.web_crawler import WebCrawlerDriver
    from app.modules.search.classifier.score import score_page

    session = get_cassandra_session()
    rows = session.execute(DomainTrackingStmts.LIST, (limit * 30,))
    pending = []
    for r in rows:
        meta = dict(r.metadata or {})
        status = r.frontier_status or meta.get("frontier_status")
        if status == "pending":
            pending.append((r.domain, meta))
        if len(pending) >= limit:
            break

    driver = WebCrawlerDriver()
    scored = rejected = errors = unreadable = 0
    samples: list[dict] = []
    for domain, meta in pending:
        url = meta.get("pending_url") or f"https://{domain}"
        try:
            # HTTP first, transparent Playwright fallback for thin/SPA pages, so a
            # JS dApp gets its REAL rendered text instead of mis-scoring 0.
            text = (driver.scrape_with_fallback(url, domain).text or "")[:20000]
        except Exception:
            errors += 1
            continue
        # Too little text to judge (dead/blocked/SPA-disabled) — don't pretend
        # it's off-topic; leave it for a human.
        if len(text.strip()) < 100:
            unreadable += 1
            continue
        score = round(float(score_page(url=url, text=text).score), 3)
        scored += 1
        will_reject = (
            auto_reject
            and score < FRONTIER_CONTENT_REJECT_SCORE
            and not is_protected_domain(domain)
        )
        if len(samples) < 40:
            samples.append({"domain": domain, "score": score, "reject": will_reject})
        if not dry_run:
            new_meta = {**meta, "content_relevance": f"{score:.3f}"}
            if will_reject:
                new_meta["frontier_status"] = "dead_end"
                new_meta["auto_rejected"] = "content_off_topic"
                update_domain_status(
                    domain, relevance_score=score, is_relevant=False,
                    online=True, metadata=new_meta, frontier_status_override="dead_end",
                )
                rejected += 1
            else:
                new_meta["frontier_status"] = "pending"
                session.execute(
                    DomainTrackingStmts.UPDATE_METADATA,
                    (new_meta, domain),
                )
    samples.sort(key=lambda s: s["score"])
    return {
        "status": "ok",
        "dry_run": dry_run,
        "auto_reject": auto_reject,
        "scored": scored,
        "rejected": rejected,
        "errors": errors,
        "unreadable": unreadable,
        "reject_threshold": FRONTIER_CONTENT_REJECT_SCORE,
        "samples_low_to_high": samples,
    }


@celery_app.task(name="app.tasks.crawler.retrain_publish_classifier")
def retrain_publish_classifier_task() -> dict[str, object]:
    # The sklearn "learned grader" (grader_model.train_grader) used to run here
    # too, but its output has no live reader — the gatekeeper quality head
    # replaces it (see app.tasks.gatekeeper.train_quality_head, queued
    # separately by admin_retrain since it's a much heavier CPU job).
    from app.modules.ai.publish_classifier import retrain_publish_classifier

    classifier = retrain_publish_classifier()
    return {"classifier": classifier}


@celery_app.task(name="app.tasks.crawler.sync_ecosystem_directories")
def sync_ecosystem_directories_task() -> dict[str, object]:
    """Daily beat: ingest curated ecosystem directories (awesome-algorand etc.)
    and case-study indexes (algorand.co/case-studies), approving + monitoring
    listed/subject domains — the discovery path for chain-silent services and
    institutional users whose own sites score 0 relevance. See ecosystem_sync."""
    from app.core.config import ECOSYSTEM_SYNC_ENABLED
    from app.modules.crawler.ecosystem_sync import (
        sync_ecosystem_case_studies,
        sync_ecosystem_directories,
    )

    if not ECOSYSTEM_SYNC_ENABLED:
        return {"status": "skipped", "reason": "ecosystem_sync_disabled"}
    return {
        "directories": sync_ecosystem_directories(),
        "case_studies": sync_ecosystem_case_studies(),
    }
