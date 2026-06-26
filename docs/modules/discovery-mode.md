# Discovery mode

Chain tail extracts crawlable URLs from transaction notes, rekey hints, and domain mentions, then enqueues them on the URL queue.

## Flow

1. `process_new_rounds` loads transactions per round.
2. `match_services()` calls `enqueue_discovered_urls()` when `DISCOVERY_MODE_ENABLED=1`.
3. URLs are enqueued with `source=chain` and `priority=50`.
4. `drain_url_queue` scrapes and runs the discovery store pipeline.

## URL extraction

Implemented in `workers/app/modules/chain_tail/discovery.py`:

- HTTP(S) in note fields
- Known rekey-to service-proxy hints
- Domain-like tokens in txn JSON

## Configuration

| Variable | Default |
|----------|---------|
| `DISCOVERY_MODE_ENABLED` | `1` |

See also [url-queue.md](url-queue.md) and [publish-classifier.md](publish-classifier.md).
