"""Celery tasks that poll new rounds and dispatch chain-triggered discovery/matching."""

from __future__ import annotations

from app.celery_app import celery_app
from app.core.config import CHAIN_TAIL_MAX_ROUNDS_PER_RUN
from app.core.redis_client import get_redis
from app.modules.chain_tail.chain_reader import (
    get_algod_head_round,
    get_conduit_head_round,
    list_transactions_for_round,
)
from app.modules.chain_tail.matching import match_services
from app.modules.chain_tail.registry_cache import clear_registry_cache, load_enabled_services
from app.modules.newspaper.tasks.publish_tasks import publish_from_chain_event
from app.modules.scraper.crawler_registry import chain_crawl_disabled_reason

CHAIN_TAIL_LAST_PROCESSED_KEY = "chain_tail:last_processed_round"


@celery_app.task(name="app.tasks.chain_tail.process_new_rounds")
def process_new_rounds() -> dict[str, int | str | bool]:
    """Tail new Conduit-indexed rounds and enqueue newspaper work for registry matches."""
    chain_off = chain_crawl_disabled_reason()
    if chain_off:
        return {
            "status": "skipped",
            "reason": chain_off,
            "rounds_processed": 0,
            "matches_enqueued": 0,
        }

    client = get_redis()
    last_processed = int(client.get(CHAIN_TAIL_LAST_PROCESSED_KEY) or 0)

    head = get_conduit_head_round()
    if head is None:
        head = get_algod_head_round()

    if head <= last_processed:
        return {
            "status": "ok",
            "head": head,
            "last_processed": last_processed,
            "rounds_processed": 0,
            "matches_enqueued": 0,
        }

    clear_registry_cache()
    registry = load_enabled_services()

    start = last_processed + 1
    end = min(head, last_processed + CHAIN_TAIL_MAX_ROUNDS_PER_RUN)
    matches_enqueued = 0
    rounds_processed = 0

    for round_num in range(start, end + 1):
        rounds_processed += 1
        transactions = list_transactions_for_round(round_num)
        for tx in transactions:
            for service in match_services(tx, registry):
                if not service.scrape_url:
                    continue
                publish_from_chain_event.delay(
                    service_id=service.service_id,
                    display_name=service.display_name,
                    scrape_url=service.scrape_url,
                    match_kind=service.match_kind,
                    match_value=service.match_value,
                    txid=tx.txid,
                    round_num=round_num,
                )
                matches_enqueued += 1

    client.set(CHAIN_TAIL_LAST_PROCESSED_KEY, str(end))
    return {
        "status": "ok",
        "head": head,
        "last_processed": end,
        "rounds_processed": rounds_processed,
        "matches_enqueued": matches_enqueued,
    }


@celery_app.task(name="app.tasks.chain_tail.poll_new_blocks")
def poll_new_blocks() -> dict[str, int | str]:
    """Backward-compatible entry point — runs full round processing."""
    result = process_new_rounds()
    return {
        "status": str(result.get("status", "ok")),
        "last_round": int(result.get("last_processed", 0)),
        "advanced": int(result.get("rounds_processed", 0)) > 0,
    }
