"""Task registry import: security-inspection Celery tasks."""

from app.modules.security.tasks.security_tasks import inspect_transaction_group

__all__ = ["inspect_transaction_group"]
