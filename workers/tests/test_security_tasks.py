"""Keyword inspection flags rekey/close/clawback in a transaction payload."""

from __future__ import annotations

import base64

from app.modules.security.tasks.security_tasks import inspect_payload


def _encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_benign_payload_not_flagged() -> None:
    """A benign payload is not flagged as suspicious."""
    result = inspect_payload(_encode("pay 1000 microalgos to treasury"))
    assert result["is_suspicious"] is False
    assert result["flagged_rules"] == []


def test_rekey_payload_flagged() -> None:
    """A payload mentioning rekey is flagged with the contains_rekey rule."""
    result = inspect_payload(_encode("txn includes rekey to attacker address"))
    assert result["is_suspicious"] is True
    assert "contains_rekey" in result["flagged_rules"]


def test_close_and_clawback_flagged_together() -> None:
    """A payload mentioning both close and clawback is flagged with both rules."""
    result = inspect_payload(_encode("close remainder; clawback asset"))
    assert result["is_suspicious"] is True
    assert "contains_close" in result["flagged_rules"]
    assert "contains_clawback" in result["flagged_rules"]


def test_invalid_base64_reports_decode_error_rule() -> None:
    """Invalid base64 input is flagged with a decode_error rule rather than raising."""
    result = inspect_payload("!!!not-base64!!!")
    assert result["is_suspicious"] is True
    assert any(rule.startswith("decode_error:") for rule in result["flagged_rules"])
