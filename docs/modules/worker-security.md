# Brick: Worker security

## Goal

Flag suspicious transaction payloads before user-facing flows (baseline).

## Status

`partial`

## Features (should do)

- Celery task `inspect_transaction_group` accepting base64 payload
- Best-effort decode; keyword checks for `rekey`, `close`, `clawback`
- Return `is_suspicious` and `flagged_rules` list
- Queue `security` for isolation from scrape/pipeline

## Good to have

- Structured logging when suspicious — **done** (`logger.warning` with `flagged_rules` extra)
- Unit tests for known benign vs flagged strings — **done** (`workers/tests/test_security_tasks.py`)

## Future improvements

- Full msgpack / SDK decode of txn and txn group
- Risk score 0–100 with explainable rules
- Integration: block suggestion submit if wallet about to sign risky group
- Human-readable report for support team
- Update rules from ecosystem incident feed

## Standards & RFCs

OWASP XSS; Algorand msgpack txn encoding (future full decode). [standards-and-rfcs.md](../architecture/standards-and-rfcs.md#worker-security).

## Depends on

- `celery-redis-queues`

## Code map

- `workers/app/modules/security/tasks/security_tasks.py`
