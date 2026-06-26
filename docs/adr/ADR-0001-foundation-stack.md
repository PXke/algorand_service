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
