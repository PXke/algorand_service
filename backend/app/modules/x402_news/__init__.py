"""x402 News Engine pay-per-call: the newspaper's live articles behind a micro-price.

Roadmap item 1 in CLAUDE.md section 9.1. Three routes under /api/v1/x402/news:
a free, rate-limited headline list, a paid full-article read, and a paid
Typesense-backed search. Every paid read goes through the shared
require_paid_request gate and the shared settlement ledger.

This module owns NO storage. It reads articles through the news module's own
service (modules/news, the same store the public site reads) and searches
through the search module's own service (modules/search). It is registered
only when the news store is durable (settings.news_store != "memory"), the
same per-product gate every other x402 product has, because a paid read
served from a per-process memory store could not be honored reliably across
gunicorn workers.
"""
