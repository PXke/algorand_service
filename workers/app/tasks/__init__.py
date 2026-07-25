"""Celery task registry package."""

from app.tasks import chain_tail, newspaper, pipeline, scrape, search, security

__all__ = ["chain_tail", "newspaper", "pipeline", "scrape", "search", "security"]
