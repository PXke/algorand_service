# ADR-0001: Foundation Stack

## Status
Accepted

## Context
We need a fast Python backend and async processing for scraping and AI-generation tasks.

## Decision
Use Robyn for API and Celery for workers. Persist primary records in Cassandra, cache and queue with Redis, and search with Typesense.

## Consequences
- Good concurrency and clear separation between API and long-running jobs.
- Requires operational discipline for Celery queues and Cassandra query-model design.

## Update

The API was later migrated off Robyn onto **Falcon + gunicorn** (free-threaded
Python). The rest of this decision — Celery for workers, Cassandra as the
primary store, Redis for cache/queue, Typesense for search — is unchanged.
This ADR's historical Decision/Consequences above are left as written; see
`backend/` for the current framework.
