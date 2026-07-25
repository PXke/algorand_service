"""Liveness/readiness checks for Redis, Cassandra, Typesense, Conduit, and Celery."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.config import settings


@dataclass
class CheckResult:
    """One dependency's liveness/readiness check outcome."""
    name: str
    ok: bool
    detail: str = ""


def check_redis() -> CheckResult:
    """Ping Redis and report whether it responded."""
    try:
        import redis

        client = redis.from_url(settings.redis_url, socket_connect_timeout=2)
        client.ping()
        return CheckResult("redis", True)
    except Exception as exc:
        return CheckResult("redis", False, str(exc))


def check_cassandra() -> CheckResult:
    """Run a trivial query against Cassandra and report whether it succeeded."""
    try:
        from app.core.cassandra import get_cassandra_session

        session = get_cassandra_session()
        session.execute("SELECT now() FROM system.local")
        return CheckResult("cassandra", True)
    except Exception as exc:
        return CheckResult("cassandra", False, str(exc))


def check_typesense() -> CheckResult:
    """Check Typesense health, treating an unconfigured client as healthy."""
    try:
        from app.core.typesense_client import get_typesense_client

        client = get_typesense_client()
        if client is None:
            return CheckResult("typesense", True, "not_configured")
        client.operations.is_healthy()
        return CheckResult("typesense", True)
    except Exception as exc:
        return CheckResult("typesense", False, str(exc))


def check_conduit_index() -> CheckResult:
    """Report the latest indexed chain round, failing if none has been ingested yet."""
    try:
        from app.modules.chain.repository import get_chain_repository

        head = get_chain_repository().get_chain_head_round()
        if head is None:
            return CheckResult("conduit_index", False, "no last_ingested_round")
        return CheckResult("conduit_index", True, f"round={head}")
    except Exception as exc:
        return CheckResult("conduit_index", False, str(exc))


def check_celery_queues() -> CheckResult:
    """Report Celery broker queue depths (scrape, pipeline, default)."""
    try:
        import redis

        client = redis.from_url(settings.celery_broker_url, socket_connect_timeout=2)
        queues = ("scrape", "pipeline", "default", "chain", "security")
        depths = {name: int(client.llen(name)) for name in queues}
        total = sum(depths.values())
        detail = ", ".join(f"{k}={v}" for k, v in depths.items())
        return CheckResult("celery_queues", True, f"total={total} {detail}")
    except Exception as exc:
        return CheckResult("celery_queues", False, str(exc))


def run_readiness_checks() -> list[CheckResult]:
    """Run all dependency checks and collect their results."""
    return [
        check_redis(),
        check_cassandra(),
        check_typesense(),
        check_conduit_index(),
        check_celery_queues(),
    ]
