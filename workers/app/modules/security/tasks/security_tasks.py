"""Best-effort keyword inspection of chain transaction payloads for risky operations."""

from __future__ import annotations

import base64
import json
import logging

from app.celery_app import celery_app

logger = logging.getLogger(__name__)


def inspect_payload(group_b64: str) -> dict[str, object]:
    """Best-effort keyword inspection; full msgpack decode is a future brick."""
    flagged_rules: list[str] = []
    try:
        payload = base64.b64decode(group_b64)
        text = payload.decode("utf-8", errors="ignore")
        lowered = text.lower()
        if "rekey" in lowered:
            flagged_rules.append("contains_rekey")
        if "close" in lowered:
            flagged_rules.append("contains_close")
        if "clawback" in lowered:
            flagged_rules.append("contains_clawback")
    except Exception as exc:
        flagged_rules.append(f"decode_error:{exc}")

    result = {
        "is_suspicious": bool(flagged_rules),
        "flagged_rules": flagged_rules,
        "raw_summary": json.dumps({"bytes": len(group_b64)}, separators=(",", ":")),
    }
    if flagged_rules:
        logger.warning(
            "suspicious transaction group flagged",
            extra={
                "flagged_rules": flagged_rules,
                "payload_bytes": len(group_b64),
            },
        )
    return result


@celery_app.task(name="app.tasks.security.inspect_transaction_group")
def inspect_transaction_group(group_b64: str) -> dict[str, object]:
    """Celery task entrypoint wrapping inspect_payload."""
    return inspect_payload(group_b64)
